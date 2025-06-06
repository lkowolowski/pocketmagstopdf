#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "docopt",
#   "Pillow",
#   "reportlab",
#   "requests",
# ]
# ///
#
# Dedicated to the public domain where possible.
# See: https://creativecommons.org/publicdomain/zero/1.0/
"""
Download pocketmags magazines in PDF format from the HTML5 reader.

Usage:
    pocketmagstopdf.py (-h | --help)
    pocketmagstopdf.py [options] <pdf> <url>

Options:

    -h, --help                  Print brief usage summary.

    --quality=QUALITY           Set magazine download quality.
                                Choose from extralow, low, mid, high, extrahigh
                                or original. (Optional)
                                [default: mid]

    --dpi=DPI                   Set image resolution in dots per inch.
                                (Optional) Not used with '--quality=original'.
                                [default: 150]

    --title=TITLE               Set magazine title in the PDF metadata. (Optional)
                                Not used with '--quality=original'.
                                default value is the filename with;
                                    - underscores replaced with spaces
                                    - the file extension removed

    --range-from=PAGE-FROM      Define a portion of the magazine to download,
                                starting from this page number. (Optional)
                                Downloads from the beginning of the magazine -
                                page 1 - if absent.
                                [default: 1]

    --range-to=PAGE-TO          Define a portion of the magazine to download,
                                ending on this page number. (Optional) Downloads
                                to the end of the magazine if absent.
                                [default: 999]

    --delay=DELAY               Set the time in seconds to wait between
                                downloading each page of the magazine.
                                (Optional) There is no delay if absent. The
                                value of the delay may be integer or decimal.
                                Used both whenenever probing for the last valid
                                page number of the magazine and between
                                downloading each individual page for all quality
                                settings except 'original'.
                                [default: 0]

    --save-images               Save the downloaded JPEG images of the magazine
                                pages to a subdirectory with the same name as
                                the magazine in addition to generating the PDF
                                of the magazine. Not used with
                                '--quality=original'.
                                [default: False]

    --image-subdir-prefix=PFX   If --save-images=yes then prefix name of the
                                subdirectory the images are saved to with this
                                string. Blank by default. (Optional) Not used
                                with '--quality=original'.
                                [default: ]

    --image-subdir-suffix=SFX   If --save-images=yes then suffix name of the
                                subdirectory the images are saved to with this
                                string. Blank by default. (Optional)
                                Not used with '--quality=original'.
                                [default: ]

    --uuid=UUID                 Specifies the User UUID to use to download the
                                PDF when '--quality=original' is used and
                                --uuid-randomise is not used. Read the 'Notes'
                                section below for details of how to find it.
                                (Optional/Required) Only used with
                                '--quality=original'. [default: None]

    --uuid-randomise            Uses a random UUID to download the PDF when
                                '--quality=original' is specified. (Optional)
                                [default: False]

    --uuid-hide                 Hides the User UUID watermark on each page of
                                the PDF by making it transparent. This option is
                                overridden by '--uuid-destroy'. Only used with
                                '--quality=original' as watermark not present on
                                lower quality downloads.
                                [default: False]

    --uuid-destroy              Completely wipes the User UUID watermark from
                                each page of the PDF. (Experimental) This option
                                overrides by '--uuid-hide'. Only used with
                                '--quality=original' as watermark not present on
                                lower quality downloads.
                                [default: False]

    --timestamp-change          Alters the timestamp within the downloaded PDF.
                                Only used with '--quality=original'.
                                [default: False]

    --user-agent=USER-AGENT     Sometimes requests will fail with 403 Forbidden
                                responses if no User-Agent header is specified
                                [default: Mozilla/5.0.0]

    --quiet                     Suppress printing of all output except warning
                                and error messages.
                                [default: False]

    --debug                     Print extra output to aid debugging of the
                                program. Setting both '--quiet' and '--debug' is
                                contradictory If this happens, a warning is
                                issued and the debug setting overrides the quiet
                                setting.
                                [default: False]

    <pdf>                       Save output to this file. (Required)
    <url>                       A URL to one image from the magazine. (Required)

Notes:

    PLEASE USE THIS SCRIPT RESPONSIBLY. THE MAGAZINE PUBLISHING INDUSTRY RELIES
    HEAVILY ON INCOME FROM SALES WITH VERY SLIM PROFIT MARGINS.

    URLs for pocketmags images and User UUIDs can be found by using the HTML 5
    reader and right-clicking on a page and selecting "inspect element". Look
    for URLs of the form:

        https://files.magazineclonercdn.com/mcmags/<uuid1>/<uuid2>/extralow/<num>.jpg

    where <uuid{1,2}> are strings of letters and numbers with dashes separating
    them and <num> is some 4-digit number.

    The User UUID required for downloading the magazine when
    '--quality=original' can be found by searching the HTML for the text
    "userGuid:" and copying the hexadecimal
    value that follows it without the surrounding single quote characters.

"""

import binascii
import os.path
import random
import re
import uuid
import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO
from time import sleep
from urllib.error import HTTPError
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen, Request
import logging

import PIL
import docopt
import requests as requests
from PIL import Image
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

LOGGER = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)


# The pattern of the URL path for a magazine
URL_PATH_PATTERN = re.compile(
    r'(?P<prefix>^/mcmags/(?P<bucket_uuid>[a-f0-9\-]+)/(?P<magazine_uuid>[a-f0-9\-]+))/(?P<imagequality>(extralow|low|mid|high|extrahigh))/[0-9]{4}.(bin|jpg)')

# GitHub issue #7 reports extrahigh/0000.bin as a new quality level: https://github.com/RichardJRL/pocketmagstopdf/issues/7
# But it does not exist for the (older) example magazine used here:

# Older magazine: Example URLs sampled 8 July 2022, deliberately using an advert from a magazine:
# https://files.magazineclonercdn.com/mcmags/f3786b15-4b19-456e-9b58-2af137a35bcd/ba9c5bcb-cf96-4215-a2f5-841ddb4a119c/extralow/0046.jpg
# https://files.magazineclonercdn.com/mcmags/f3786b15-4b19-456e-9b58-2af137a35bcd/ba9c5bcb-cf96-4215-a2f5-841ddb4a119c/low/0046.jpg
# https://files.magazineclonercdn.com/mcmags/f3786b15-4b19-456e-9b58-2af137a35bcd/ba9c5bcb-cf96-4215-a2f5-841ddb4a119c/mid/0046.jpg
# https://files.magazineclonercdn.com/mcmags/f3786b15-4b19-456e-9b58-2af137a35bcd/ba9c5bcb-cf96-4215-a2f5-841ddb4a119c/high/0046.bin
# Image Sizes (based upon the above URLs for page 46)
# extralow: 340  x 480
# low:      362  x 512
# mid:      905  x 1280
# high:     1447 x 2048

# Newer magazine: Example URLs sampled 17 March 2023, deliberately using an advert from a magazine:
# extralow
# https://files.magazineclonercdn.com/mcmags/6c4f9d70-3791-4cdb-8649-deb6d4b8bd29/c4d2c289-48ae-4bad-9a20-7eb0de6debf9/extralow/0084.jpg
# https://files.magazineclonercdn.com/mcmags/6c4f9d70-3791-4cdb-8649-deb6d4b8bd29/c4d2c289-48ae-4bad-9a20-7eb0de6debf9/extralow/0084.bin
# low
# https://files.magazineclonercdn.com/mcmags/6c4f9d70-3791-4cdb-8649-deb6d4b8bd29/c4d2c289-48ae-4bad-9a20-7eb0de6debf9/low/0084.jpg
# https://files.magazineclonercdn.com/mcmags/6c4f9d70-3791-4cdb-8649-deb6d4b8bd29/c4d2c289-48ae-4bad-9a20-7eb0de6debf9/low/0084.bin
# mid
# https://files.magazineclonercdn.com/mcmags/6c4f9d70-3791-4cdb-8649-deb6d4b8bd29/c4d2c289-48ae-4bad-9a20-7eb0de6debf9/mid/0084.jpg
# https://files.magazineclonercdn.com/mcmags/6c4f9d70-3791-4cdb-8649-deb6d4b8bd29/c4d2c289-48ae-4bad-9a20-7eb0de6debf9/mid/0084.bin
# high
# https://files.magazineclonercdn.com/mcmags/6c4f9d70-3791-4cdb-8649-deb6d4b8bd29/c4d2c289-48ae-4bad-9a20-7eb0de6debf9/high/0084.bin
# extrahigh
# https://files.magazineclonercdn.com/mcmags/6c4f9d70-3791-4cdb-8649-deb6d4b8bd29/c4d2c289-48ae-4bad-9a20-7eb0de6debf9/extrahigh/0084.bin
# Image Sizes (based upon the above URLs for page 84)
# extralow: 340  x 480 (same for .bin and .jpg)
# low:      362  x 512 (same for .bin and .jpg)
# mid:      905  x 1280 (same for .bin and .jpg)
# high:     1448 x 2048
# extrahigh:2171 x 3072

QUALITY_PATTERN = re.compile("(extralow|low|mid|high|extrahigh|original)")

# Notes on the "high" image size with the "bin" file extension.
# Running the Linux "file" command gives the output:
# Atari DEGAS Elite bitmap 320 x 200 x 16, color palette ffe0 0010 4a46 4946 0001 ...

# But it seems unlikely that what is expected to be the highest quality image is actually an old Atari bitmap with
# 320 x 200 resolution! Using a hex editor (Okteta) to examine the bin file immediately revealed the string "JFIF"
# near the beginning of the file. This is one of the things expected in the header of a "jpg" file but the first two
# bytes of the file were 0x0000 when for a "jpg" they should be 0xFFD8. The rest of the beginning of the file also
# looked like what would be expected in a "jpg" header section. Editing the first two bytes of the "bin" file to
# 0xFFD8 resulted in it opening in the Gwenview image viewer and showed it to have a resolution of 1447 x 2048.

# Notes on the "extrahigh" image size with the "bin" file extension.
# Running the Linux "file" command gives the output:
# 0084extrahigh.bin: data

# Inspecting the file with a hex editor (Okteta) revealed the first bytes were:
# 00 00 46 46 F2 BE 09 00 57 45 42 50
# So the zero the first two bytes trick is being played again, but it's not a "jpg" being obfuscated this time.
# Searching Wikipedia's list of file signatures at https://en.wikipedia.org/wiki/List_of_file_signatures
# for files where the 3rd and 4th bytes of the file are "46 46" revealed a number of RIFF file format variants that
# began "52 49 46 46 ..."
# Changing the first two bytes to 52 49 resulted in the file successfully being identified by the "file" command as:
# images/0084extrahigh.webp: RIFF (little-endian) data, Web/P image, VP8 encoding, 2171x3072, Scaling: [none]x[none], YUV color, decoders should clamp
# and it loaded successfully in the Gewnview image viewer thereafter with a resolution of 2171 x 3072.
# Edited information copied from the Wikipedia page, for reference:
# 52 49 46 46 ?? ?? ?? ?? 57 45 42 50 | RIFF????WEBP | Google WebP image file, where ?? ?? ?? ?? is the file size.
# Extension should be .webp
# See also: https://developers.google.com/speed/webp/docs/riff_container#webp_file_header

# The pattern for a standard UUID, used to identify storage blobs, magazines and users
UUID_PATTERN = re.compile("^[a-z0-9]{8}-([a-z0-9]{4}-){3}[a-z0-9]{12}$")


@contextmanager
def saving(thing):
    """Context manager which ensures save() is called on thing."""
    try:
        yield thing
    finally:
        thing.save()


def main():
    opts = docopt.docopt(__doc__)
    pdf_fn, url = (opts[k] for k in ('<pdf>', '<url>'))
    url = urlparse(url)
    dpi = float(opts['--dpi'])
    quality = str(opts['--quality']).lower()
    title = str(opts['--title'])
    range_from = int(opts['--range-from'])
    range_to = int(opts['--range-to'])
    delay = float(opts['--delay'])
    save_images = bool(opts['--save-images'])
    image_subdir_prefix = str(opts['--image-subdir-prefix'])
    image_subdir_suffix = str(opts['--image-subdir-suffix'])
    user_uuid = str(opts['--uuid'])
    user_uuid_randomise = bool(opts['--uuid-randomise'])
    user_uuid_hide = bool(opts['--uuid-hide'])
    user_uuid_destroy = bool(opts['--uuid-destroy'])
    timestamp_change = bool(opts['--timestamp-change'])
    user_agent = str(opts['--user-agent'])
    quiet = bool(opts['--quiet'])
    debug = bool(opts['--debug'])

    if quiet:
        LOGGER.setLevel(level=logging.WARNING)

    # Check if both quiet output and debug output options are specified. Debug overrides quiet
    if quiet is True and debug is True:
        LOGGER.warning('Specifying both \'--quiet\' and \'--debug\' is contradictory. Debug output setting will override quiet output setting.')

    if debug:
        LOGGER.setLevel(level=logging.DEBUG)

    m = URL_PATH_PATTERN.match(url.path)
    if not m:
        raise RuntimeError('URL path does not match expected pattern')
    prefix = m.group('prefix')

    q = QUALITY_PATTERN.match(quality)
    if not q:
        raise RuntimeError(
            "--quality= argument does not match any of the expected values: extralow|low|mid|high|extrahigh|original")

    bu = UUID_PATTERN.match(m.group('bucket_uuid'))
    if not bu:
        raise RuntimeError('URL supplied does not contain a valid storage bucket UUID')
    bucket_uuid = bu.string

    mu = UUID_PATTERN.match(m.group('magazine_uuid'))
    if not mu:
        raise RuntimeError('URL supplied does not contain a valid magazine UUID')
    magazine_uuid = mu.string

    # NB: docopts gives the variable 'title' the string value of "None" not the type "None" when it is absent as a CLA
    # Hence 'if title == "None"' rather than 'if title is None'
    if title == "None":
        (title, extension) = os.path.splitext(os.path.basename(pdf_fn))
        title = title.replace('_', ' ')

    # Check range_from and range_to are both >0, exit if not
    if range_from < 1 or range_to < 1:
        raise RuntimeError(
            "Error setting the page range to download, the optional arguments --range-from= and --range-to=, if specified, must have integer values greater than 1")

    # Check range_from < range_to
    if range_from > range_to:
        raise RuntimeError("Error setting the page range to download. the value of--range-from= must be less than the value of --range-to=")

    # Assemble range text
    end_text = " to page " + str(range_to)
    if range_to == 999:
        end_text = " to the end of the magazine"
    range_text = 'Range of pages to download is page ' + str(range_from) + end_text
    # TODO: Move last-page-finding code to before the range of pages to download is printed, so the inaccurate default of 999 is never shown

    # Check delay value
    if delay < 0:
        raise RuntimeError(
            "Error setting the delay between page downloads. The value of --delay= must be not be less than zero.")

    # Warn that save_images is not compatible with 'original' quality
    if save_images == True and quality == 'original':
        raise RuntimeError("Cannot save images when quality is set to 'original'.")

    # Check that a UUID option is provided with '--quality=original'
    if quality == 'original':
        if user_uuid == 'None' and user_uuid_randomise is False:
            LOGGER.error('If \'--quality=original\' is used, EITHER --uuid=UUID OR --uuid-randomise MUST be present.')
            exit(1)
        if user_uuid != 'None':
            if not UUID_PATTERN.match(user_uuid):
                raise RuntimeError('User UUID supplied with \'--uuid=\' is not a valid UUID')
        if user_uuid_randomise == True:
            user_uuid = str(uuid.uuid4())

    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent

    LOGGER.info('URL is {}'.format(url.geturl()))
    LOGGER.info('File is {}'.format(pdf_fn))
    LOGGER.info('Storage bucket UUID is {}'.format(bucket_uuid))
    LOGGER.info('Magazine UUID is {}'.format(magazine_uuid))
    LOGGER.info('DPI is {}'.format(dpi))
    LOGGER.info('Quality is {}'.format(quality))
    LOGGER.info(range_text)
    LOGGER.info('Delay between downloading each page is {} seconds'.format(delay))
    LOGGER.info('Saving images is {}'.format(str(save_images).lower()))
    LOGGER.info('User UUID is {}'.format(user_uuid))
    LOGGER.info('Randomise User UUID is {}'.format(str(user_uuid_randomise).lower()))
    LOGGER.info('Hide User UUID is {}'.format(str(user_uuid_hide).lower()))
    LOGGER.info('Destroy User UUID is {}'.format(str(user_uuid_destroy).lower()))
    LOGGER.info('User-Agent is {}'.format(str(user_agent)))
    LOGGER.info('Quiet output is {}'.format(str(quiet).lower()))
    LOGGER.info('Quiet output is {}'.format(str(quiet).lower()))
    LOGGER.info('Debug output is {}'.format(str(debug).lower()))

    if quality != 'original':
        c = canvas.Canvas(pdf_fn)
        c.setTitle(title)
        with saving(c):

            # create directory to hold magazine images, if required
            if save_images:
                (pdf_parent_dir_name, pdf_filename) = os.path.split(os.path.abspath(pdf_fn))
                (image_subdir_name, extension) = os.path.splitext(pdf_filename)
                image_subdir_name = image_subdir_prefix + image_subdir_name + image_subdir_suffix
                image_subdir_path = os.path.join(pdf_parent_dir_name, image_subdir_name)
                os.makedirs(image_subdir_path)

            for page_num in range(range_from - 1, range_to):
                page_url = list(url)
                file_extension = 'jpg'
                if quality == 'high' or quality == 'extrahigh':
                    file_extension = 'bin'
                page_url[2] = '{}/{}/{:04d}.{}'.format(prefix, quality, page_num, file_extension)
                page_url = urlunparse(page_url)

                try:
                    req = Request(page_url , headers=headers)
                    with urlopen(req) as f:
                        LOGGER.info('Downloading page {} from {}...'.format(page_num + 1, page_url))

                        # if: the extralow, low & mid quality "jpg" format URLs
                        if quality == 'extralow' or quality == 'low' or quality == 'mid':
                            im = Image.open(f)
                        # else: the high quality "bin" format URL
                        elif quality == 'high':
                            # Rewrite the beginning of the file to include the proper JPEG file type code.
                            jpg_header = binascii.unhexlify('FFD8')
                            filedata = f.read()[2:]
                            imgdata = BytesIO(jpg_header + filedata)
                            try:
                                im = Image.open(imgdata)
                            except PIL.UnidentifiedImageError as uie:
                                LOGGER.error('Page {} is not a valid image file. Unable to continue; exiting...'.format(
                                    page_num))
                                break
                        # else: the extrahigh quality "bin" format URL
                        elif quality == 'extrahigh':
                            # Rewrite the beginning of the file to include the proper RIFF/webp file type code.
                            riff_header = binascii.unhexlify('5249')
                            filedata = f.read()[2:]
                            imgdata = BytesIO(riff_header + filedata)
                            try:
                                im = Image.open(imgdata)
                            except PIL.UnidentifiedImageError as uie:
                                LOGGER.error('Page {} is not a valid image file. Unable to continue; exiting...'.format(
                                    page_num))
                                break

                except HTTPError as e:
                    if e.code == 404 and quality == 'extrahigh':
                        LOGGER.info('No image found. Some magazines are not available in \'extrahigh\' quality; try \'high\' quality instead. => stopping')
                        break
                    elif e.code == 404:
                        LOGGER.info('No image found => stopping')
                        break
                    raise e

                w, h = tuple(dim / dpi for dim in im.size)

                LOGGER.info('Image is {} x {} pixels and {:.2f}in x {:.2f}in at {} DPI'.format(im.width, im.height,
                                                                                             w, h, dpi))
                c.setPageSize((w * inch, h * inch))
                c.drawInlineImage(im, 0, 0, w * inch, h * inch)
                c.showPage()
                if save_images:
                    # Save in "human-ranged" format - starting the page count from 1, not 0.
                    if quality == 'extrahigh':
                        image_name = '{:04d}.webp'.format(page_num + 1)
                        image_path = os.path.join(image_subdir_path, image_name)
                        im.save(image_path, lossless=True)
                    else:
                        image_name = '{:04d}.jpg'.format(page_num + 1)
                        image_path = os.path.join(image_subdir_path, image_name)
                        im.save(image_path)
                sleep(delay)

    # else quality == 'original'
    else:
        LOGGER.info("Downloading magazine as the original PDF")
        post_request_url = 'http://readerv2.pocketmags.com/PrintPage'
        post_request_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "readerv2.pocketmags.com",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
            "Origin": "https://pocketmags.com",
            "Referer": "https://pocketmags.com"
        }
        post_request_data = {
            "id": magazine_uuid,
            "user": user_uuid,
        }

        LOGGER.info('Determining the page number of the end of the magazine')
        # Check the range_to value exists as a magazine page (as the default is 999)
        # by checking the extralow JPG for the page exists. Need HTTP response 200, not 404.
        # Jumps between pages in 20,10,5,2,1 page intervals to avoid having to check everysingle page.
        # The page jump size halves and the process works backwards after a page is not found.
        file_extension = 'jpg'
        page_num = range_from - 1
        page_jump = 20
        last_good_page = -1
        last_bad_page = None
        bad_page_count = 0
        bad_page_limit = page_jump
        jpeg_quality = 'extralow'
        while True:
            sleep(delay)
            jpeg_url = list(url)
            jpeg_url[2] = '{}/{}/{:04d}.{}'.format(prefix, jpeg_quality, page_num, file_extension)
            jpeg_url = urlunparse(jpeg_url)
            jpeg_exists_response = requests.get(url=jpeg_url)
            if jpeg_exists_response.status_code == 200:
                last_good_page = page_num
                if last_good_page + 1 == last_bad_page:
                    # Output as human-readable page numbers (counting from 1 not 0)
                    LOGGER.info("Page {} is the last good page".format(last_good_page + 1))
                    break
                page_num += page_jump
            elif jpeg_exists_response.status_code == 404:
                last_bad_page = page_num
                bad_page_count += 1
                page_jump = page_jump // 2
                if page_jump < 1:
                    page_jump = 1
                page_num -= page_jump
                if page_num < 0:
                    page_num = 0
                if bad_page_count == bad_page_limit:
                    raise RuntimeError("Cannot find any valid page numbers, exiting...")
            else:
                raise RuntimeError(
                    "Unexpected HTTP error code encountered while probing for the last page in the magazine: HTTP error {}".format(
                        jpeg_exists_response.status_code))

            LOGGER.debug("HTTP response code {} for URL {}".format(jpeg_exists_response.status_code, jpeg_url))
            LOGGER.debug("Last good page number: {}, last bad page number: {}".format(last_good_page, last_bad_page))
            LOGGER.debug("Page jump value is {}".format(page_jump))
            LOGGER.debug("Next page to be queried is {}".format(page_num))

        # Determine if magazine is to be downloaded to the end or an earlier user-specified page number
        # Convert last_good_page to human-readable form, the same form as the range_to variable.
        last_good_page = last_good_page + 1
        if last_good_page < range_to:
            range_to = last_good_page
            LOGGER.info('Downloading the magazine from page {} to the end of the magazine on page {}'.format(range_from,
                                                                                                           range_to,
                                                                                                           last_good_page))
        else:
            LOGGER.info(
                'Downloading the magazine from page {} to page {} instead of to the end of the magazine on page {}'.format(
                    range_from, range_to, last_good_page))

        # Add the required number of pages to the post_request_data
        for page_num in range(range_from - 1, range_to):
            post_request_data["pages[{}]".format(page_num)] = page_num

        LOGGER.debug('Post request data to be sent is:')
        LOGGER.debug(post_request_data)

        pdf_response = requests.post(url=post_request_url, data=post_request_data, headers=post_request_headers)

        if pdf_response.status_code == 200:
            LOGGER.info('Success: Downloaded magazine')
            LOGGER.info('Editing downloaded magazine...')
        else:
            LOGGER.error('Unable to download magazine: HTTP error code {}'.format(pdf_response.status_code))
            exit(1)

        pdf_download = bytearray(pdf_response.content)

        # Manipulate various elements of the PDF file
        number_of_pages = range_to - (range_from - 1)

        # The user UUID watermarks which printed on each page are all added together as objects beginning with
        # '<</Length' located at the beginning of the PDF file before any of the magazine content appears.
        # The objects are variable length flate-encoded streams that hold the User UUID amongst other compressed data.
        # The User UUID never appears in plaintext in the PDF.
        # It is the first object after the '<</Length' objects  which is not '<</Length' but '<</Type...' or
        # '<</ArtBox...' or something else that signifies the start of the magazine content.
        # There should be twice as many '<</Length' objects as pages in the magazine.
        start_of_magazine_content_location = -1
        LOGGER.info('Searching the PDF for the magazine content...')
        # Find the short/long pairs of flate-encoded stream objects that hold the position and text of the user UUID watermark on each page
        LOGGER.info('Searching for the flate-encoded stream objects containing the User UUID watermark...')
        length_string = '<</Length '
        next_object_string = length_string
        # Quick and dirty way to get to the approximate location to start searching
        # The iTextSharp object will be precisely located later, but it's a circular dependency situation here...
        next_object_location = pdf_download.find(b'iTextSharp')
        uuid_stream_object_list = list()
        while next_object_location != -1:
            next_object_location += 1
            # Find each '<</' after the iTextSharp object
            next_object_location = pdf_download.find(b'<</', next_object_location)
            next_object_string = pdf_download[next_object_location:next_object_location + len(length_string)].decode(
                encoding='cp1252')
            # Find the first string beginning '<</...' that is not '<</Length'. That is the start of the magazine.
            if next_object_string == length_string:
                uuid_stream_object_list.append(next_object_location)
            else:
                start_of_magazine_content_location = pdf_download.find(b'>>', next_object_location) + 1
                break
        if len(uuid_stream_object_list) == number_of_pages * 2:
            LOGGER.debug('Found the expected number of flate-encoded User UUID stream objects')
            LOGGER.debug(
                'The number of User UUID stream objects ({}) is twice then number of pages ({}). This is correct.'.format(
                    len(uuid_stream_object_list), number_of_pages))
        else:
            LOGGER.warning(
                'The number of User UUID stream objects ({}) should be twice then number of pages ({}). It is not.'.format(
                    len(uuid_stream_object_list), number_of_pages))

        # Print summary of all User UUID stream objects found
        if debug:
            uuid_stream_object_temp_counter = 0
            for uuid_stream_object_offset in uuid_stream_object_list:
                uuid_stream_object_temp_counter += 1
                LOGGER.debug('{}: User UUID flate-encoded stream object found at offset {}'.format(
                    uuid_stream_object_temp_counter,
                    hex(uuid_stream_object_offset)))

        # Decode the flate-encoded objects containing the User UUID objects out of curiosity
        LOGGER.debug('Decoding the previously discovered flate-encoded objects containing the User UUID watermarks...')
        # for uuid_object_location in uuid_placement_object_location_list:
        uuid_object_counter = 0
        for uuid_object_location in uuid_stream_object_list:
            length_string_start_offset = uuid_object_location + 10
            length_string_end_offset = pdf_download.find(b'/Filter/FlateDecode', length_string_start_offset) - 1
            flate_encoded_stream_integer_length = int(
                pdf_download[length_string_start_offset:length_string_end_offset + 1].decode(encoding='cp1252'))
            flate_encoded_stream_start_offset = pdf_download.find(b'>>stream\n', uuid_object_location) + 9
            flate_encoded_stream_end_offset = flate_encoded_stream_start_offset + flate_encoded_stream_integer_length - 1
            flate_encoded_stream_content = pdf_download[
                                           flate_encoded_stream_start_offset:flate_encoded_stream_end_offset + 1]
            flate_encoded_stream_decoded_content = zlib.decompress(flate_encoded_stream_content, wbits=0)
            LOGGER.debug('Working on User UUID flate-encoded stream number {}'.format(uuid_object_counter + 1))
            LOGGER.debug('User UUID flate-encoded placement length string start offset is {}'.format(
                hex(length_string_start_offset)))
            LOGGER.debug('User UUID flate-encoded placement length string end offset is {}'.format(
                hex(length_string_end_offset)))
            LOGGER.debug('User UUID flate-encoded placement stream real integer value is {}'.format(
                flate_encoded_stream_integer_length))
            LOGGER.debug('User UUID flate-encoded placement stream start offset is {}'.format(
                hex(flate_encoded_stream_start_offset)))
            LOGGER.debug('User UUID flate-encoded placement stream end offset is {}'.format(
                hex(flate_encoded_stream_end_offset)))
            LOGGER.debug('User UUID flate-encoded placement stream content byte length is {}'.format(
                len(flate_encoded_stream_content)))
            LOGGER.debug('User UUID flate-encoded placement stream content is {}'.format(flate_encoded_stream_content))
            LOGGER.debug('User UUID flate-encoded placement decoded stream content is {}'.format(
                flate_encoded_stream_decoded_content))

            if user_uuid_destroy:
                LOGGER.info('Zeroing the user UUID flate-encoded placement stream data...')
                flate_encoded_stream_replacement_content = bytes(
                    '0'.encode(encoding='cp1252') * len(flate_encoded_stream_content))
                LOGGER.debug('User UUID flate-encoded placement replacement stream content is: {}'.format(
                    flate_encoded_stream_replacement_content))
                pdf_download[
                flate_encoded_stream_start_offset:flate_encoded_stream_end_offset + 1] = flate_encoded_stream_replacement_content
            uuid_object_counter += 1

        # The software that Pocketmags use to add the User UUID watermarks is called iTextSharp and it adds its own
        # object near the beginning of the PDF in order to advertise itself and add two timestamps.
        # Find the location of the iTextSharp object that includes the two timestamps
        LOGGER.info('Searching for the iTextSharp object and associated properties...')
        itextsharp_object_location = pdf_download.find(b'<</Producer(iTextSharp', 0, start_of_magazine_content_location)
        itextsharp_object_end_location = -1
        itextsharp_object_creationdate_property_location = -1
        itextsharp_object_moddate_property_location = -1
        if itextsharp_object_location == -1:
            LOGGER.warning('Cannot find the iTextSharp object location in the PDF file.')
        else:
            # Find the next 'endobj' tag after the iTextSharp object hast started in order to limit the search range for
            # the two timestamps that should be associated with it.
            itextsharp_object_end_location = pdf_download.find(b'endobj', itextsharp_object_location)
            LOGGER.debug('Byte offset of the iTextSharp object located at {}'.format(hex(itextsharp_object_location)))

            # Find the CreationDate timestamp property location
            itextsharp_object_creationdate_property_location = pdf_download.find(b'CreationDate',
                                                                                 itextsharp_object_location,
                                                                                 itextsharp_object_end_location)
            if itextsharp_object_creationdate_property_location == -1:
                LOGGER.warning('Cannot find the iTextSharp object\'s CreationDate property.')
            else:
                LOGGER.debug('Byte offset of the iTextSharp object\'s CreationDate property is {}'.format(
                    hex(itextsharp_object_creationdate_property_location)))
            # Find the ModDate timestamp property location
            itextsharp_object_moddate_property_location = pdf_download.find(b'ModDate', itextsharp_object_location,
                                                                            itextsharp_object_end_location)
            if itextsharp_object_moddate_property_location == -1:
                LOGGER.warning('Cannot find the iTextSharp object\'s ModDate property.')
            else:
                LOGGER.debug('Byte offset of the iTextSharp object\'s ModDate property is {}'.format(
                    hex(itextsharp_object_moddate_property_location)))

        # Create new timestamps for the iTextSharp object CreationDate and ModDate properties
        # Timestamps need to be 14 char YYYYmmddHHMMSS format.
        time_now = datetime.now()
        time_delta = timedelta(microseconds=random.randrange(0, 999),
                               milliseconds=random.randrange(0, 999),
                               seconds=random.randrange(0, 60),
                               minutes=random.randrange(0, 60),
                               hours=random.randrange(0, 23),
                               days=random.randrange(0, 30),
                               weeks=random.randrange(0, 4))
        time_replacement = time_now + time_delta
        timestamp_length = 14
        creationdate_date_offset_from_property_tag = 15
        moddate_date_offset_from_property_tag = 10
        itextsharp_object_original_content = pdf_download[itextsharp_object_location:itextsharp_object_end_location]
        itextsharp_object_creationdate_property_original_value = pdf_download[
                                                                 itextsharp_object_creationdate_property_location + creationdate_date_offset_from_property_tag:
                                                                 itextsharp_object_creationdate_property_location + creationdate_date_offset_from_property_tag + timestamp_length].decode(encoding='cp1252')
        itextsharp_object_creationdate_property_replacement_value = time_replacement.strftime('%Y%m%d%H%M%S').encode(encoding='cp1252')
        itextsharp_object_moddate_property_original_value = pdf_download[
                                                            itextsharp_object_moddate_property_location + moddate_date_offset_from_property_tag:
                                                            itextsharp_object_moddate_property_location + moddate_date_offset_from_property_tag + timestamp_length].decode(encoding='cp1252')
        itextsharp_object_moddate_property_replacement_value = time_replacement.strftime('%Y%m%d%H%M%S').encode(encoding='cp1252')
        if timestamp_change:
            LOGGER.info('Changing the PDFs internal timestamps...')
            pdf_download[
            itextsharp_object_creationdate_property_location + creationdate_date_offset_from_property_tag:
            itextsharp_object_creationdate_property_location + creationdate_date_offset_from_property_tag + len(
                itextsharp_object_creationdate_property_replacement_value)] = itextsharp_object_creationdate_property_replacement_value
            pdf_download[
            itextsharp_object_moddate_property_location + moddate_date_offset_from_property_tag:
            itextsharp_object_moddate_property_location + moddate_date_offset_from_property_tag + len(
                itextsharp_object_moddate_property_replacement_value)] = itextsharp_object_moddate_property_replacement_value

        LOGGER.debug('Original iTextSharp CreationDate timestamp is {}, length {}'.format(
            itextsharp_object_creationdate_property_original_value,
            len(itextsharp_object_creationdate_property_original_value)))
        if timestamp_change:
            LOGGER.debug('Replacement iTextSharp CreationDate timestamp is {}, length {}'.format(
                itextsharp_object_creationdate_property_replacement_value,
                len(itextsharp_object_creationdate_property_replacement_value)))
        LOGGER.debug('Original iTextSharp ModDate timestamp is {}, length {}'.format(
            itextsharp_object_moddate_property_original_value,
            len(itextsharp_object_moddate_property_original_value)))
        if timestamp_change:
            LOGGER.debug('Replacement iTextSharp ModDate timestamp is {}, length {}'.format(
                itextsharp_object_moddate_property_replacement_value,
                len(itextsharp_object_moddate_property_replacement_value)))
        LOGGER.debug('iTextSharp object before timestamp modification is: {}'.format(itextsharp_object_original_content))
        if timestamp_change:
            LOGGER.debug('iTextSharp object after  timestamp modification is: {}'.format(
                pdf_download[itextsharp_object_location:itextsharp_object_end_location]))

        # Find the locations of all the UUID opacity objects
        LOGGER.info("Searching for user UUID opacity objects...")
        uuid_opacity_object_original_value = b'<</ca 0.35/CA 0.3>>'
        uuid_opacity_object_location_list = list()
        uuid_opacity_object_last_location_found = -1
        count = 0
        latest_location = 0
        while latest_location > -1:
            latest_location: int = pdf_download.find(uuid_opacity_object_original_value,
                                                     uuid_opacity_object_last_location_found + 1,
                                                     start_of_magazine_content_location)
            if latest_location != -1:
                uuid_opacity_object_location_list.append(int(latest_location))
                count += 1
                uuid_opacity_object_last_location_found = latest_location
            # else:
            #     break

        # Check the number of UUID opacity objects found matches the number of pages expected in the magazine
        if len(uuid_opacity_object_location_list) != number_of_pages:
            LOGGER.warning('The number of UUID opacity objects found does not equal the number of pages expected:')
            LOGGER.warning('         UUID Opacity objects found: {}, pages expected: {}'.format(
                len(uuid_opacity_object_location_list), number_of_pages))
        else:
            LOGGER.debug(
                'Number of UUID opacity objects found ({}) equals the number of pages expected ({}). This is good.'.format(
                    len(uuid_opacity_object_location_list), number_of_pages))

        # Print summary of all user UUID opacity objects found
        if debug:
            uuid_opacity_object_temp_counter = 0
            for uuid_opacity_object_offset in uuid_opacity_object_location_list:
                uuid_opacity_object_temp_counter += 1
                LOGGER.debug('{}: UUID opacity object found at offset {}'.format(uuid_opacity_object_temp_counter,
                                                                          hex(uuid_opacity_object_offset)))

        # Modify user UUID opacity objects to make the UUID less visible
        # ca = fill (non-stroking), CA = border (stroking)
        # NB: Do NOT change the length of the uuid_opacity_object_replacement_value string!
        uuid_opacity_object_replacement_value = b'<</ca 0.35/CA 0.3>>'
        if user_uuid_hide:
            uuid_opacity_object_replacement_value = b'<</ca 0.00/CA 0.0>>'
        if user_uuid_destroy:
            uuid_opacity_object_replacement_value = b'0000000000000000000'
        if user_uuid_hide or user_uuid_destroy:
            LOGGER.info('Changing the User UUID watermark opacity...')
            for uuid_opacity_object_location in uuid_opacity_object_location_list:
                pdf_download[uuid_opacity_object_location:uuid_opacity_object_location + len(
                    uuid_opacity_object_replacement_value)] = uuid_opacity_object_replacement_value
                LOGGER.debug('New UUID opacity object value written to the PDF file is: {}'.format(pdf_download[
                                                                                            uuid_opacity_object_location:uuid_opacity_object_location + len(
                                                                                                uuid_opacity_object_replacement_value)]))

        LOGGER.info('Finished editing the downloaded PDF file')

        # Save the PDF download
        # TODO: Check for any non-existent directories in the output file path and create them before saving the file.
        with open(pdf_fn, 'bw') as pdf_original:
            pdf_original.write(pdf_download)
            LOGGER.info('Saved PDF download to {}'.format(pdf_fn))


if __name__ == '__main__':
    main()
