"""A shared place splits its prizes (P0, F4, money).

CEO, 16 October 2026, agreeing the wrong results go first: "these are fine."
Both semi-final losers are third now, so the 3rd and 4th place prizes are added
up and split equally between them, in whole coins, any leftover coin going one
each in their order (decision 5 of the design, the recommendation standing).
Before, one of them took the 3rd place prize and the other the 4th, by match
number.
"""
from django.test import TestCase

from vent_auth.models import UserWallet

from .models import PrizePayout, TournamentPrizeDistribution
from .services import prizes
from .tests import gen_bracket
from .tests_shared_places import play_chalk
from .tests_stage_engine import field


def finished(prize_table, n=8):
    t, creator, _regs = field(n, 'single_elimination')
    t.prize_type = 'distributed'
    t.save(update_fields=['prize_type'])
    gen_bracket(t, creator, 'registration')
    play_chalk(t)
    for position, amount in prize_table.items():
        TournamentPrizeDistribution.objects.create(tournament=t, position=position, prize=amount)
    UserWallet.objects.filter(user=creator).update(wallet_balance=100000)
    return t


def paid_by_seed(t):
    rows = prizes.resolve(t, list(TournamentPrizeDistribution.objects.filter(tournament=t)
                                  .order_by('position')))
    return {row['registration'].seed: (row['amount'], row['shared']) for row in rows}


class PrizeSplitTests(TestCase):
    def test_two_share_third_and_fourth(self):
        t = finished({1: 500, 2: 250, 3: 150, 4: 100})
        got = paid_by_seed(t)
        self.assertEqual(got[1], (500, None))
        self.assertEqual(got[2], (250, None))
        self.assertEqual(got[3], (125, '3-4'))
        self.assertEqual(got[4], (125, '3-4'))
        self.assertEqual(prizes.plan(t)['total'], 1000)

    def test_leftover_coins_go_in_order(self):
        t = finished({1: 500, 2: 250, 3: 100, 4: 60, 5: 40})
        # Three finishers level on third, written straight onto the places.
        t.registrations.filter(seed__in=(3, 4, 5)).update(final_position=3)
        got = paid_by_seed(t)
        self.assertEqual([got[s][0] for s in (3, 4, 5)], [67, 67, 66])
        self.assertEqual(got[3][1], '3-5')

    def test_band_past_the_last_prize(self):
        t = finished({1: 500, 2: 250, 3: 150})
        got = paid_by_seed(t)
        self.assertEqual((got[3][0], got[4][0]), (75, 75))
        plan = prizes.plan(t)
        self.assertEqual(plan['total'], 900)
        self.assertEqual(sorted(r['position'] for r in plan['rows']), [1, 2, 3, 4])

    def test_bands_cover_exactly_their_members(self):
        # Quarter-final losers share 5th: the band covers 5th to 8th.
        t = finished({1: 400, 2: 200, 3: 100, 4: 100, 5: 40, 6: 40, 7: 20, 8: 20})
        got = paid_by_seed(t)
        self.assertEqual(sorted(got[s][0] for s in (5, 6, 7, 8)), [30, 30, 30, 30])
        self.assertEqual(got[5][1], '5-8')

    def test_distribute_pays_the_split_and_a_rerun_pays_nothing_twice(self):
        t = finished({1: 500, 2: 250, 3: 150, 4: 100})
        prizes.distribute(t)
        paid = {p.winner_registration.seed: p.amount for p in PrizePayout.objects.filter(tournament=t)
                .select_related('winner_registration')}
        self.assertEqual(paid, {1: 500, 2: 250, 3: 125, 4: 125})
        prizes.distribute(t, force_recompute=True)
        self.assertEqual(PrizePayout.objects.filter(tournament=t).count(), 4)
