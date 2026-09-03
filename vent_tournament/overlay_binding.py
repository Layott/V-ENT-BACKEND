"""What an uploaded HTML file can be driven by, and what it cannot.

CEO, 29 August 2026: can any HTML file uploaded to V-ENT have its player images,
team logos and standings filled from a live tournament.

The honest answer has an edge, and the edge is the whole design:

**An arbitrary HTML file cannot be driven, and no amount of cleverness changes
that.** A designer hands over a file containing `<div>ALIEN X</div>`. Nothing
can know whether that is a team name, a sponsor, or a word in the artwork.
Guessing produces an overlay that rewrites the wrong text on air, which is worse
than one that does nothing.

**But the marking is trivial, and a prompt can add it.** One attribute:

    <div data-vent="team.name"></div>
    <img data-vent-src="team.logo">
    <tbody data-vent-repeat="standings">
      <tr><td data-vent="place"></td><td data-vent="name"></td><td data-vent="won"></td></tr>
    </tbody>

That is a two-minute edit on an existing design, it survives being opened in any
editor, and it is exactly the kind of change a person can ask an assistant to
make. `docs/OVERLAY-PROMPT.md` is the prompt.

So an upload lands in one of three states, and this module says which:

- `marked`   - carries `data-vent` attributes. Driven by the generic runtime.
- `scripted` - defines `window.build()` and reads `window.VENT`. Driven by
               calling `build()` again on new data. More power, more work.
- `none`     - neither. It will render, and nothing on it will ever change.

The third is reported at upload with the reason, rather than accepted quietly.
An overlay that is silently static is discovered on air.
"""

import re

MARKED = 'marked'
SCRIPTED = 'scripted'
NONE = 'none'

#: `data-vent="team.name"`, `data-vent-src="team.logo"`, `data-vent-repeat="standings"`.
_ATTR = re.compile(
    r'data-vent(?:-(?:src|repeat|attr|show))?\s*=\s*["\']([^"\']+)["\']',
    re.I)

#: An overlay that drives itself: it defines the function the runtime calls.
_BUILD = re.compile(r'window\.build\s*=|function\s+build\s*\(', re.I)

#: Things that must not be in a file served from our own domain.
_DANGER = [
    (re.compile(r'<\s*iframe', re.I), 'an iframe'),
    (re.compile(r'\bdocument\.cookie\b', re.I), 'a read of document.cookie'),
    (re.compile(r'\blocalStorage\b', re.I), 'localStorage'),
    (re.compile(r'\bnavigator\.credentials\b', re.I), 'the credentials API'),
    (re.compile(r'\bXMLHttpRequest\b|\bfetch\s*\(', re.I), 'its own network calls'),
]


def font_problems(markup):
    """Fonts this file asks for that will not arrive.

    Kept as a name because tests and callers use it, but the work lives in
    `overlay_audit`, which checks fonts alongside every other thing a file can
    get wrong silently. Two copies of "will this URL resolve" is exactly the
    shape this codebase keeps paying for.
    """
    from . import overlay_audit

    text = markup if isinstance(markup, str) else markup.decode('utf-8', 'replace')
    return [src for src in overlay_audit.FONT_SRC.findall(text)
            if overlay_audit._is_local(src)]


def inspect(markup):
    """What this file is, what it binds to, and anything worth refusing over.

    Returns `(binding, fields, warnings)`.
    """
    text = markup if isinstance(markup, str) else markup.decode('utf-8', 'replace')

    fields = sorted({m.group(1).strip() for m in _ATTR.finditer(text)})
    scripted = bool(_BUILD.search(text)) or 'window.VENT' in text

    if fields:
        binding = MARKED
    elif scripted:
        binding = SCRIPTED
    else:
        binding = NONE

    warnings = []
    if binding == NONE:
        warnings.append(
            'Nothing in this file is marked as data, so it will look right and '
            'never change. Add data-vent="team.name" to the elements that '
            'should follow the tournament.')

    for pattern, what in _DANGER:
        if pattern.search(text):
            warnings.append(
                'This file contains %s. It still works, but the overlay is '
                'served from v-ent.co, so anything it does is done as v-ent.co.'
                % what)

    return binding, fields, warnings


#: Every name the runtime knows how to fill. Reported to the uploader so a typo
#: in an attribute is caught at upload rather than on air.
KNOWN_FIELDS = [
    'tournament.title', 'tournament.game', 'tournament.logo',
    'team.tag', 'team.name', 'team.logo', 'team.place',
    'team.played', 'team.won', 'team.lost',
    'team.points_for', 'team.points_against',
    'player.ign', 'player.id', 'player.img',
    # Inside a repeat, the row's own fields are addressed without a prefix.
    'place', 'tag', 'name', 'logo', 'played', 'won', 'lost',
    'points_for', 'points_against', 'ign', 'id', 'img', 'website',
    # Inside a repeat over the studio's media library.
    'kind', 'url', 'slot', 'team_tag', 'player', 'pictures',
]

#: What a `data-vent-repeat` may repeat over.
KNOWN_REPEATS = ['standings', 'teams', 'players', 'live', 'sponsors',
                 'assets', 'pictures', 'programme']


def unknown_fields(fields, known=None):
    """Names in the file that the runtime will not be able to fill.

    `known` is the vocabulary to judge against, for a caller that has a more
    exact one: the upload path knows whether this is a tournament or an event
    and can refuse an event name on a tournament overlay. It defaults to
    everything this module knows.

    Every caller must come through here. The upload path used to do its own
    set membership against its own list, so when `asset.<name>` was allowed
    here it went on telling people their asset names would stay empty, on the
    one screen where anybody reads that warning.
    """
    allowed = set(known) if known is not None else set(KNOWN_FIELDS) | set(KNOWN_REPEATS)
    out = []
    for field in fields:
        bare = field.split('|')[0].strip()
        if bare in allowed:
            continue
        # `asset.<name>` is whatever the organiser assigned that name to in
        # the studio, so the name half cannot be known here. A designer writes
        # `data-vent-src="asset.hero"` and the organiser decides later what
        # hero is; reporting it as undriveable at upload would be wrong, and
        # would push them to edit the file instead of uploading a picture.
        if bare.startswith('asset.') and len(bare) > len('asset.'):
            continue
        out.append(field)
    return out
