"""Every format draws exactly as it did before P1a (design section 17, golden draws).

CEO, 16 October 2026, approving the bracket structure design: "i aprove the
design". Its anti-goals include "A live tournament that draws differently the
day after a deploy". P1a rewires how every generator links its matches, so the
draws are recorded here on the code before the change, one canonical record
per match, and compared on every run after it.

Rewrite the record only when a draw is MEANT to change, and say why in the
commit:

    GOLDEN_UPDATE=1 python manage.py test vent_tournament.tests_golden_draws
"""
import json
import os

from django.db import transaction
from django.test import TestCase

from . import options as opts
from . import stage_engine
from .tests import gen_bracket
from .tests_stage_engine import add_stage, field

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testdata', 'golden_draws.json')

RANGE = lambda a, b: list(range(a, b + 1))  # noqa: E731

# (name, bracket_type, options, sizes)
VARIANTS = [
    ('se', 'single_elimination', {}, RANGE(2, 17) + [24, 31, 32, 33, 48, 64]),
    ('se_third', 'single_elimination', {'third_place_match': True}, [4, 5, 8, 11, 16]),
    ('se_every', 'single_elimination', {'every_place': True}, [4, 6, 8, 13, 16]),
    ('de', 'double_elimination', {}, RANGE(4, 17) + [24, 32, 33, 64]),
    ('rr', 'round_robin', {}, RANGE(3, 9)),
    ('rr_legs', 'round_robin', {'legs': 2}, [4, 5]),
    ('league', 'league', {}, [4, 6]),
    ('swiss', 'swiss', {}, [4, 7, 8, 16]),
    ('gsl', 'gsl', {}, [8, 12, 16]),
    ('stepladder', 'stepladder', {}, [3, 4, 6]),
    ('page', 'page_playoff', {}, [4]),
    ('wso', 'winner_stays_on', {}, [3, 5]),
]

# (entrants, groups): groups of a round robin into a knockout, drawn as stages.
STAGED = [(8, 2), (12, 3), (16, 4)]

# Settings only a stage offers: a one-format double elimination keeps the
# decisive grand final, so the reset is drawn on a stage.
# (name, format, settings, sizes)
STAGE_ONLY = [
    ('de_reset', 'double_elimination', {'grand_final': 'reset'}, [4, 8, 11, 16]),
]


def _key(m):
    return '%s|%s|%s|%s' % (m.bracket_side, m.group_number or 0, m.round_number, m.match_number)


def snapshot(t):
    """One canonical record per match, in playing order, with no database ids."""
    matches = list(t.bracket_matches.select_related(
        'participant_1', 'participant_2', 'winner').order_by(
        'bracket_side', 'group_number', 'round_number', 'match_number'))
    keys = {m.id: _key(m) for m in matches}
    assert len(set(keys.values())) == len(matches), 'two matches share a key'
    seed = lambda reg: reg.seed if reg is not None else None  # noqa: E731
    return [{
        'key': keys[m.id],
        'is_final': m.is_final,
        'best_of': m.best_of,
        'legs': m.legs,
        'draw_allowed': m.draw_allowed,
        'winner_place': m.winner_place,
        'loser_place': m.loser_place,
        'status': m.status,
        'p1': seed(m.participant_1),
        'p2': seed(m.participant_2),
        'winner': seed(m.winner),
        'w_to': keys.get(m.winner_to_match_id),
        'w_slot': m.winner_to_slot,
        'l_to': keys.get(m.loser_to_match_id),
        'l_slot': m.loser_to_slot,
    } for m in matches]


def drawn(name, bracket_type, options, n):
    """A tournament of `n` drawn as one format with these options."""
    t, creator, _regs = field(n, bracket_type)
    if options:
        t.options = opts.clean(options)
        t.save(update_fields=['options'])
    gen_bracket(t, creator, 'registration')
    return t


def drawn_staged(n, groups):
    t, creator, _regs = field(n, 'round_robin')
    first = add_stage(t, 0, 'round_robin', advances=2, groups=groups)
    add_stage(t, 1, 'single_elimination')
    with transaction.atomic():
        stage_engine.draw(first, creator)
    return t


def drawn_stage(fmt, settings, n):
    """A tournament of `n` whose one stage is drawn with these settings."""
    t, creator, _regs = field(n, fmt)
    stage = add_stage(t, 0, fmt, settings=settings)
    with transaction.atomic():
        stage_engine.draw(stage, creator)
    return t


def every_draw():
    """{variant name: {size: snapshot}} for every variant here."""
    out = {}
    for name, bracket_type, options, sizes in VARIANTS:
        out[name] = {str(n): snapshot(drawn(name, bracket_type, options, n)) for n in sizes}
    out['groups'] = {'%s/%s' % (n, g): snapshot(drawn_staged(n, g)) for n, g in STAGED}
    for name, fmt, settings, sizes in STAGE_ONLY:
        out[name] = {str(n): snapshot(drawn_stage(fmt, settings, n)) for n in sizes}
    return out


class GoldenDrawTests(TestCase):
    def test_every_draw_matches_the_record(self):
        got = every_draw()
        if os.environ.get('GOLDEN_UPDATE') == '1':
            os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
            with open(GOLDEN, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(got, fh, sort_keys=True, indent=1)
                fh.write('\n')
            return
        with open(GOLDEN, encoding='utf-8') as fh:
            golden = json.load(fh)
        self.assertEqual(sorted(got), sorted(golden), 'the variants differ from the record')
        for name in golden:
            self.assertEqual(sorted(got[name]), sorted(golden[name]), '%s: the sizes differ' % name)
            for size in golden[name]:
                with self.subTest(variant=name, size=size):
                    self.assertEqual(got[name][size], golden[name][size], '%s %s' % (name, size))
