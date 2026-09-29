"""What an uploaded image really is, read from its bytes (owner rule R70).

A content type and an extension are what the uploader says; the first bytes
are what the file is. One sniffer for every door that takes an image, so a
format added here is accepted everywhere at once.
"""
IMAGE_TYPES = ('image/png', 'image/jpeg', 'image/webp')


def sniff(head):
    """The real type from the first bytes, whatever the upload claimed."""
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


def image_refusal(upload, max_bytes):
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
