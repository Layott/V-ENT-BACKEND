"""Tournaments pay organisers a share, and V-ENT takes a tournament fee.

CEO, 13 September 2026, asked whether tournaments should pay organisers a share
of entry fees so the tournament fee on the dashboard means something: "i want
it". Until then an entry fee left the player's wallet and reached nobody, and
prizes were minted out of nothing.

What these hold: a paid entry writes the organiser's take and the platform's
fee on the ledger; the organiser chooses who bears the fee; a refund returns
what the player actually paid; prizes come out of the pool the entries built,
with the organiser's own wallet covering any shortfall first; and the organiser
is paid what is left, once.
"""
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import AdminSetting, Transaction, UserWallet
from vent_event import ledger
from vent_event.models import EventLedgerEntry

from .models import PrizePayout, TournamentPrizeDistribution, TournamentRegistration
from .services import prizes as prize_service
from .tests import PrizeDistributionTests, client_for, make_tournament, make_user

PIN = '2468'


def a_player(i, coins):
    user = make_user(i, kyc=True)
    UserWallet.objects.filter(user=user).update(
        wallet_balance=coins, pin_hash=make_password(PIN))
    return user


def balance(user):
    return UserWallet.objects.get(user=user).wallet_balance


def join(user, tournament):
    return client_for(user).post('/tournament/join-tournament/', {
        'tournament_id': tournament.tournament_id, 'pin': PIN}, format='json')


class EntryLedgerTests(TestCase):
    """A paid entry is a sale: the organiser is owed it, less the fee."""

    def setUp(self):
        self.org = make_user(900)
        self.t = make_tournament(self.org, entry_fee='Paid', entry_fee_price=10)
        self.player = a_player(901, coins=50)

    def test_an_entry_writes_the_organiser_line_and_the_platform_line(self):
        res = join(self.player, self.t)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(balance(self.player), 40, 'the entry, and nothing on top')
        self.assertEqual(res.data['data']['coins_deducted'], 10)
        self.assertEqual(res.data['data']['fee_vc'], 0)

        lines = {l.kind: l for l in EventLedgerEntry.objects.filter(tournament=self.t)}
        self.assertEqual(set(lines), {'organiser', 'platform'})
        # 5% + 100 naira on a 10,000 naira entry is 600 naira.
        self.assertEqual(lines['platform'].amount_ngn, Decimal('600.00'))
        self.assertEqual(lines['organiser'].amount_ngn, Decimal('9400.00'))
        self.assertEqual(lines['organiser'].user_id, self.org.user_id)
        self.assertEqual(lines['organiser'].registration.user_id, self.player.user_id)
        self.assertEqual((lines['organiser'].fee_pct, lines['organiser'].fee_flat_ngn),
                         (5.0, Decimal('100.00')))
        self.assertIsNone(lines['organiser'].event_id)

    def test_the_rate_is_the_dashboard_rate_at_the_moment_of_entry(self):
        AdminSetting.put('platform_fees', tournament_fee_pct=10, tournament_fee_flat_ngn=0)
        join(self.player, self.t)
        line = EventLedgerEntry.objects.get(tournament=self.t, kind='platform')
        self.assertEqual(line.amount_ngn, Decimal('1000.00'))
        AdminSetting.put('platform_fees', tournament_fee_pct=50)
        line.refresh_from_db()
        self.assertEqual(line.amount_ngn, Decimal('1000.00'), 'a change never rewrites an entry')

    def test_a_free_entry_writes_nothing(self):
        free = make_tournament(self.org)
        res = join(self.player, free)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertFalse(EventLedgerEntry.objects.filter(tournament=free).exists())

    def test_a_cancel_refunds_what_was_paid_and_reverses_the_lines(self):
        join(self.player, self.t)
        # The price goes up after they entered; the refund is what THEY paid.
        self.t.entry_fee_price = 25
        self.t.save(update_fields=['entry_fee_price'])
        res = client_for(self.org).post('/tournament/%s/cancel/' % self.t.tournament_id,
                                        {'reason': 'rain'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['data']['total_refunded'], 10)
        self.assertEqual(balance(self.player), 50)
        figures = ledger.balances(self.t)
        self.assertEqual(figures['organiser_owed_ngn'], Decimal('0'))
        self.assertEqual(figures['platform_fee_ngn'], Decimal('0'))
        reg = TournamentRegistration.objects.get(tournament=self.t)
        self.assertEqual(reg.status, 'withdrawn')

    def test_the_quote_is_public_and_matches_the_charge(self):
        from rest_framework.test import APIClient
        res = APIClient().get('/tournament/%s/entry-quote/' % self.t.tournament_id)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.data['data']
        self.assertEqual((data['entry_vc'], data['fee_ngn'], data['total_vc']), (10, 600.0, 10))
        self.assertEqual(data['fee_bearer'], 'organiser')
        self.assertFalse(data['buyer_pays_fee'])


class WhoBearsTheFeeTests(TestCase):

    def setUp(self):
        self.org = make_user(910)
        self.t = make_tournament(self.org, entry_fee='Paid', entry_fee_price=30)
        self.player = a_player(911, coins=50)

    def test_the_organiser_can_put_it_on_the_player(self):
        res = client_for(self.org).put(
            '/tournament/edit-tournament/%s/' % self.t.tournament_id,
            {'fee_bearer': 'player'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.t.refresh_from_db()
        self.assertEqual(self.t.fee_bearer, 'player')
        res = client_for(self.org).put(
            '/tournament/edit-tournament/%s/' % self.t.tournament_id,
            {'fee_bearer': 'somebody'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'VALIDATION_ERROR')

    def test_on_the_player_the_whole_coins_go_on_top_and_the_rest_off_the_organiser(self):
        # 5% of 30,000 + 100 = 1,600 naira: the player pays 1 coin on top, the
        # 600 under a coin comes off the organiser, told before the PIN.
        self.t.fee_bearer = 'player'
        self.t.save(update_fields=['fee_bearer'])
        from rest_framework.test import APIClient
        quote = APIClient().get('/tournament/%s/entry-quote/' % self.t.tournament_id).data['data']
        self.assertTrue(quote['buyer_pays_fee'])
        self.assertEqual((quote['buyer_fee_vc'], quote['total_vc']), (1, 31))

        res = join(self.player, self.t)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data['data']['coins_deducted'], 31)
        self.assertEqual(res.data['data']['fee_vc'], 1)
        self.assertEqual(balance(self.player), 19)
        lines = {l.kind: l for l in EventLedgerEntry.objects.filter(tournament=self.t)}
        self.assertEqual(lines['platform'].amount_ngn, Decimal('1600.00'))
        self.assertEqual(lines['organiser'].amount_ngn, Decimal('29400.00'))
        self.assertEqual(lines['organiser'].buyer_fee_ngn, Decimal('1000.00'))

    def test_a_refund_returns_the_fee_the_player_bore_too(self):
        self.t.fee_bearer = 'player'
        self.t.save(update_fields=['fee_bearer'])
        join(self.player, self.t)
        self.assertEqual(balance(self.player), 19)
        client_for(self.org).post('/tournament/%s/cancel/' % self.t.tournament_id,
                                  {'reason': 'off'}, format='json')
        self.assertEqual(balance(self.player), 50)

    def test_the_short_balance_counts_the_fee(self):
        self.t.fee_bearer = 'player'
        self.t.save(update_fields=['fee_bearer'])
        UserWallet.objects.filter(user=self.player).update(wallet_balance=30)
        res = join(self.player, self.t)
        self.assertEqual(res.status_code, 422, res.content)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_BALANCE')
        self.assertEqual(balance(self.player), 30)
        self.assertFalse(TournamentRegistration.objects.filter(tournament=self.t).exists())


class PrizesFromThePoolTests(TestCase):
    """A prize comes out of what the entries brought in, then out of the
    organiser's own wallet, and never out of nothing."""

    def _finished(self, *, entry=0, prizes=(1000, 500), organiser_coins=0):
        org, t = PrizeDistributionTests()._completed_tournament_with_prizes()
        TournamentPrizeDistribution.objects.filter(tournament=t).delete()
        for pos, amount in enumerate(prizes, start=1):
            TournamentPrizeDistribution.objects.create(tournament=t, position=pos, prize=amount)
        UserWallet.objects.filter(user=org).update(wallet_balance=organiser_coins)
        if entry:
            # The four entrants paid, on the ledger, as if they had entered
            # through the door with the fee on the organiser.
            t.entry_fee, t.entry_fee_price = 'Paid', entry
            t.save(update_fields=['entry_fee', 'entry_fee_price'])
            for reg in TournamentRegistration.objects.filter(tournament=t):
                ledger.record_entry(reg, ledger.quote_entry(t))
        return org, t

    def test_a_free_tournament_pays_prizes_from_the_organisers_wallet(self):
        org, t = self._finished(organiser_coins=1500)
        plan = prize_service.plan(t)
        self.assertEqual((plan['from_pool_vc'], plan['from_wallet_vc']), (0, 1500))
        self.assertTrue(plan['wallet_can_cover'])
        prize_service.distribute(t, triggered_by=org)
        self.assertEqual(balance(org), 0)
        champ = TournamentRegistration.objects.get(tournament=t, final_position=1)
        self.assertEqual(balance(champ.user), 1000)
        topup = EventLedgerEntry.objects.get(tournament=t, note=ledger.PRIZE_TOPUP_NOTE)
        self.assertEqual(topup.amount_vc, 1500)
        self.assertEqual(EventLedgerEntry.objects.filter(tournament=t, prize__isnull=False).count(), 2)
        # Nothing left owed: the top-up went straight out as prizes.
        self.assertEqual(ledger.balances(t)['organiser_owed_ngn'], Decimal('0'))

    def test_an_organiser_who_cannot_cover_it_is_refused_before_any_coin_moves(self):
        org, t = self._finished(organiser_coins=100)
        plan = prize_service.plan(t)
        self.assertIn('pool_short', plan['problems'])
        with self.assertRaises(prize_service.PrizeError) as caught:
            prize_service.distribute(t, triggered_by=org)
        self.assertEqual(caught.exception.code, 'pool_short')
        self.assertIn('1500', caught.exception.message)
        self.assertEqual(balance(org), 100)
        self.assertEqual(PrizePayout.objects.filter(tournament=t).count(), 0)
        champ = TournamentRegistration.objects.get(tournament=t, final_position=1)
        self.assertEqual(balance(champ.user), 0)
        self.assertFalse(EventLedgerEntry.objects.filter(tournament=t).exists())

    def test_the_console_answers_402_with_the_code(self):
        org, t = self._finished(organiser_coins=100)
        res = client_for(org).post('/tournament/%s/distribute-prizes/' % t.tournament_id,
                                   {'confirm': True}, format='json')
        self.assertEqual(res.status_code, 402, res.content)
        self.assertEqual(res.data['code'], 'POOL_SHORT')

    def test_entries_fund_the_prizes_and_the_organiser_keeps_the_rest(self):
        # Four entries at 500 coins: 2,000,000 naira gross, fee 5% + 100 each
        # = 100,400 naira, pool 1,899,600. Prizes 1,500 coins = 1,500,000.
        org, t = self._finished(entry=500, prizes=(1000, 500), organiser_coins=0)
        plan = prize_service.plan(t)
        self.assertEqual(plan['pool_ngn'], 1899600.0)
        self.assertEqual((plan['from_pool_vc'], plan['from_wallet_vc']), (1500, 0))
        prize_service.distribute(t, triggered_by=org)
        self.assertEqual(balance(org), 0, 'nothing from the wallet')
        self.assertEqual(ledger.balances(t)['organiser_owed_ngn'], Decimal('399600.00'))

    def test_a_pool_that_covers_part_takes_the_rest_from_the_wallet_in_whole_coins(self):
        # Four entries at 100 coins: pool 400,000 - 4 x 5,100 = 379,600 naira.
        # Prizes 1,500,000: 1,120,400 short = 1,121 coins, rounded up.
        org, t = self._finished(entry=100, prizes=(1000, 500), organiser_coins=2000)
        plan = prize_service.plan(t)
        self.assertEqual(plan['from_wallet_vc'], 1121)
        self.assertEqual(plan['from_pool_vc'], 379)
        prize_service.distribute(t, triggered_by=org)
        self.assertEqual(balance(org), 2000 - 1121)
        # 379,600 + 1,121,000 - 1,500,000 = 600 naira left, under a coin.
        self.assertEqual(ledger.balances(t)['organiser_owed_ngn'], Decimal('600.00'))


class OrganiserIsPaidTests(TestCase):

    def setUp(self):
        self.org = make_user(920)
        self.t = make_tournament(self.org, entry_fee='Paid', entry_fee_price=10)
        for i in range(3):
            join(a_player(921 + i, coins=20), self.t)

    def test_earnings_say_what_the_entries_earned(self):
        res = client_for(self.org).get('/tournament/%s/earnings/' % self.t.tournament_id)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.data['data']
        self.assertEqual(data['entries_paid'], 3)
        self.assertEqual(data['entries_ngn'], 30000.0)
        self.assertEqual(data['fee_ngn'], 1800.0)
        self.assertEqual(data['organiser_owed_ngn'], 28200.0)
        self.assertEqual(data['organiser_owed_vc'], 28)
        self.assertEqual(data['organiser_paid_vc'], 0)
        self.assertEqual((data['fee_pct'], data['fee_flat_ngn'], data['fee_bearer']),
                         (5.0, 100.0, 'organiser'))

    def test_earnings_are_not_public(self):
        stranger = client_for(make_user(930))
        res = stranger.get('/tournament/%s/earnings/' % self.t.tournament_id)
        self.assertEqual(res.status_code, 403)

    def test_settle_pays_whole_coins_carries_the_rest_and_never_pays_twice(self):
        res = client_for(self.org).post('/tournament/%s/settle/' % self.t.tournament_id,
                                        {}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['data']['amount_vc'], 28)
        self.assertEqual(balance(self.org), 28)
        self.assertEqual(res.data['data']['organiser_owed_ngn'], 200.0)
        self.assertEqual(res.data['data']['organiser_paid_ngn'], 28000.0)
        tx = Transaction.objects.get(wallet__user=self.org)
        self.assertEqual(tx.amount, 28)
        self.assertIn(self.t.tournament_title, tx.description)

        again = client_for(self.org).post('/tournament/%s/settle/' % self.t.tournament_id,
                                          {}, format='json')
        self.assertEqual(again.data['data']['amount_vc'], 0)
        self.assertEqual(balance(self.org), 28)

        # Another entry brings the carry over a coin.
        join(a_player(925, coins=20), self.t)
        third = client_for(self.org).post('/tournament/%s/settle/' % self.t.tournament_id,
                                          {}, format='json')
        self.assertEqual(third.data['data']['amount_vc'], 9)
        self.assertEqual(balance(self.org), 37)
        self.assertEqual(third.data['data']['organiser_owed_ngn'], 600.0)
