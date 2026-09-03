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
}

_BOOLEAN = {'hold'}
_WHOLE = {'duration_ms'}


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
        elif key == 'exit':
            if value not in EXITS:
                raise PresentationError(
                    'An exit is one of: %s.' % ', '.join(EXITS), key)
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
        'defaults': dict(DEFAULTS),
    }
