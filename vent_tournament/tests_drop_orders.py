"""Losers drop into the lower bracket in an order that avoids rematches (P0, F1).

CEO, 16 October 2026, agreeing the four wrong-result faults go first: "these
are fine." The lower bracket took the upper bracket's losers in natural order,
so under expected results about half of its matches were rematches: 3 of 6 at
8 entrants, 31 of 62 at 64 (docs/research/de_rematch.py). The orders
brackets-manager publishes, replayed in docs/research/de_orders.py, leave the
few that no order can avoid.

Every bracket here is played the way the favourite always wins, and a lower
bracket match whose pair already met is counted.
"""
from django.test import TestCase

from .models import BracketMatch
from .services import bracket as bracket_service
from .tests import gen_bracket
from .tests_stage_engine import field, play


def chalk_rematches(n):
    t, creator, regs = field(n, 'double_elimination')
    gen_bracket(t, creator, 'registration')
    met, rematches, guard = set(), 0, 0
    while True:
        guard += 1
        assert guard < 400, 'the bracket never finished'
        ready = list(BracketMatch.objects.filter(
            tournament=t, status='scheduled', participant_1__isnull=False,
            participant_2__isnull=False).select_related('participant_1', 'participant_2')
            .order_by('bracket_side', 'round_number', 'match_number'))
        if not ready:
            break
        m = ready[0]
        pair = frozenset((m.participant_1_id, m.participant_2_id))
        if m.bracket_side == 'losers' and pair in met:
            rematches += 1
        met.add(pair)
        if (m.participant_1.seed or 99) < (m.participant_2.seed or 99):
            play(m, 2, 0)
        else:
            play(m, 0, 2)
    t.refresh_from_db()
    return rematches, t


class DropOrderTests(TestCase):
    def test_rematches_follow_the_research(self):
        found = {n: chalk_rematches(n)[0] for n in (4, 8, 16, 32, 64)}
        self.assertEqual(found, {4: 1, 8: 1, 16: 1, 32: 3, 64: 3})

    def test_an_odd_field_still_finishes(self):
        for n in (5, 11):
            _rematches, t = chalk_rematches(n)
            self.assertEqual(t.status, 'completed', 'a field of %d never finished' % n)

    def test_existing_draws_are_untouched(self):
        t, creator, _regs = field(8, 'double_elimination')
        gen_bracket(t, creator, 'registration')
        before = list(BracketMatch.objects.filter(tournament=t).order_by('id')
                      .values_list('id', 'loser_to_match_id', 'loser_to_slot'))
        self.assertEqual(bracket_service.drop_order(8)[1], 'reverse')
        after = list(BracketMatch.objects.filter(tournament=t).order_by('id')
                     .values_list('id', 'loser_to_match_id', 'loser_to_slot'))
        self.assertEqual(before, after)

    def test_every_order_is_a_permutation(self):
        items = list(range(16))
        for name in ('natural', 'reverse', 'half_shift', 'reverse_half_shift', 'pair_flip'):
            self.assertEqual(sorted(bracket_service.reorder(name, items)), items, name)
        self.assertEqual(bracket_service.reorder('pair_flip', [1, 2, 3, 4]), [2, 1, 4, 3])
        self.assertEqual(bracket_service.reorder('reverse_half_shift', [1, 2, 3, 4]), [2, 1, 4, 3])
        self.assertEqual(bracket_service.reorder('half_shift', [1, 2, 3, 4]), [3, 4, 1, 2])
