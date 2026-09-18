"""A promo code the organiser made is worth something at the till.

The organiser's side of promo codes shipped on 3 September 2026 with a
"uses" column that nothing ever wrote: no quote read a code, no purchase took
one. Written on the consequence: after a purchase with a code the buyer paid
less, the ticket says which code, the code's uses went up, and a code that
may not be used says why.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from .models import Event, EventPromo, EventReferral, Ticket, TicketTier


def a_user(name, balance=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('p-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('pw%s' % name)[:10], user=user, wallet_balance=balance,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class PromoRedeemTests(TestCase):
    def setUp(self):
        self.organiser, self.org_auth = a_user('pr_org')
        self.buyer, self.buyer_auth = a_user('pr_buyer', balance=100)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Promo Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, capacity=100,
            start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=6))
        self.ga = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('3000'), quantity=50)
        self.vip = TicketTier.objects.create(
            event=self.event, name='VIP', price=Decimal('5000'), quantity=10)
        self.ten_off = EventPromo.objects.create(
            event=self.event, code='WALK10', kind=EventPromo.PERCENT, value=10,
            max_tickets=2)
        self.flat = EventPromo.objects.create(
            event=self.event, code='WALKOFF', kind=EventPromo.AMOUNT, value=1500)

    def quote(self, **params):
        query = '&'.join('%s=%s' % kv for kv in params.items())
        return self.client.get('/event/%s/quote/?%s' % (self.event.slug, query)).json()['data']

    def buy(self, promo=None, tier=None, quantity=1):
        body = {'tier_id': (tier or self.ga).id, 'quantity': quantity, 'pin': '1234'}
        if promo is not None:
            body['promo'] = promo
        return self.client.post('/event/%s/buy-ticket/' % self.event.slug, data=body,
                                content_type='application/json', **self.buyer_auth)

    # ------------------------------------------------------------ the quote

    def test_the_quote_takes_the_code_off(self):
        q = self.quote(tier_id=self.ga.id, quantity=1, promo='WALK10')
        # 3000 less 10% is 2700 naira, which is 2 whole coins (the same floor
        # every discount on the platform uses).
        self.assertEqual(q['promo']['applied'], True)
        self.assertEqual(q['promo']['saving_ngn'], 300.0)
        self.assertEqual(q['unit_vc'], 2)
        self.assertEqual(q['tickets_ngn'], 2700.0)
        q = self.quote(tier_id=self.ga.id, quantity=1, promo='WALKOFF')
        self.assertEqual((q['tickets_ngn'], q['unit_vc']), (1500.0, 1))

    def test_a_small_discount_cannot_make_a_coin_ticket_free(self):
        """10% off a 1,000 naira ticket is 900 naira, which floored to 0
        coins and gave the ticket away."""
        one = TicketTier.objects.create(
            event=self.event, name='Day pass', price=Decimal('1000'), quantity=50)
        q = self.quote(tier_id=one.id, quantity=1, promo='WALK10')
        self.assertEqual((q['tickets_ngn'], q['unit_vc'], q['total_vc']), (900.0, 1, 1))
        self.assertEqual((q['promo']['saving_ngn'], q['promo']['saving_vc']), (100.0, 0))
        res = self.buy(promo='WALK10', tier=one)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(UserWallet.objects.get(user=self.buyer).wallet_balance, 99)

    def test_the_quote_says_why_a_code_did_not_take(self):
        q = self.quote(tier_id=self.ga.id, quantity=1, promo='NOPE')
        self.assertEqual(q['promo'], {'code': '', 'applied': False, 'error': 'PROMO_UNKNOWN',
                                      'saving_ngn': 0.0, 'saving_vc': 0})
        self.assertEqual(q['unit_vc'], 3)
        self.ten_off.tier = self.vip
        self.ten_off.save(update_fields=['tier'])
        q = self.quote(tier_id=self.ga.id, quantity=1, promo='WALK10')
        self.assertEqual(q['promo']['error'], 'PROMO_WRONG_TIER')
        q = self.quote(tier_id=self.ga.id, quantity=3, promo='WALKOFF')
        self.assertTrue(q['promo']['applied'])
        self.ten_off.tier = None
        self.ten_off.save(update_fields=['tier'])
        q = self.quote(tier_id=self.ga.id, quantity=3, promo='WALK10')
        self.assertEqual(q['promo']['error'], 'PROMO_EXHAUSTED')

    # --------------------------------------------------------- the wallet

    def test_a_wallet_purchase_pays_the_discounted_price_and_spends_the_code(self):
        res = self.buy(promo='walk10')
        self.assertEqual(res.status_code, 201, res.content)
        ticket = Ticket.objects.get(user=self.buyer)
        self.assertEqual((ticket.price_vc, ticket.price_ngn), (2, Decimal('2700.00')))
        self.assertEqual(ticket.promo_id, self.ten_off.id)
        self.assertEqual(UserWallet.objects.get(user=self.buyer).wallet_balance, 98)
        self.ten_off.refresh_from_db()
        self.assertEqual(self.ten_off.used_tickets, 1)

    def test_a_code_that_cannot_be_used_is_refused_before_any_money_moves(self):
        self.ten_off.is_active = False
        self.ten_off.save(update_fields=['is_active'])
        res = self.buy(promo='WALK10')
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertEqual((body['code'], body['field']), ('PROMO_INACTIVE', 'promo'))
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertEqual(UserWallet.objects.get(user=self.buyer).wallet_balance, 100)

    def test_the_last_uses_go_and_the_next_buyer_is_told(self):
        res = self.buy(promo='WALK10', quantity=2)
        self.assertEqual(res.status_code, 201, res.content)
        self.ten_off.refresh_from_db()
        self.assertEqual(self.ten_off.used_tickets, 2)
        other, other_auth = a_user('pr_other', balance=100)
        res = self.client.post('/event/%s/buy-ticket/' % self.event.slug,
                               data={'tier_id': self.ga.id, 'quantity': 1, 'pin': '1234',
                                     'promo': 'WALK10'},
                               content_type='application/json', **other_auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'PROMO_EXHAUSTED')

    def test_a_promo_tied_to_an_influencer_credits_them(self):
        ref = EventReferral.objects.create(event=self.event, code='INF', name='Inf')
        self.flat.referral = ref
        self.flat.save(update_fields=['referral'])
        res = self.buy(promo='WALKOFF')
        self.assertEqual(res.status_code, 201, res.content)
        ref.refresh_from_db()
        self.assertEqual(ref.sold, 1)
        self.assertEqual(Ticket.objects.get().referral_id, ref.id)

    # ---------------------------------------------------------- the guest

    def guest(self, **body):
        body.setdefault('tier_id', self.ga.id)
        body.setdefault('email', 'amara@example.test')
        return self.client.post('/event/%s/guest-buy/' % self.event.slug,
                                data=body, content_type='application/json')

    def test_a_guest_card_is_charged_the_discounted_price_and_the_tickets_follow(self):
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': 'sk_test'}), \
             patch('vent_event.views_guest.http_requests.post') as post:
            post.return_value.json = lambda: {
                'status': True, 'data': {'authorization_url': 'https://paystack.test/pay/abc'}}
            res = self.guest(promo='WALKOFF')
            self.assertEqual(res.status_code, 200, res.content)
            sent = post.call_args.kwargs['json']
        # 3000 less 1500 flat, in kobo, and the code rides in the metadata.
        self.assertEqual(sent['amount'], 150000)
        self.assertEqual(sent['metadata']['promo'], 'WALKOFF')

        reference = sent['reference']
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': 'sk_test'}), \
             patch('vent_event.views_guest.http_requests.get') as get:
            get.return_value.raise_for_status = lambda: None
            get.return_value.json = lambda: {
                'status': True,
                'data': {'status': 'success', 'reference': reference, 'amount': 150000,
                         'customer': {'email': 'amara@example.test'},
                         'metadata': sent['metadata']}}
            res = self.client.post('/event/guest-verify/', data={'reference': reference},
                                   content_type='application/json')
        self.assertIn(res.status_code, (200, 201), res.content)
        ticket = Ticket.objects.get()
        self.assertEqual((ticket.price_ngn, ticket.price_vc), (Decimal('1500.00'), 1))
        self.assertEqual(ticket.promo_id, self.flat.id)
        self.flat.refresh_from_db()
        self.assertEqual(self.flat.used_tickets, 1)

    def test_a_guest_with_a_dead_code_is_told_not_charged(self):
        res = self.guest(promo='NOPE')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'PROMO_UNKNOWN')
