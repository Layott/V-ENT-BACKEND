"""Small real files for tests that upload something.

Since 30 September 2026 every upload door reads the bytes (vent_auth.uploads),
so a test that posts b'a picture' is posting something that is not one, and is
refused like any other. These are the smallest files each sniffer accepts:
a real PNG that PIL opens, and the true opening bytes of each other kind.
"""
import io


def png(size=(4, 4), colour=(200, 30, 30)):
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', size, colour).save(out, format='PNG')
    return out.getvalue()


FONT_MAGIC = {'.woff2': b'wOF2', '.woff': b'wOFF', '.ttf': b'\x00\x01\x00\x00', '.otf': b'OTTO'}


def font(extension='.woff2'):
    return FONT_MAGIC[extension.lower()] + b'\x00' * 60


def mp4():
    return b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 60


def webm():
    return b'\x1a\x45\xdf\xa3' + b'\x00' * 60
