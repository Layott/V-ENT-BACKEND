"""A prize typed in naira is paid in the coins it is worth, through create and edit (inbox 403).

Found walking inbox 398 on 1 October 2026: the wizard dropped each prize's
currency and never sent prize_currency, so 50,000 naira was stored as 50,000
coins; reopening the draft loaded the coin figure with no currency, and saving
it again converted a second time. Both doors now read the table through
views._prize_rows, and every screen reads a row through views.prize_row.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games

from .money import ngn_per_coin
from .models import Tournament
from .tests_draft_round_trip import a_user


class PrizeCurrencyTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('pc_org')
        Games.objects.create(game_title='EA FC PC')
        now = timezone.now()
        self.body = {
            'tournament_title': 'Naira Cup', 'game': 'EA FC PC', 'tournament_description': 'x',
            'tournament_type': 'virtual',
            'start_date_and_time': (now + timedelta(days=7)).isoformat(),
            'end_date_and_time': (now + timedelta(days=8)).isoformat(),
            'entry_type': 'Free', 'tournament_visibility': 'public', 'tournament_access': 'individual',
            'is_draft': 'true', 'prize_type': 'distributed', 'prize_currency': 'NGN',
            'prize_data': json.dumps([
                {'position': 1, 'amount': '50000', 'currency': 'NGN', 'extras': ''},
                {'position': 2, 'amount': '20000', 'currency': 'NGN', 'extras': ''},
            ]),
        }
        res = self.client.post('/tournament/create-tournament/', self.body, **self.auth)
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.tournament = Tournament.objects.get(tournament_title='Naira Cup')

    def coins(self):
        return [p.prize for p in self.tournament.prize_distributions.order_by('position')]

    def test_create_converts_the_typed_naira(self):
        self.assertEqual(self.coins(), [Decimal('50000') / ngn_per_coin(), Decimal('20000') / ngn_per_coin()])

    def test_the_draft_gives_back_what_was_typed(self):
        res = self.client.get('/tournament/view-tournament/%s/' % self.tournament.slug, **self.auth)
        rows = (res.data['data'].get('tournament') or res.data['data'])['prize_distribution']
        self.assertEqual([(r['amount'], r['currency']) for r in rows],
                         [('50000.00', 'NGN'), ('20000.00', 'NGN')])

    def test_saving_the_reopened_draft_does_not_convert_twice(self):
        before = self.coins()
        res = self.client.put(
            '/tournament/edit-tournament/%d/' % self.tournament.tournament_id,
            data={'prize_currency': 'NGN', 'prize_data': [
                {'position': 1, 'amount': '50000.00', 'currency': 'NGN'},
                {'position': 2, 'amount': '20000.00', 'currency': 'NGN'},
            ]}, content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(self.coins(), before)
