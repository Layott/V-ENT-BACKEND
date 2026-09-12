"""One price, answered once.

Found on 8 September by pressing the buy panel rather than reading it. A tier
with a group rate of 16 VC at four or more showed "20 VC x 4, total 80" while
`price_for(4)` returned 16 and the checkout took 64. The panel was doing its own
arithmetic, and `ledger.quote` was never reachable over HTTP, so it could not
have asked the authority even if somebody had wanted it to.

These are the cases that stop the two sides drifting again.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from . import ledger
from .models import Event, EventReferral, TicketTier


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('q-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class QuoteTests(TestCase):

    def setUp(self):
        self.organiser = a_user('quote_org')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Quote Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=6))
        # 20 VC each, 16 VC each at four or more. The real shape from the
        # seeded Lagos Anime Con VIP tier that exposed the fault.
        self.tier = TicketTier.objects.create(
            event=self.event, name='VIP', price=Decimal('20000'), quantity=30,
            group_min=4, group_price=Decimal('16000'))

    def url(self, **params):
        query = '&'.join('%s=%s' % (k, v) for k, v in params.items())
        return '/event/%s/quote/?%s' % (self.event.slug or self.event.event_id,
                                        query)

    def test_one_ticket_is_the_list_price(self):
        res = self.client.get(self.url(tier=self.tier.id, quantity=1))
        self.assertEqual(res.status_code, 200)
        body = res.json()['data']
        self.assertEqual(body['unit_vc'], 20)
        self.assertEqual(body['total_vc'], 20)
        self.assertEqual(body['price_reason'], 'list')

    def test_the_group_rate_applies_at_the_threshold(self):
        res = self.client.get(self.url(tier=self.tier.id, quantity=4))
        body = res.json()['data']
        self.assertEqual(body['unit_vc'], 16)
        self.assertEqual(body['total_vc'], 64)
        self.assertEqual(body['price_reason'], 'group')

    def test_one_below_the_threshold_is_still_the_list_price(self):
        body = self.client.get(self.url(tier=self.tier.id, quantity=3)).json()['data']
        self.assertEqual(body['unit_vc'], 20)
        self.assertEqual(body['total_vc'], 60)

    def test_the_quote_matches_what_the_checkout_would_charge(self):
        """The whole point. Any drift here is the bug this file exists for."""
        for quantity in (1, 2, 3, 4, 5, 10):
            with self.subTest(quantity=quantity):
                body = self.client.get(
                    self.url(tier=self.tier.id, quantity=quantity)).json()['data']
                charged = ledger.quote(self.tier, quantity, self.event)
                self.assertEqual(body['total_vc'], charged['total_vc'])
                self.assertEqual(body['unit_vc'], charged['unit_vc'])

    def test_the_panel_is_told_the_offer_before_the_quantity_reaches_it(self):
        """At quantity 1 the screen still has to be able to say "4 for 16 each"."""
        body = self.client.get(self.url(tier=self.tier.id, quantity=1)).json()['data']
        self.assertEqual(body['group_min'], 4)
        self.assertEqual(body['group_unit_vc'], 16)

    def test_early_bird_moves_the_price_once_the_allocation_has_gone(self):
        tier = TicketTier.objects.create(
            event=self.event, name='Early', price=Decimal('10000'),
            quantity=100, early_bird_quantity=2,
            early_bird_price=Decimal('15000'))
        first = self.client.get(self.url(tier=tier.id, quantity=1)).json()['data']
        self.assertEqual(first['unit_vc'], 10)
        self.assertEqual(first['price_reason'], 'list')

        tier.sold = 2
        tier.save(update_fields=['sold'])
        after = self.client.get(self.url(tier=tier.id, quantity=1)).json()['data']
        self.assertEqual(after['unit_vc'], 15)
        self.assertEqual(after['price_reason'], 'early_bird')

    def test_the_group_rate_wins_over_early_bird_exactly_as_the_model_says(self):
        tier = TicketTier.objects.create(
            event=self.event, name='Both', price=Decimal('10000'), quantity=100,
            early_bird_quantity=1, early_bird_price=Decimal('15000'),
            group_min=3, group_price=Decimal('8000'))
        tier.sold = 5
        tier.save(update_fields=['sold'])
        body = self.client.get(self.url(tier=tier.id, quantity=3)).json()['data']
        self.assertEqual(body['unit_vc'], 8)
        self.assertEqual(body['price_reason'], 'group')

    def test_a_hidden_tier_is_not_priced_without_its_code(self):
        hidden = TicketTier.objects.create(
            event=self.event, name='Cosplayer pass', price=Decimal('2500'),
            quantity=20, access_code='COSPLAY26')
        refused = self.client.get(self.url(tier=hidden.id, quantity=1))
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()['code'], 'CODE_REQUIRED')

    def test_a_hidden_tier_is_priced_for_somebody_holding_the_code(self):
        hidden = TicketTier.objects.create(
            event=self.event, name='Cosplayer pass', price=Decimal('2500'),
            quantity=20, access_code='COSPLAY26')
        ok = self.client.get(self.url(tier=hidden.id, quantity=1,
                                      code='cosplay26'))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['data']['unit_vc'], 2)

    def test_an_influencer_code_prices_the_tier_it_unlocks(self):
        referral = EventReferral.objects.create(
            event=self.event, name='Ada', code='ADA20', commission_pct=20)
        locked = TicketTier.objects.create(
            event=self.event, name="Ada's allocation", price=Decimal('5000'),
            quantity=10, unlocked_by=referral)
        self.assertEqual(
            self.client.get(self.url(tier=locked.id, quantity=1)).status_code, 403)
        self.assertEqual(
            self.client.get(self.url(tier=locked.id, quantity=1,
                                     code='ada20')).status_code, 200)

    def test_a_guest_with_no_account_is_told_the_same_number(self):
        """No Authorization header at all. A guest buys without an account."""
        body = self.client.get(self.url(tier=self.tier.id, quantity=4)).json()['data']
        self.assertEqual(body['total_vc'], 64)

    def test_a_tier_from_another_event_is_not_priced_here(self):
        other = Event.objects.create(
            name='Elsewhere', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=timezone.now() + timedelta(days=3),
            end_date=timezone.now() + timedelta(days=3, hours=2),
            reg_start_date=timezone.now() - timedelta(days=1),
            reg_end_date=timezone.now() + timedelta(days=2))
        theirs = TicketTier.objects.create(
            event=other, name='Theirs', price=Decimal('1000'), quantity=5)
        res = self.client.get(self.url(tier=theirs.id, quantity=1))
        self.assertEqual(res.status_code, 404)

    def test_quantity_is_bounded_rather_than_trusted(self):
        body = self.client.get(self.url(tier=self.tier.id, quantity=9999)).json()['data']
        self.assertLessEqual(body['quantity'], 10)
        body = self.client.get(self.url(tier=self.tier.id, quantity=-3)).json()['data']
        self.assertEqual(body['quantity'], 1)

    def test_a_missing_tier_is_a_404_and_not_a_500(self):
        self.assertEqual(
            self.client.get(self.url(quantity=1)).status_code, 404)
        self.assertEqual(
            self.client.get(self.url(tier=999999, quantity=1)).status_code, 404)
