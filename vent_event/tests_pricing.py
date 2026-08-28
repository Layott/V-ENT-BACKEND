"""Early bird, group rates and access codes.

Three ways a price moves, all on the tier because all three answer the same
question: what does this type cost, and who may see it.

The test that matters most is the last one. What somebody paid is written on
their ticket, so a price change later never rewrites a receipt from before it.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from .models import Event, Ticket, TicketTier


def a_user(name, balance=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('p-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    # A paid ticket goes through the wallet PIN, so a buyer without one cannot
    # complete a purchase at all.
    UserWallet.objects.create(
        user_wallet_id=('pw%s' % name)[:10], user=user, wallet_balance=balance,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class PriceForTests(TestCase):
    """The rule, without a request in the way."""

    def setUp(self):
        self.organiser, _ = a_user('price_org')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Price Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('10000'),
            quantity=100)

    def test_a_plain_tier_has_one_price(self):
        self.assertEqual(self.tier.price_for(1), Decimal('10000'))
        self.assertEqual(self.tier.price_for(10), Decimal('10000'))

    def test_early_bird_holds_until_the_allocation_is_gone(self):
        self.tier.early_bird_quantity = 20
        self.tier.early_bird_price = Decimal('15000')
        self.tier.sold = 19
        self.assertEqual(self.tier.price_for(1), Decimal('10000'))

    def test_early_bird_lifts_the_price_once_it_is(self):
        self.tier.early_bird_quantity = 20
        self.tier.early_bird_price = Decimal('15000')
        self.tier.sold = 20
        self.assertEqual(self.tier.price_for(1), Decimal('15000'))

    def test_a_group_pays_less_per_ticket(self):
        self.tier.group_min = 5
        self.tier.group_price = Decimal('8000')
        self.assertEqual(self.tier.price_for(4), Decimal('10000'))
        self.assertEqual(self.tier.price_for(5), Decimal('8000'))

    def test_a_group_rate_wins_over_early_bird(self):
        """Somebody buying ten is the case the organiser most wants to reward,
        and the two stacking is never what anybody meant."""
        self.tier.early_bird_quantity = 1
        self.tier.early_bird_price = Decimal('15000')
        self.tier.sold = 50
        self.tier.group_min = 5
        self.tier.group_price = Decimal('8000')
        self.assertEqual(self.tier.price_for(5), Decimal('8000'))

    def test_an_access_code_makes_a_tier_hidden(self):
        self.assertFalse(self.tier.is_hidden)
        self.tier.access_code = 'MEMBERS'
        self.assertTrue(self.tier.is_hidden)


class BuyingTests(TestCase):
    def setUp(self):
        self.organiser, self.organiser_auth = a_user('buy_org')
        self.buyer, self.buyer_auth = a_user('buy_buyer', balance=100000)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Buy Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('10000'),
            quantity=100)

    def buy(self, quantity=1, code=None, tier=None):
        body = {'tier_id': (tier or self.tier).id, 'quantity': quantity,
                'pin': '1234'}
        if code:
            body['code'] = code
        return self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id, data=body,
            content_type='application/json', **self.buyer_auth)

    def listing(self, code=None):
        url = '/event/%s/ticket-types/' % self.event.event_id
        if code:
            url += '?code=%s' % code
        return self.client.get(url)

    # ----------------------------------------------------------- group rate

    def test_a_group_is_charged_the_group_price(self):
        self.tier.group_min = 3
        self.tier.group_price = Decimal('6000')
        self.tier.save(update_fields=['group_min', 'group_price'])

        res = self.buy(quantity=3)
        self.assertIn(res.status_code, (200, 201), res.content)
        prices = {t.price_ngn for t in Ticket.objects.all()}
        self.assertEqual(prices, {Decimal('6000.00')})

    def test_below_the_group_size_pays_the_normal_price(self):
        self.tier.group_min = 3
        self.tier.group_price = Decimal('6000')
        self.tier.save(update_fields=['group_min', 'group_price'])

        self.buy(quantity=2)
        self.assertEqual(
            {t.price_ngn for t in Ticket.objects.all()}, {Decimal('10000.00')})

    # ---------------------------------------------------------- early bird

    def test_the_price_moves_once_the_early_allocation_is_gone(self):
        self.tier.early_bird_quantity = 1
        self.tier.early_bird_price = Decimal('20000')
        self.tier.save(update_fields=['early_bird_quantity', 'early_bird_price'])

        self.buy()
        first = Ticket.objects.order_by('id').first()
        self.assertEqual(first.price_ngn, Decimal('10000.00'))

        self.buy()
        second = Ticket.objects.order_by('id').last()
        self.assertEqual(second.price_ngn, Decimal('20000.00'))

    def test_a_later_price_change_does_not_rewrite_an_earlier_receipt(self):
        """What somebody paid is on their ticket. This is the whole reason it
        is stored there rather than read back off the tier."""
        self.buy()
        paid = Ticket.objects.get().price_ngn

        self.tier.price = Decimal('99000')
        self.tier.save(update_fields=['price'])

        Ticket.objects.get().refresh_from_db()
        self.assertEqual(Ticket.objects.get().price_ngn, paid)

    # --------------------------------------------------------- access code

    def test_a_hidden_tier_is_not_in_the_public_list(self):
        secret = TicketTier.objects.create(
            event=self.event, name='Members', price=Decimal('5000'),
            quantity=10, access_code='MEMBERS')
        names = [t['name'] for t in self.listing().json()['data']['tiers']]
        self.assertNotIn('Members', names)
        self.assertEqual(self.listing().json()['data']['hidden_count'], 1)

    def test_the_code_reveals_it(self):
        TicketTier.objects.create(
            event=self.event, name='Members', price=Decimal('5000'),
            quantity=10, access_code='MEMBERS')
        data = self.listing(code='members').json()['data']
        self.assertIn('Members', [t['name'] for t in data['tiers']])
        self.assertEqual(data['unlocked'], ['Members'])

    def test_a_hidden_tier_cannot_be_bought_by_guessing_its_id(self):
        """A hidden type that anybody who knows the id can buy is not hidden."""
        secret = TicketTier.objects.create(
            event=self.event, name='Members', price=Decimal('5000'),
            quantity=10, access_code='MEMBERS')
        res = self.buy(tier=secret)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'CODE_REQUIRED')

    def test_the_right_code_buys_it(self):
        secret = TicketTier.objects.create(
            event=self.event, name='Members', price=Decimal('5000'),
            quantity=10, access_code='MEMBERS')
        res = self.buy(tier=secret, code='MEMBERS')
        self.assertIn(res.status_code, (200, 201), res.content)

    def test_the_code_is_not_case_sensitive(self):
        """Somebody typing it off a poster should not be caught by that."""
        secret = TicketTier.objects.create(
            event=self.event, name='Members', price=Decimal('5000'),
            quantity=10, access_code='MEMBERS')
        self.assertIn(self.buy(tier=secret, code='members').status_code,
                      (200, 201))
