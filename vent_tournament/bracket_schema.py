"""A bracket described by its matches, checked, and drawn (P3 W2).

CEO, 16 October 2026, inbox 484: "i also need to implement new brackets,
formats and modes", approved with the design. Section 11: "Custom bracket
schema (P3 W2): a JSON description of rounds, matches and slot sources,
compiled to rows and checked (every slot has one source, each winner and loser
goes at most once, no cycles, every exit has a place). The presets are schemas
produced by a function for N entrants, so a new preset is a function, not a new
engine."

A schema is {"matches": [...]}, each match

    {"id": "C", "round": 2, "side": "winners", "branch": "", "final": true,
     "winner_place": 1, "loser_place": 2,
     "slots": [{"winner": "A"}, {"winner": "B"}]}

and a slot names exactly one source: {"seed": n}, {"winner": id},
{"loser": id} or {"bye": true}. A seed above the number of entrants is a bye
at the draw.

Organisers write it in toornament's line language (research section 4.7):

    #round 1
    A=[1,4]
    B=[2,3]
    #round 2
    C=[W:A,W:B]! >1,2      the final; its winner is first, its loser second
    [PL]
    D=[L:A,L:B] >3,4       a match for third, on the placement side

`[WB]`, `[LB]`, `[GF]` and `[PL]` set the side of the matches below them,
`@east` their branch (`@` alone clears it), `-` is a bye, and rounds left out
are worked out from the sources. Every refusal is a code with the match or line
it is about, never a sentence, so the screen can say it in the organiser's
language.
"""
import re

SIDES = {'WB': 'winners', 'LB': 'losers', 'GF': 'grand_final', 'PL': 'placement'}
SIDE_VALUES = tuple(SIDES.values())
SOURCES = ('seed', 'winner', 'loser', 'bye')
MAX_MATCHES = 512
MAX_ROUND = 64
MAX_PLACE = 1024

_ID = r'[A-Za-z0-9_]{1,12}'
_MATCH = re.compile(r'^(%s)\s*=\s*\[([^\]]*)\]\s*(!)?\s*(?:>\s*(-|\d+)\s*,\s*(-|\d+))?\s*$' % _ID)
_ROUND = re.compile(r'^#round\s+(\d+)$', re.I)
_SIDE = re.compile(r'^\[(\w+)\]$')
_BRANCH = re.compile(r'^@([a-z0-9_]{0,20})$')
_ID_ONLY = re.compile(r'^%s$' % _ID)


class SchemaError(ValueError):
    def __init__(self, code, match=None, line=None):
        super().__init__(code)
        self.code = code
        self.match = match
        self.line = line


def _opponent(token, line):
    token = token.strip()
    if token == '-':
        return {'bye': True}
    if token.isdigit():
        return {'seed': int(token)}
    kind, _, ref = token.partition(':')
    if kind.upper() in ('W', 'L') and _ID_ONLY.match(ref):
        return {'winner' if kind.upper() == 'W' else 'loser': ref}
    raise SchemaError('SCHEMA_LINE', line=line)


def parse(text):
    """The schema written in the line language, as a dict. Raises SchemaError
    ('SCHEMA_LINE', line=n) on a line it cannot read."""
    matches = []
    side, branch, current_round, explicit = 'winners', '', 1, False
    for number, raw in enumerate(str(text or '').splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('//'):
            continue
        found = _ROUND.match(line)
        if found:
            current_round, explicit = int(found.group(1)), True
            continue
        found = _SIDE.match(line)
        if found:
            if found.group(1).upper() not in SIDES:
                raise SchemaError('SCHEMA_LINE', line=number)
            side = SIDES[found.group(1).upper()]
            continue
        found = _BRANCH.match(line)
        if found:
            branch = found.group(1)
            continue
        found = _MATCH.match(line)
        if not found:
            raise SchemaError('SCHEMA_LINE', line=number)
        ident, inside, final, win_place, lose_place = found.groups()
        tokens = inside.split(',')
        if len(tokens) != 2:
            raise SchemaError('SCHEMA_LINE', line=number)
        matches.append({
            'id': ident,
            'round': current_round if explicit else None,
            'side': side,
            'branch': branch,
            'final': bool(final),
            'winner_place': int(win_place) if win_place and win_place != '-' else None,
            'loser_place': int(lose_place) if lose_place and lose_place != '-' else None,
            'slots': [_opponent(t, number) for t in tokens],
        })
    if not explicit:
        # Rounds left out: one after the latest match a match is fed by.
        rounds = {}
        for m in matches:
            fed = [rounds.get(s.get('winner') or s.get('loser'), 0) for s in m['slots']]
            rounds[m['id']] = max(fed + [0]) + 1
            m['round'] = rounds[m['id']]
    return {'matches': matches}


def _value(match, key, low, high, allow_none=False):
    value = match.get(key)
    if value is None and allow_none:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise SchemaError('SCHEMA_VALUE', match=match.get('id'))
    return value


def check(schema):
    """Refuse a schema that cannot be drawn and played; return how many seeds
    it holds. Codes, in the order they are checked: SCHEMA_VALUE,
    SCHEMA_EMPTY, SCHEMA_DUPLICATE_ID, SCHEMA_TWO_SLOTS, SCHEMA_SLOT_SOURCE,
    SCHEMA_UNKNOWN_MATCH, SCHEMA_ROUND_ORDER, SCHEMA_WINNER_TWICE,
    SCHEMA_LOSER_TWICE, SCHEMA_CYCLE, SCHEMA_EMPTY_MATCH, SCHEMA_SEEDS,
    SCHEMA_TWO_FINALS, SCHEMA_NO_FINAL, SCHEMA_EXIT_NO_PLACE."""
    if not isinstance(schema, dict) or not isinstance(schema.get('matches'), list):
        raise SchemaError('SCHEMA_VALUE')
    matches = schema['matches']
    if not matches:
        raise SchemaError('SCHEMA_EMPTY')
    if len(matches) > MAX_MATCHES:
        raise SchemaError('SCHEMA_VALUE')
    by_id = {}
    for m in matches:
        if not isinstance(m, dict) or not isinstance(m.get('id'), str) or not _ID_ONLY.match(m['id']):
            raise SchemaError('SCHEMA_VALUE')
        if m['id'] in by_id:
            raise SchemaError('SCHEMA_DUPLICATE_ID', match=m['id'])
        _value(m, 'round', 1, MAX_ROUND)
        if m.get('side', 'winners') not in SIDE_VALUES:
            raise SchemaError('SCHEMA_VALUE', match=m['id'])
        _value(m, 'winner_place', 1, MAX_PLACE, allow_none=True)
        _value(m, 'loser_place', 1, MAX_PLACE, allow_none=True)
        by_id[m['id']] = m

    seeds = []
    used = {'winner': {}, 'loser': {}}
    for m in matches:
        slots = m.get('slots')
        if not isinstance(slots, list) or len(slots) != 2:
            raise SchemaError('SCHEMA_TWO_SLOTS', match=m['id'])
        for slot in slots:
            keys = [k for k in SOURCES if isinstance(slot, dict) and k in slot]
            if len(keys) != 1 or len(slot) != 1:
                raise SchemaError('SCHEMA_SLOT_SOURCE', match=m['id'])
            kind = keys[0]
            if kind == 'seed':
                if not isinstance(slot['seed'], int) or slot['seed'] < 1:
                    raise SchemaError('SCHEMA_VALUE', match=m['id'])
                seeds.append(slot['seed'])
            elif kind in ('winner', 'loser'):
                ref = slot[kind]
                if ref not in by_id:
                    raise SchemaError('SCHEMA_UNKNOWN_MATCH', match=m['id'])
                if by_id[ref]['round'] >= m['round']:
                    raise SchemaError('SCHEMA_ROUND_ORDER', match=m['id'])
                if ref in used[kind]:
                    raise SchemaError('SCHEMA_WINNER_TWICE' if kind == 'winner' else 'SCHEMA_LOSER_TWICE',
                                      match=m['id'])
                used[kind][ref] = m['id']
        if all('bye' in s for s in slots):
            raise SchemaError('SCHEMA_EMPTY_MATCH', match=m['id'])
    if _has_cycle(by_id):
        raise SchemaError('SCHEMA_CYCLE')
    if sorted(seeds) != list(range(1, len(seeds) + 1)):
        raise SchemaError('SCHEMA_SEEDS')

    finals = [m for m in matches if m.get('final')]
    if len(finals) > 1:
        raise SchemaError('SCHEMA_TWO_FINALS', match=finals[1]['id'])
    if not finals and not any(m.get('winner_place') == 1 for m in matches):
        raise SchemaError('SCHEMA_NO_FINAL')
    for m in matches:
        if m['id'] not in used['winner'] and not m.get('final') and m.get('winner_place') is None:
            raise SchemaError('SCHEMA_EXIT_NO_PLACE', match=m['id'])
    return len(seeds)


def _has_cycle(by_id):
    """Rounds already order every source before its match; this is the guard
    for anything that slips past that rule."""
    state = {}

    def visit(ident):
        state[ident] = 1
        for slot in by_id[ident]['slots']:
            ref = slot.get('winner') or slot.get('loser')
            if ref is None:
                continue
            if state.get(ref) == 1 or (state.get(ref) is None and visit(ref)):
                return True
        state[ident] = 2
        return False

    return any(state.get(i) is None and visit(i) for i in by_id)


def schema_for(settings, entrants):
    """The schema a custom bracket stage is drawn from: its preset's for this
    many entrants, else the one the organiser wrote."""
    from . import bracket_presets
    settings = settings or {}
    preset = settings.get('preset')
    if preset:
        build = bracket_presets.PRESETS.get(preset)
        if build is None:
            raise SchemaError('SCHEMA_EMPTY')
        return build(entrants)
    return settings.get('schema') or {'matches': []}


def compile_into(tournament, stage, ordered, settings):
    """Draw `ordered` (best seed first) as the stage's schema.

    Every match is created with how it is played (the stage settings), its
    side, branch, round and the places it states; every winner and loser
    source is written through `match_slots.link`, the one link writer; seeds
    are seated, a seed above the field is a bye, and a match left with one
    side settles as a walkover the way every generator's round one does.
    """
    from django.utils import timezone
    from . import match_slots
    from .services import advance
    from .services.bracket import BracketError, _new_match

    schema = schema_for(settings, len(ordered))
    try:
        seeds = check(schema)
    except SchemaError as exc:
        raise BracketError(exc.code.lower(), 'The bracket schema cannot be drawn.')
    if len(ordered) > seeds:
        raise BracketError('schema_too_small', 'The schema holds %s.' % seeds)
    matches = sorted(schema['matches'], key=lambda m: m['round'])
    rounds_total = max(m['round'] for m in matches)
    numbers, rows = {}, {}
    for m in matches:
        key = (m.get('side') or 'winners', m['round'])
        numbers[key] = numbers.get(key, 0) + 1
        rows[m['id']] = _new_match(
            tournament, stage, settings, m['round'], numbers[key], key[0],
            rounds_total=rounds_total, is_final=bool(m.get('final')),
            branch=m.get('branch') or '', winner_place=m.get('winner_place'),
            loser_place=m.get('loser_place'))
    settled = []
    for m in matches:
        match = rows[m['id']]
        seated, waiting = [], False
        for index, slot in enumerate(m['slots'], start=1):
            if 'winner' in slot or 'loser' in slot:
                outcome = 'winner' if 'winner' in slot else 'loser'
                match_slots.link(rows[slot[outcome]], outcome, match, index)
                waiting = True
            elif 'seed' in slot and slot['seed'] <= len(ordered):
                setattr(match, 'participant_%d' % index, ordered[slot['seed'] - 1])
                seated.append(index)
            else:
                match_slots.mark_bye(match, index)
        if seated:
            match.save(update_fields=['participant_%d' % i for i in seated])
        if not waiting and len(seated) < 2:
            # Both seats were seeds and one or both are byes: settled now.
            match.status = 'bye'
            if seated:
                match.winner = getattr(match, 'participant_%d' % seated[0])
                match.completed_at = timezone.now()
                match.save(update_fields=['status', 'winner', 'completed_at'])
            else:
                match.save(update_fields=['status'])
            settled.append(match)
    for match in settled:
        advance.cascade(match)
    branches = {}
    for m in matches:
        branches[m.get('branch') or ''] = branches.get(m.get('branch') or '', 0) + 1
    return {
        'rounds_count': rounds_total,
        'matches_created': len(matches),
        'structure_summary': [{'branch': b, 'matches': c} for b, c in sorted(branches.items())],
    }
