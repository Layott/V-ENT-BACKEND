"""What an uploaded file really is, read from its bytes (owner rule R70).

A content type and an extension are what the uploader says; the first bytes
are what the file is. One sniffer for every door that takes a file, so a
format added here is accepted everywhere at once.

Every door calls `image_refusal` (pictures) or `file_refusal` (anything with a
wider list: clips, fonts, spreadsheets, documents, overlays) BEFORE the file
reaches a model, and every FileField stores under `OpaqueName`, so what lands
on disk is a random name with the extension of what the bytes turned out to
be, never the name somebody typed.
"""
import os
import uuid

from django.utils.deconstruct import deconstructible

IMAGE_TYPES = ('image/png', 'image/jpeg', 'image/webp')

#: The biggest picture any door takes. Doors with their own smaller cap pass it.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: What each kind a door may accept is made of, by sniffed type.
KINDS = {
    'image': ('image/png', 'image/jpeg', 'image/webp'),
    'gif': ('image/gif',),
    'video': ('video/mp4', 'video/webm'),
    'font': ('font/woff2', 'font/woff', 'font/ttf', 'font/otf'),
    'pdf': ('application/pdf',),
    'spreadsheet': ('application/zip',),
    'document': ('application/zip',),
    'html': ('text/html',),
    'text': ('text/plain',),
}



def sniff(head):
    """The real type from the first bytes, whatever the upload claimed."""
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    # mp4 and mov are both ISO media: a box size, then `ftyp`.
    if head[4:8] == b'ftyp':
        return 'video/mp4'
    if head[:4] == b'\x1a\x45\xdf\xa3':
        return 'video/webm'
    if head[:4] == b'wOF2':
        return 'font/woff2'
    if head[:4] == b'wOFF':
        return 'font/woff'
    if head[:4] in (b'\x00\x01\x00\x00', b'true'):
        return 'font/ttf'
    if head[:4] == b'OTTO':
        return 'font/otf'
    if head[:5] == b'%PDF-':
        return 'application/pdf'
    # xlsx, xlsm and docx are zip archives; the reader that opens them is the
    # rest of the check.
    if head[:4] == b'PK\x03\x04':
        return 'application/zip'
    return None


def _text_type(upload):
    """text/html or text/plain for a file that is readable text, else None."""
    upload.seek(0)
    body = upload.read(64 * 1024)
    upload.seek(0)
    if b'\x00' in body:
        return None
    try:
        text = body.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = body.decode('latin-1')
        except Exception:
            return None
    lowered = text.lstrip().lower()
    if lowered.startswith(('<!doctype html', '<html', '<head', '<body', '<div',
                           '<style', '<svg', '<script', '<meta', '<link', '<!--')):
        return 'text/html'
    return 'text/plain'


def detect(upload):
    """The sniffed type of an upload, leaving it at the start."""
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    found = sniff(head)
    if found is None:
        found = _text_type(upload)
    return found


def image_refusal(upload, max_bytes=MAX_IMAGE_BYTES):
    """None when `upload` is a real PNG, JPG or WebP within `max_bytes`,
    otherwise the code to refuse it with: IMAGE_TOO_LARGE or NOT_AN_IMAGE.
    Leaves the file positioned at the start, ready to be saved."""
    if upload is None:
        return 'NOT_AN_IMAGE'
    if upload.size > max_bytes:
        return 'IMAGE_TOO_LARGE'
    head = upload.read(16)
    upload.seek(0)
    if sniff(head) not in IMAGE_TYPES:
        return 'NOT_AN_IMAGE'
    try:
        from PIL import Image
        Image.open(upload).verify()
    except Exception:
        return 'NOT_AN_IMAGE'
    finally:
        upload.seek(0)
    return None


def file_refusal(upload, max_bytes, kinds):
    """None when `upload`'s bytes are one of `kinds` (keys of KINDS) and it is
    within `max_bytes`, otherwise FILE_TOO_LARGE or UNSUPPORTED_FILE.

    Pictures among the kinds are also opened with PIL, as image_refusal does,
    because a PNG header on something else is the classic disguise."""
    if upload is None:
        return 'UNSUPPORTED_FILE'
    if upload.size > max_bytes:
        return 'FILE_TOO_LARGE'
    allowed = set()
    for kind in kinds:
        allowed.update(KINDS[kind])
    found = detect(upload)
    if found not in allowed:
        return 'UNSUPPORTED_FILE'
    if found in IMAGE_TYPES or found == 'image/gif':
        try:
            from PIL import Image
            Image.open(upload).verify()
        except Exception:
            return 'UNSUPPORTED_FILE'
        finally:
            upload.seek(0)
    return None


@deconstructible
class OpaqueName:
    """upload_to that stores `<folder>/<random hex><ext>`.

    The door has already checked the bytes (image_refusal / file_refusal), so
    the extension is the uploaded one reduced to at most eight letters and
    digits: neither a path nor a double extension from the uploader ever
    reaches the disk, and the name cannot be guessed from the person."""

    def __init__(self, folder):
        self.folder = folder.strip('/')

    def __call__(self, instance, filename):
        raw = os.path.splitext(os.path.basename(filename or ''))[1].lower()
        clean = ''.join(c for c in raw[1:] if c.isalnum())[:8]
        ext = '.' + clean if clean else ''
        return '%s/%s%s' % (self.folder, uuid.uuid4().hex, ext)

    def __eq__(self, other):
        return isinstance(other, OpaqueName) and other.folder == self.folder

    def __hash__(self):
        return hash(self.folder)


MESSAGES = {
    'NOT_AN_IMAGE': 'That file is not a picture. Use a PNG, JPG or WebP.',
    'IMAGE_TOO_LARGE': 'That picture is too large.',
    'UNSUPPORTED_FILE': 'That kind of file cannot be uploaded here.',
    'FILE_TOO_LARGE': 'That file is too large.',
}


def files_refusal(request, fields, *, kinds=('image',), max_bytes=MAX_IMAGE_BYTES):
    """Check every upload under `fields` (a name, or several) in one call.

    Returns None when all of them pass, otherwise a Response in the site's
    envelope naming the field and the code. `fields=None` checks every upload
    in the request. A door calls it before any file reaches a model:

        refused = uploads.files_refusal(request, ('logo', 'banner'))
        if refused:
            return refused
    """
    from rest_framework import status
    from rest_framework.response import Response

    if fields is None:
        # Every upload the request carries, for a door whose file fields are
        # named by position (sponsor_logo_0, sponsor_logo_1, ...).
        fields = tuple(request.FILES.keys())
    elif isinstance(fields, str):
        fields = (fields,)
    for field in fields:
        for upload in request.FILES.getlist(field):
            if not upload.size:
                # An empty slot, sent to keep a list in step by position
                # (sponsor logos). It carries nothing and is never stored.
                continue
            if tuple(kinds) == ('image',):
                code = image_refusal(upload, max_bytes)
            else:
                code = file_refusal(upload, max_bytes, kinds)
            if code:
                return Response({'status': 'error', 'code': code, 'field': field,
                                 'message': MESSAGES[code]},
                                status=status.HTTP_400_BAD_REQUEST)
    return None
