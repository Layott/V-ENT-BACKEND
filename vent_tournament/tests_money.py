"""Prize money, converted on the server.

The rule this protects: what pays out is computed here, from the currency and
the amount the organiser typed, and never from a figure a browser worked out.
"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_tournament.models import Tournament, TournamentPrizeDistribution
from vent_tournament.money import from_coins, rates, to_coins


class ConversionTests(TestCase):
    def test_coins_pass_through(self):
        self.assertEqual(to_coins('500', 'VC'), Decimal('500'))

    def test_naira_converts_at_the_published_rate(self):
        # Every screen says 1,000 NGN = 1 VC.
        self.assertEqual(to_coins('1000', 'NGN'), Decimal('1'))
        self.assertEqual(to_coins('500000', 'NGN'), Decimal('500'))

    def test_dollars_convert_through_naira(self):
        with mock.patch.dict('os.environ', {'NGN_TO_USD_RATE': '1500'}):
            # $100 -> ₦150,000 -> 150 VC
            self.assertEqual(to_coins('100', 'USD'), Decimal('150'))

    def test_coins_are_whole_and_round_up_at_the_half(self):
        self.assertEqual(to_coins('1500', 'NGN'), Decimal('2'))
        self.assertEqual(to_coins('1400', 'NGN'), Decimal('1'))

    def test_nothing_and_nonsense_become_zero_rather_than_raising(self):
        for value in ('', None, 'abc', '-50'):
            self.assertEqual(to_coins(value, 'NGN'), Decimal('0'), value)

    def test_an_unknown_currency_is_worth_nothing(self):
        self.assertEqual(to_coins('100', 'XYZ'), Decimal('0'))

    def test_the_round_trip_holds(self):
        self.assertEqual(from_coins(Decimal('500'), 'NGN'), Decimal('500000.00'))
        with mock.patch.dict('os.environ', {'NGN_TO_USD_RATE': '1500'}):
            self.assertEqual(from_coins(Decimal('150'), 'USD'), Decimal('100.00'))

    def test_the_rates_endpoint_states_the_rate(self):
        res = self.client.get('/tournament/prize-rates/')
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertEqual(data['ngn_per_coin'], 1000)
        self.assertIn('NGN', data['currencies'])


class PrizeCreationTests(TestCase):
    def setUp(self):
        self.game = Games.objects.create(game_title='Free Fire')
        self.user = Users.objects.create(
            username='organiser', email='org@vent.test',
            login_session_token='orgtoken12345678',
            login_session_created_at=timezone.now(), is_active=True,
        )
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.user.login_session_token}'}

    def create(self, **extra):
        import json
        body = {
            'tournament_title': 'Naira Cup',
            'game': 'Free Fire',
            'tournament_type': 'online',
            'tournament_visibility': 'public',
            'tournament_access': 'individual',
            'start_date_and_time': '2026-09-10T18:00',
            'end_date_and_time': '2026-09-10T22:00',
            'entry_type': 'Free',
            'is_draft': '0',
            'prize_type': 'distributed',
            'prize_currency': 'NGN',
            'prize_pool_total': '1000000',
            'prize_data': json.dumps([
                {'position': 1, 'amount': '500000', 'extras': 'Gaming chair',
                 'extras_amount': '100000'},
                {'position': 2, 'amount': '300000'},
                {'position': 3, 'amount': '200000'},
            ]),
        }
        body.update(extra)
        return self.client.post('/tournament/create-tournament/', body, **self.auth)

    def test_naira_prizes_are_stored_as_coins_and_as_typed(self):
        res = self.create()
        self.assertIn(res.status_code, (200, 201), res.content[:300])

        tournament = Tournament.objects.get(tournament_title='Naira Cup')
        self.assertEqual(tournament.prize_currency, 'NGN')
        self.assertEqual(tournament.prize_pool_total, Decimal('1000000.00'))
        self.assertEqual(tournament.prize_pool_total_vc, Decimal('1000.00'))

        first = TournamentPrizeDistribution.objects.get(tournament=tournament, position=1)
        self.assertEqual(first.prize, Decimal('500.00'))          # what pays out
        self.assertEqual(first.amount_original, Decimal('500000.00'))  # what was typed
        self.assertEqual(first.currency, 'NGN')
        self.assertEqual(first.extras, 'Gaming chair')
        self.assertEqual(first.extras_prize, Decimal('100.00'))

    def test_the_positions_add_up_to_the_pool(self):
        self.create()
        tournament = Tournament.objects.get(tournament_title='Naira Cup')
        total = sum(
            p.prize for p in TournamentPrizeDistribution.objects.filter(tournament=tournament)
        )
        self.assertEqual(total, tournament.prize_pool_total_vc)

    def test_a_client_supplied_coin_figure_cannot_override_the_conversion(self):
        import json
        res = self.create(prize_data=json.dumps([
            # A tampered payload: says ₦1,000 but claims a million coins.
            {'position': 1, 'amount': '1000', 'prize': '1000000'},
        ]))
        self.assertIn(res.status_code, (200, 201))
        row = TournamentPrizeDistribution.objects.get(position=1)
        self.assertEqual(row.prize, Decimal('1.00'))

    def test_an_unknown_currency_falls_back_to_coins(self):
        res = self.create(prize_currency='XYZ', prize_pool_total='500')
        self.assertIn(res.status_code, (200, 201))
        tournament = Tournament.objects.get(tournament_title='Naira Cup')
        self.assertEqual(tournament.prize_currency, 'VC')
