"""How a graphic arrives, how it leaves, and what stays behind.

CEO, 3 September 2026: "Then a way to set options generally for the overlays
like maybe usong a trigger button, or setting the entry animations for
specific elemnts or if the bg of that overlay should not leave or load in and
just be present etc and still have the same options available for each
individual overlay."

Two levels, and the second is the point: a broadcast sets its house style once,
and any single graphic may differ. So the session carries `defaults` and an
element carries `options`; what the element page is told is the two merged,
already resolved, so the page never has to know which level a value came from.

The vocabulary is deliberately small. Every value here is something an operator
can see the effect of in one press, and nothing here loops: a graphic that
breathes on air is a graphic the viewer looks at instead of the match.
"""

ENTRANCES = ['rise', 'fade', 'slide_left', 'slide_right', 'none']
EXITS = ['fade', 'drop', 'slide_left', 'slide_right', 'none']

#: Where on the frame a graphic sits.
#:
#: CEO, 4 September 2026: "SHould also be able to move the position of
#: overlays, whether they load in at the centre bottom or center top, or top
#: right or top left or middle or middle right, etc. this mostly affect lower
#: thirds."
#:
#: A nine point grid, which is what every broadcast switcher offers and what
#: the CEO listed. `as_designed` is FIRST and is the default, and that matters
#: more than the rest of this list put together: it means the graphic sits
#: where its own design put it. A default of `bottom_left` would silently move
#: every graphic already on air the moment this shipped.
POSITIONS = [
    'as_designed',
    'top_left', 'top_centre', 'top_right',
    'middle_left', 'centre', 'middle_right',
    'bottom_left', 'bottom_centre', 'bottom_right',
]

#: How far a positioned graphic may be nudged off its anchor, in pixels at
#: 1920x1080. Enough to clear a scoreboard bug or a broadcaster's safe area,
#: not enough to put a graphic off the frame by accident.
OFFSET_LIMIT = 800

#: The house style when nobody has said otherwise.
DEFAULTS = {
    'entry': 'rise',
    'exit': 'fade',
    # The surface stays on screen when the graphic is taken off; only its
    # content leaves. For a lower third that sits under a caster all show, or
    # a background plate that should not flash on every change.
    'hold': False,
    # How long a clip or a card stays before it takes itself off. 0 means it
    # stays until the operator takes it off.
    'duration_ms': 0,
    # Where it sits. See POSITIONS: the default moves nothing.
    'position': 'as_designed',
    # A nudge off that anchor, in pixels at 1920x1080. Positive x is right,
    # positive y is down, the way the screen is measured everywhere else.
    'offset_x': 0,
    'offset_y': 0,
}

_BOOLEAN = {'hold'}
_WHOLE = {'duration_ms'}
_SIGNED = {'offset_x', 'offset_y'}


class PresentationError(ValueError):
    """An option that cannot be stored, with a sentence saying which."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def clean(raw):
    """Validated options, or raise. Unknown keys are refused rather than kept.

    Refused, not dropped: an operator who set `entrance` and saw it ignored
    would have no way to tell a typo from a feature that does not work, and the
    first thing they would do is set it again.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PresentationError('Presentation options are a set of named values.')

    out = {}
    for key, value in raw.items():
        if key not in DEFAULTS:
            raise PresentationError(
                'There is no presentation option called %s.' % key, key)
        if key in _BOOLEAN:
            out[key] = bool(value)
        elif key in _WHOLE:
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise PresentationError('%s is a whole number of milliseconds.' % key, key)
            if number < 0 or number > 600000:
                raise PresentationError(
                    '%s is between 0 and 600000 milliseconds.' % key, key)
            out[key] = number
        elif key == 'entry':
            if value not in ENTRANCES:
                raise PresentationError(
                    'An entry is one of: %s.' % ', '.join(ENTRANCES), key)
            out[key] = value
        elif key in _SIGNED:
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise PresentationError(
                    '%s is a whole number of pixels.' % key, key)
            if abs(number) > OFFSET_LIMIT:
                raise PresentationError(
                    '%s is between -%d and %d pixels.'
                    % (key, OFFSET_LIMIT, OFFSET_LIMIT), key)
            out[key] = number
        elif key == 'exit':
            if value not in EXITS:
                raise PresentationError(
                    'An exit is one of: %s.' % ', '.join(EXITS), key)
            out[key] = value
        elif key == 'position':
            if value not in POSITIONS:
                raise PresentationError(
                    'A position is one of: %s.' % ', '.join(POSITIONS), key)
            out[key] = value
    return out


def resolve(session_defaults, element_options):
    """What this graphic actually does, with the element's word winning."""
    out = dict(DEFAULTS)
    out.update(clean(session_defaults or {}))
    out.update(clean(element_options or {}))
    return out


def catalogue():
    """What a console offers, so it keeps no list of its own to drift."""
    return {
        'entrances': ENTRANCES,
        'exits': EXITS,
        'positions': POSITIONS,
        'offset_limit': OFFSET_LIMIT,
        'defaults': dict(DEFAULTS),
    }
