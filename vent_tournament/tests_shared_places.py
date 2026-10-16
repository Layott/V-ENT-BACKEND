"""Equal finishers share a place, and one-format tables rank by points (P0: F3, F4).

CEO, 16 October 2026, agreeing the wrong results go first: "these are fine."
Knockout losers of the same round were given different places by match number,
and prizes were paid by those places, so of two semi-final losers one got the
3rd place prize and the other the 4th for nothing either of them did. A table
played as the only format was ranked by wins and then registration order, not
by the points the table showed.
"""
from django.db import transaction
from django.test import TestCase

from . import options as opts
from . import stage_engine
from .models import BracketMatch
from .services import league
from .tests import gen_bracket
from .tests_stage_engine import add_stage, draw, field, play, play_out


def play_chalk(t):
    """Play every open match of a one-format bracket, the better seed winning."""
    guard = 0
    while True:
        guard += 1
        assert guard < 300, 'the bracket never finished'
        ready = list(BracketMatch.objects.filter(
            tournament=t, status='scheduled', participant_1__isnull=False,
            participant_2__isnull=False).select_related('participant_1', 'participant_2'))
        if not ready:
            break
        m = ready[0]
        if (m.participant_1.seed or 99) < (m.participant_2.seed or 99):
            play(m, 2, 0)
        else:
            play(m, 0, 2)
    t.refresh_from_db()


def places(t):
    return {r.seed: r.final_position for r in t.registrations.filter(status='confirmed')}


class KnockoutPlacesTests(TestCase):
    def test_semi_losers_share_third(self):
        t, creator, _regs = field(8, 'single_elimination')
        gen_bracket(t, creator, 'registration')
        play_chalk(t)
        self.assertEqual(t.status, 'completed')
        got = places(t)
        self.assertEqual((got[1], got[2]), (1, 2))
        self.assertEqual((got[3], got[4]), (3, 3))
        self.assertEqual(sorted(got[s] for s in (5, 6, 7, 8)), [5, 5, 5, 5])

    def test_a_third_place_match_still_decides(self):
        t, creator, _regs = field(8, 'single_elimination')
        t.options = opts.clean({'third_place_match': True})
        t.save(update_fields=['options'])
        gen_bracket(t, creator, 'registration')
        play_chalk(t)
        got = places(t)
        self.assertEqual(sorted((got[3], got[4])), [3, 4])

    def test_double_elimination_places(self):
        t, creator, _regs = field(8, 'double_elimination')
        gen_bracket(t, creator, 'registration')
        play_chalk(t)
        self.assertEqual(t.status, 'completed')
        self.assertEqual(sorted(places(t).values()), [1, 2, 3, 4, 5, 5, 7, 7])


class OneFormatTableTests(TestCase):
    def test_one_format_table_ranks_by_points(self):
        # A and B both win once; B draws twice where A loses twice, so B has
        # more points. Ranked by wins then registration, A was above B.
        t, creator, regs = field(4, 'round_robin')
        gen_bracket(t, creator, 'registration')
        by_seed = {r.seed: r for r in regs}
        results = {(1, 2): (0, 1), (1, 3): (0, 1), (1, 4): (1, 0),
                   (2, 3): (1, 1), (2, 4): (1, 1), (3, 4): (1, 0)}
        for m in BracketMatch.objects.filter(tournament=t).select_related('participant_1', 'participant_2'):
            a, b = m.participant_1.seed, m.participant_2.seed
            s1, s2 = results[(a, b)] if (a, b) in results else tuple(reversed(results[(b, a)]))
            if s1 == s2:
                m.draw_allowed = True
                m.save(update_fields=['draw_allowed'])
            play(m, s1, s2)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        got = places(t)
        table = {row['registration_id']: row['position'] for row in league.team_table(t)}
        self.assertEqual(got[2], 2, 'B has 5 points to A\'s 3')
        self.assertEqual(got[1], 3)
        self.assertEqual({by_seed[s].id: p for s, p in got.items()}, table)


class StagedPlacesTests(TestCase):
    def test_group_thirds_share_a_place_in_a_staged_tournament(self):
        t, creator, _regs = field(8, 'round_robin')
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        add_stage(t, 1, 'single_elimination')
        draw(groups, creator)
        play_out(groups)
        with transaction.atomic():
            stage_engine.advance(groups, creator)
        playoff = t.stages.get(order=1)
        play_out(playoff)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        self.assertEqual(sorted(places(t).values()), [1, 2, 3, 3, 5, 5, 7, 7])
