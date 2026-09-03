# -*- coding: utf-8 -*-
"""Everything an uploaded overlay has to get right, checked when it arrives.

CEO, 4 September 2026: "with this issue for the fonts, please check the rest of
the other things that are absolutely needed for the site to get what it wants
from the generated html file of the video or the image. remember some html
files will come animated and some will be still for images."

The fonts question was one instance of a class: **things a file gets wrong that
nothing errors on**. An overlay is not run in a page anybody is watching until
it is on air in front of an audience, so every one of these is discovered at the
worst possible moment.

Each check below is something that has no error message of its own:

  a missing font          substitutes silently, wrong typeface on air
  a missing design image  a blank rectangle, or a broken glyph
  an opaque background    BLACKS OUT THE STREAM, which is the worst of them
  the wrong stage size    the graphic sits in a corner, or is cut off
  an endless loop         a six hour broadcast with something breathing on it
  no marks at all         a file that will never change (already covered)

They are WARNINGS rather than refusals, deliberately. An organiser knows things
this cannot: their design may legitimately be 1080x1920 for a vertical stream,
and their ticker may legitimately loop. Refusing would make the tool argue with
the person using it. Telling them, at the moment they can still fix it, is the
whole job.
"""

import re

#: `src: url(...)` inside an `@font-face`, however it is quoted.
FONT_SRC = re.compile(r"""@font-face[^}]*?url\(\s*['"]?([^'")]+)['"]?""",
                      re.I | re.S)

#: Any other `url(...)` in CSS: a background, a mask, a cursor.
CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.I)

#: `<img src="...">`, and the lazy-loading spellings.
IMG_SRC = re.compile(r"""<img[^>]+?(?:src|data-src)\s*=\s*['"]([^'"]+)['"]""",
                     re.I)

#: A background on `html` or `body`. An overlay is composited over video.
BODY_BACKGROUND = re.compile(
    r"""(?:^|[^.\w])(?:html|body)\s*(?:,\s*(?:html|body)\s*)*\{[^}]*?"""
    r"""background(?:-color)?\s*:\s*([^;}]+)""",
    re.I | re.S | re.M)

#: The size the stage is drawn at.
STAGE_SIZE = re.compile(r"""(?:width|height)\s*:\s*(\d{3,5})px""", re.I)

#: An animation that never stops.
ENDLESS = re.compile(r"""animation[^;}]*?\binfinite\b""", re.I)


def _is_local(url):
    """A path that will not resolve once the file is served on its own.

    Absolute URLs are fetched. Data URIs are in the file. Everything else is a
    relative path pointing at a folder that does not exist beside an upload.
    """
    url = (url or '').strip()
    if not url:
        return False
    if url.lower().startswith(('data:', 'blob:', '#')):
        return False
    if re.match(r'^(https?:)?//', url):
        return False
    return True


def _transparent(value):
    """Whether a background value leaves the video showing through."""
    text = (value or '').strip().lower()
    if not text:
        return True
    if 'transparent' in text or text == 'none':
        return True
    # rgba(...,0) and hsla(...,0): a zero alpha is transparent.
    alpha = re.search(r'(?:rgba|hsla)\([^)]*?,\s*(0|0?\.0+)\s*\)', text)
    return bool(alpha)


def problems(markup):
    """Everything worth telling the uploader, as sentences they can act on."""
    text = markup if isinstance(markup, str) else markup.decode('utf-8', 'replace')
    found = []

    # 1. Fonts that will not arrive.
    for src in FONT_SRC.findall(text):
        if _is_local(src):
            found.append(
                'This file loads a font from "%s", which will not be there '
                'once it is uploaded, and a missing font is replaced silently: '
                'the overlay goes on air in the wrong typeface. Paste the font '
                'into the file as a data URI, or upload it to the studio and '
                'use the name you gave it as the font family.' % src[:80])

    # 2. Design pictures that will not arrive. A marked image is fine: the
    #    runtime replaces its src, so its placeholder never has to resolve.
    font_urls = set(FONT_SRC.findall(text))
    marked = set(re.findall(r'data-vent-src\s*=\s*["\'][^"\']+["\']', text, re.I))
    seen = set()
    for src in list(CSS_URL.findall(text)) + list(IMG_SRC.findall(text)):
        if src in font_urls or src in seen or not _is_local(src):
            continue
        seen.add(src)
        # An `<img>` carrying data-vent-src has its address supplied at runtime.
        if marked and re.search(
                r'<img[^>]*?%s[^>]*?data-vent-src' % re.escape(src), text, re.I):
            continue
        found.append(
            'This file loads a picture from "%s". There is no folder beside an '
            'uploaded overlay, so that will be missing on air. Put it in the '
            'file as a data URI, upload it to the studio, or mark it with '
            'data-vent-src so the tournament supplies it.' % src[:80])

    # 3. An opaque background. The worst of them: it does not look broken, it
    #    looks like the stream has gone black.
    for value in BODY_BACKGROUND.findall(text):
        if not _transparent(value):
            found.append(
                'The page background is set to "%s". An overlay is composited '
                'over video, so anything but transparent covers the stream. '
                'Remove it, or set it on an inner element instead.'
                % value.strip()[:60])
            break

    # 4. A stage that is not the size of a browser source.
    sizes = {int(n) for n in STAGE_SIZE.findall(text)}
    if sizes and not (sizes & {1920, 1080}):
        found.append(
            'Nothing in this file is 1920 or 1080 pixels. A browser source is '
            '1920x1080, so a stage drawn at another size will sit in a corner '
            'or be cut off. If your stream is vertical this is fine, and you '
            'can ignore it.')

    # 5. Something that never stops. Worth saying, not worth refusing: a ticker
    #    that scrolls for ever is a real design.
    if ENDLESS.search(text):
        found.append(
            'Something in this file animates for ever. On a six hour broadcast '
            'that is a graphic that never settles, and it costs a browser '
            'source real work the whole time. Fine for a ticker; worth a look '
            'for anything else.')

    return found
