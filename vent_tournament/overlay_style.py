"""A broadcast's overlay style: the fonts, colours and logos every designed
overlay starts from (inbox 393).

CEO, 30 September 2026: "They should be able to also decide on an overlay
design template that will apply to all overlays, like primary fonts, secondary
fonts, primary colors, secondary colors, etc."

Each designed overlay (src/lib/overlays on the frontend) names which of these
roles each of its fields follows. A field the organiser changed on one overlay
keeps that overlay's own value; every other field follows the style, so
changing the style changes every overlay at once.

Stored on the BroadcastSession, and carried into the next broadcast when one
starts, with each designed overlay's own settings, so a brand is set once
rather than every broadcast day.
"""
import hashlib
import json
import re

#: The roles, and what each one holds. Nothing else is stored.
COLOURS = ('primary', 'secondary', 'text')
FONTS = ('font_primary', 'font_secondary')
PICTURES = ('logo', 'logo_secondary')
ROLES = COLOURS + FONTS + PICTURES

_HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
#: A built-in face by name, or a font uploaded to the studio library.
_FONT = re.compile(r'^(?:[a-z_]{2,20}|asset:\d{1,9})$')
#: The platform logo, no logo, or a picture in the studio library by id.
_PICTURE = re.compile(r'^(?:default|none|\d{1,9})$')


class StyleError(ValueError):
    def __init__(self, field):
        super().__init__(field)
        self.field = field


def clean(raw):
    """The style as stored: known roles only, each checked. An empty value
    means "follow the design's own default" and is dropped."""
    if not isinstance(raw, dict):
        raise StyleError('style')
    out = {}
    for key, value in raw.items():
        if key not in ROLES:
            raise StyleError(key)
        text = str(value if value is not None else '').strip()
        if not text:
            continue
        pattern = _HEX if key in COLOURS else _FONT if key in FONTS else _PICTURE
        if not pattern.match(text):
            raise StyleError(key)
        out[key] = text.upper() if key in COLOURS else text
    return out


def stamp(style):
    """A short fingerprint for the feed version: a browser source redraws only
    when the version moves, so a style change must move it."""
    blob = json.dumps(style or {}, sort_keys=True)
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()[:10]
