"""Text put on top of any overlay: the words, the size, the colour, the place.

CEO, 4 September 2026, inbox row 52: "also should be able to add text, change
the font size, color, position, animation of that text also on any overlay".

"On any overlay" is the load bearing half. V-ENT has two kinds of overlay and a
layer hangs off either: a graphic the platform draws (`BroadcastElement`) and an
HTML file an organiser designed and uploaded (`TournamentOverlay`). Built for one
and forgotten on the other is the fault `tools/check-parity.py` exists for, and
it has already happened five times in a day on this platform.

The rules live here rather than in a view because the same layer is written
through four routes on two prefixes. One copy of "what is a colour" is one place
for that answer to change.

Nothing here invents a vocabulary. Where a graphic already has a word for
something (the places it may sit, how far it may be nudged off its anchor, how
it arrives and how it leaves) a layer uses the same one, out of
`presentation.py`. A second list is a list that drifts, and this codebase has
paid for that twice: five label maps in the frontend and two format catalogues
in this app.

## Why a colour is free here and nowhere else

Every other surface on this platform takes its colour from a token, and that
rule is not being relaxed. A broadcast graphic differs in one specific way: it
carries the CLIENT's brand, over live video, and the operator is the person who
knows what the sponsor's red is. A colour picker here is the same decision as
letting them upload their own HTML.

So any hex is accepted, none of it reaches the rest of the product, and the
value is validated as a colour and nothing else. An empty or malformed value is
refused, never defaulted: a caption that silently turned white on a white lower
third is discovered on air.

## Why a refusal carries a code

A sentence built in Python cannot be translated. Everything raised here carries
a code, the field it is about, and the numbers or the list that made it wrong,
so the console can say which box is wrong in the reader's own language and can
say it with the real limits in it.
"""
import hashlib
import json
import re

from . import presentation

#: The typefaces a layer may name, deliberately short. A free font name is a
#: font the machine running OBS does not have, and the substitute it picks is
#: discovered on air. An organiser who wants their own face uploads it to the
#: studio and names the slot; see `font_slot` below.
FAMILIES = [
    ('house', 'V-ENT house'),
    ('condensed', 'Condensed'),
    ('display', 'Display'),
    ('accent', 'Accent'),
]

#: Three weights, not a free number. A weight the font does not have is drawn
#: synthetically by the browser, which looks like a smeared version of the real
#: thing at broadcast size.
WEIGHTS = [(400, 'Regular'), (600, 'Medium'), (800, 'Bold')]

ALIGNMENTS = [('left', 'Left'), ('centre', 'Centre'), ('right', 'Right')]

#: Pixels at 1920x1080, the raster every other measurement here uses. The floor
#: is the smallest thing anybody can read on a stream that has been compressed
#: twice; the ceiling is a word that fills the frame.
FONT_SIZE = (8, 400)
#: So two layers can arrive one after the other. A minute is longer than any
#: sting anybody builds.
DELAY_MS = (0, 60000)
#: 0 means it stays until the graphic goes.
DURATION_MS = (0, 600000)
#: Paint order and z, low first.
ORDER = (0, 999)

TEXT_MAX = 240
FIELD_MAX = 120

#: `#RRGGBB` or `#RRGGBBAA`. Three digit shorthand is not accepted: it is a
#: different thing from what the operator's picker hands over, and quietly
#: expanding it is the silent correction this whole module refuses to do.
_COLOUR = re.compile(r'^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$')

#: A studio font is addressed by its slot, and a slot is already restricted to
#: these characters at upload so it cannot carry a quote out of a font name and
#: into CSS. Same expression as `views_assets`, same reason.
_SLOT = re.compile(r'^[a-z0-9_]{1,40}$')

#: What a layer is before anybody changes anything. The model's own defaults
#: read from here so there is one answer rather than two that agree today.
KINDS = [
    ('text', 'Words'),
    ('asset', 'Something from the media library'),
]

#: How wide an asset layer may be drawn, in pixels at 1920x1080. Zero means the
#: media's own size. The ceiling is the frame: a layer wider than the frame is
#: a layer nobody can see the edges of, and it is always a mistake.
WIDTH_MAX = 1920

DEFAULTS = {
    'kind': 'text',
    'asset_id': None,
    'width_px': 0,
    'text': '',
    'field': '',
    'font_size': 64,
    'colour': '#FFFFFF',
    'family': 'house',
    'font_slot': '',
    'weight': 600,
    'align': 'centre',
    # Where a caption goes. `presentation.DEFAULTS` says `as_designed`, which
    # means "leave the graphic where its own design put it" and cannot mean
    # anything for words that have no design of their own.
    'position': 'bottom_centre',
    'offset_x': 0,
    'offset_y': 0,
    'entry': 'rise',
    'exit': 'fade',
    'delay_ms': 0,
    'duration_ms': 0,
    'order': 0,
    'is_active': True,
}

#: Every name a payload may carry. Anything else is refused rather than dropped,
#: for the reason `presentation.clean` gives: an operator who set `colour` on a
#: field called `color` and saw it ignored has no way to tell a typo from a
#: feature that does not work, and the first thing they do is set it again.
FIELDS = tuple(DEFAULTS)

#: The keys a layer is serialised with, in the order the contract lists them.
SHAPE = ('id', 'kind', 'asset_id', 'asset_url', 'asset_kind', 'asset_name',
         'width_px', 'text', 'field', 'font_size', 'colour', 'family', 'font_slot',
         'weight', 'align', 'position', 'offset_x', 'offset_y', 'entry',
         'exit', 'delay_ms', 'duration_ms', 'order', 'is_active')


class LayerError(ValueError):
    """A layer that cannot be stored, with the code and field that say why."""

    def __init__(self, message, code, field=None, data=None):
        super().__init__(message)
        self.code = code
        self.field = field
        self.data = data or {}


def _whole(value, field, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise LayerError('%s is a whole number.' % field, 'INVALID_NUMBER',
                         field, {'min': low, 'max': high})
    if number < low or number > high:
        raise LayerError('%s is between %d and %d.' % (field, low, high),
                         'INVALID_NUMBER', field, {'min': low, 'max': high})
    return number


def _choice(value, field, allowed):
    if value not in allowed:
        raise LayerError(
            '%s is one of: %s.' % (field, ', '.join(str(a) for a in allowed)),
            'INVALID_CHOICE', field, {'allowed': list(allowed)})
    return value


def _colour(value):
    text = str(value or '').strip()
    if not _COLOUR.match(text):
        raise LayerError('A colour is written #RRGGBB or #RRGGBBAA.',
                         'INVALID_COLOUR', 'colour')
    # Upper cased so two operators typing the same colour store the same
    # string. Case is the only thing normalised, and it changes no pixel.
    return '#' + text[1:].upper()


def _slot(value):
    text = str(value or '').strip().lower()
    if text and not _SLOT.match(text):
        raise LayerError(
            'A font slot is lower case letters, digits and underscores.',
            'INVALID_SLOT', 'font_slot')
    return text


def apply(row, data):
    """The fields a layer carries, read off a payload. Shared by create and edit.

    Only what the payload names is touched, so a console can correct one box
    mid show without resending the rest and without racing its own last write.
    Raises `LayerError`; the caller turns that into the envelope.
    """
    if not isinstance(data, dict):
        raise LayerError('A text layer is a set of named values.',
                         'VALIDATION_FAILED')

    for key in data:
        if key not in FIELDS:
            raise LayerError('There is no text layer setting called %s.' % key,
                             'UNKNOWN_FIELD', key)

    if 'kind' in data:
        row.kind = _choice(str(data.get('kind') or ''), 'kind',
                           [v for v, _label in KINDS])
    if 'asset_id' in data:
        row.asset = _asset(data.get('asset_id'), row)
    if 'width_px' in data:
        row.width_px = _whole(data.get('width_px'), 'width_px', 0, WIDTH_MAX)
    if 'text' in data:
        row.text = str(data.get('text') or '')[:TEXT_MAX]
    if 'field' in data:
        # A path into the feed, e.g. `tournament.title`. Not checked against
        # the feed: what a feed carries depends on the tournament, and refusing
        # a name that is merely empty today would refuse a caption written
        # before the first fixture is in.
        row.field = str(data.get('field') or '').strip()[:FIELD_MAX]
    if 'colour' in data:
        row.colour = _colour(data.get('colour'))
    if 'font_size' in data:
        row.font_size = _whole(data.get('font_size'), 'font_size', *FONT_SIZE)
    if 'family' in data:
        row.family = _choice(str(data.get('family') or ''), 'family',
                             [v for v, _label in FAMILIES])
    if 'font_slot' in data:
        row.font_slot = _slot(data.get('font_slot'))
    if 'weight' in data:
        try:
            wanted = int(data.get('weight'))
        except (TypeError, ValueError):
            wanted = None
        row.weight = _choice(wanted, 'weight', [v for v, _label in WEIGHTS])
    if 'align' in data:
        row.align = _choice(str(data.get('align') or ''), 'align',
                            [v for v, _label in ALIGNMENTS])
    if 'position' in data:
        row.position = _choice(str(data.get('position') or ''), 'position',
                               presentation.POSITIONS)
    for axis in ('offset_x', 'offset_y'):
        if axis in data:
            setattr(row, axis, _whole(data.get(axis), axis,
                                      -presentation.OFFSET_LIMIT,
                                      presentation.OFFSET_LIMIT))
    if 'entry' in data:
        row.entry = _choice(str(data.get('entry') or ''), 'entry',
                            presentation.ENTRANCES)
    if 'exit' in data:
        row.exit = _choice(str(data.get('exit') or ''), 'exit',
                           presentation.EXITS)
    if 'delay_ms' in data:
        row.delay_ms = _whole(data.get('delay_ms'), 'delay_ms', *DELAY_MS)
    if 'duration_ms' in data:
        row.duration_ms = _whole(data.get('duration_ms'), 'duration_ms',
                                 *DURATION_MS)
    if 'order' in data:
        row.order = _whole(data.get('order'), 'order', *ORDER)
    if 'is_active' in data:
        row.is_active = data.get('is_active') is not False
    return row


def _asset(value, row):
    """The piece of media this layer draws, or a refusal.

    It has to belong to the same tournament or event the layer does. Without
    that check an operator could point a layer at another organiser's media by
    guessing a number, and the studio's library is per owner precisely so that
    cannot happen.
    """
    if value in (None, '', 0, '0'):
        return None
    from .models import StudioAsset
    try:
        wanted = int(value)
    except (TypeError, ValueError):
        raise LayerError('That is not a piece of media.', 'VALIDATION_FAILED',
                         'asset_id')

    found = StudioAsset.objects.filter(pk=wanted).first()
    if found is None:
        raise LayerError('That media is not in the library.', 'ASSET_NOT_FOUND',
                         'asset_id')

    # Whatever this layer is on, the media has to belong to the same thing.
    # StudioAsset carries its owner as two nullable columns exactly as the
    # layer does, so this compares the pair rather than a computed label.
    if row.element_id:
        session = row.element.session
        mine = (session.tournament_id, session.event_id)
    else:
        overlay = row.overlay
        mine = (overlay.tournament_id, overlay.event_id)

    if (found.tournament_id, found.event_id) != mine:
        raise LayerError('That media belongs to something else.',
                         'ASSET_NOT_YOURS', 'asset_id')
    return found


def is_empty(row):
    """Whether this layer would draw nothing at all.

    A layer with no words and no feed path is a row that exists, occupies a
    place in the list, and puts nothing on screen. Refused at the press that
    made it, because the operator who made it will look for it on air.

    An asset layer is empty when it points at no media, for the same reason.
    """
    if row.kind == 'asset':
        return row.asset_id is None
    return not (row.text or '').strip() and not (row.field or '').strip()


def serialize(row):
    """One layer, in the shape both halves agreed on.

    `is_active` is carried even though the feed only ever sends active layers:
    the console lists the ones that are switched off as well, and one serializer
    for both is one set of field names. See the one model per thing rule.
    """
    asset = row.asset if row.asset_id else None
    return {
        'id': row.id,
        'kind': row.kind,
        'asset_id': row.asset_id,
        # The URL and what it is, so a page draws it without a second request
        # and a console can show which one is chosen without holding the whole
        # library. Absent rather than guessed when the media has been deleted.
        'asset_url': (asset.file.url if asset and asset.file else ''),
        'asset_kind': getattr(asset, 'kind', '') if asset else '',
        'asset_name': getattr(asset, 'name', '') if asset else '',
        'width_px': row.width_px,
        'text': row.text,
        'field': row.field,
        'font_size': row.font_size,
        'colour': row.colour,
        'family': row.family,
        'font_slot': row.font_slot,
        'weight': row.weight,
        'align': row.align,
        'position': row.position,
        'offset_x': row.offset_x,
        'offset_y': row.offset_y,
        'entry': row.entry,
        'exit': row.exit,
        'delay_ms': row.delay_ms,
        'duration_ms': row.duration_ms,
        'order': row.order,
        'is_active': row.is_active,
    }


def serialize_many(rows):
    return [serialize(row) for row in rows]


def _ordered(rows):
    """Stable paint order: `order` first, then id.

    Two layers created in the same second have the same `order` until somebody
    changes one, and a list that reshuffled itself between two polls would swap
    two captions on air.
    """
    return sorted(rows, key=lambda row: (row.order, row.id or 0))


def active_of(rows):
    """The layers that are on, in paint order, out of an already loaded list.

    Takes a list rather than running a query, so a feed that has prefetched
    every element's layers does not go back to the database once per graphic.
    """
    return _ordered([row for row in rows if row.is_active])


def for_element(element):
    """Every layer on one studio graphic, on or off, in paint order."""
    if element is None:
        return []
    return list(element.text_layers.all())


def for_overlay(overlay):
    """Every ACTIVE layer on one uploaded file, in paint order.

    Active only, because this is what gets written into somebody else's page.
    A layer switched off is switched off everywhere, and the operator switching
    it off is watching a match.
    """
    if overlay is None:
        return []
    return active_of(list(overlay.text_layers.all()))


def stamp(elements):
    """A fingerprint of every layer on screen, for the studio feed's version.

    `_version()` in `views_studio.py` is what an element page compares before it
    redraws, so a layer edited under a stale stamp is a change nobody on air
    ever sees. That has already happened twice on this platform: once to squad
    depth, once to the broadcast look.

    A hash rather than a newest timestamp, because the stamp has to move on a
    REORDER and on a REMOVAL too, and neither of those touches the clock on the
    newest row.
    """
    rows = [[kind, (elements[kind] or {}).get('layers') or []]
            for kind in sorted(elements)]
    if not any(row[1] for row in rows):
        # Nothing has any layers, which is the common case. A constant keeps
        # the version short and keeps this out of the hash entirely.
        return '0'
    blob = json.dumps(rows, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:10]


def catalogue():
    """What a console offers, so it keeps no list of its own to drift.

    Values only, no labels. A label built in Python is a label the console
    cannot translate, and this platform has already shipped one of those to the
    CEO.
    """
    return {
        'families': [value for value, _label in FAMILIES],
        'weights': [value for value, _label in WEIGHTS],
        'alignments': [value for value, _label in ALIGNMENTS],
        'positions': list(presentation.POSITIONS),
        'entrances': list(presentation.ENTRANCES),
        'exits': list(presentation.EXITS),
        'offset_limit': presentation.OFFSET_LIMIT,
        'limits': {
            'font_size': {'min': FONT_SIZE[0], 'max': FONT_SIZE[1]},
            'delay_ms': {'min': DELAY_MS[0], 'max': DELAY_MS[1]},
            'duration_ms': {'min': DURATION_MS[0], 'max': DURATION_MS[1]},
            'order': {'min': ORDER[0], 'max': ORDER[1]},
            'text': TEXT_MAX,
            'field': FIELD_MAX,
        },
        'defaults': dict(DEFAULTS),
    }
