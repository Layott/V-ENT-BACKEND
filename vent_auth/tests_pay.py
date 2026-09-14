"""Nobody has to buy VENT COINS before they can buy anything.

CEO, 13 September 2026: "Also i hope people can still bu stuff directly on the
platform without having to buy V-ENT coins, that option must always be
vaailable."

Paystack is never called here. What is tested is the decision: what somebody
can pay with, that a saved card covers exactly the shortfall and no more, that
a reference is credited once, and that every door which refuses for want of
coins says how many were needed, which is what lets a screen offer the card
without asking the price a second time.
"""
import uuid
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import pay
from .models import (AdminSetting, Games, SavedCard, Transaction, UserWallet,
                     Users)

PIN = '2468'
KEY = {'PAYSTACK_SECRET_KEY': 'sk_live_pretend'}


def a_user(name, coins=0):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s@vent.test' % name, is_active=True, full_name=name.title(),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=uuid.uuid4().hex[:10], user=user,
        wallet_balance=coins, kyc_verified=True, pin_hash=make_password(PIN))
    return user


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return c


def a_card(user, last4='4242'):
    return SavedCard.objects.create(
        user=user, authorization_code='AUTH_%s' % uuid.uuid4().hex[:8],
        brand='visa', last4=last4, is_default=True)


def paystack_says(status_text='success'):
    """A Paystack answer, without Paystack."""
    reply = mock.Mock()
    reply.raise_for_status = lambda: None
    reply.json = lambda: {'status': True, 'data': {
        'status': status_text,
        'authorization_url': 'https://checkout.paystack.test/pay',
        'reference': 'r'}}
    return reply


class WhatCanIPayWithTests(TestCase):

    def setUp(self):
        self.user = a_user('payer', coins=3)

    def test_with_no_key_cards_are_off_and_the_screen_is_told(self):
        with mock.patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': ''}, clear=False), \
                override_settings(DEBUG=False):
            opts = pay.options(self.user)
        self.assertFalse(opts['cards_enabled'])
        self.assertIsNone(opts['saved_card'])
        self.assertEqual(opts['balance_vc'], 3)

    def test_a_saved_card_is_named_so_the_button_can_say_which(self):
        a_card(self.user, last4='1881')
        with mock.patch.dict('os.environ', KEY, clear=False):
            opts = pay.options(self.user)
        self.assertTrue(opts['cards_enabled'])
        self.assertEqual(opts['saved_card']['last4'], '1881')
        self.assertFalse(opts['test_mode'])

    def test_a_removed_card_is_not_offered(self):
        card = a_card(self.user)
        card.removed_at = timezone.now()
        card.authorization_code = ''
        card.save()
        with mock.patch.dict('os.environ', KEY, clear=False):
            self.assertIsNone(pay.options(self.user)['saved_card'])

    def test_the_shortfall_is_what_is_missing_not_the_price(self):
        wallet = UserWallet.objects.get(user=self.user)
        self.assertEqual(pay.shortfall_vc(wallet, 10), 7)
        self.assertEqual(pay.shortfall_vc(wallet, 3), 0)
        self.assertEqual(pay.shortfall_vc(wallet, 1), 0)


class CoverWithASavedCardTests(TestCase):

    def setUp(self):
        self.user = a_user('coverer', coins=2)
        self.card = a_card(self.user)

    def test_it_charges_the_coins_asked_for_and_credits_them(self):
        with mock.patch.dict('os.environ', KEY, clear=False), \
                mock.patch('vent_auth.pay.http_requests.post', return_value=paystack_says()) as post:
            out = pay.cover(self.user, 8, purpose='ticket')
        self.assertTrue(out['paid'])
        self.assertEqual(out['coins_added'], 8)
        self.assertEqual(out['amount_ngn'], 8000)
        self.assertEqual(out['balance_vc'], 10)
        sent = post.call_args.kwargs['json']
        self.assertEqual(sent['amount'], 800000, 'kobo')
        self.assertEqual(sent['metadata']['purpose'], 'ticket')
        row = Transaction.objects.get(wallet__user=self.user)
        self.assertEqual((row.type, row.amount, row.status), ('top_up', 8, 'completed'))
        self.assertIn('4242', row.description)

    def test_a_decline_takes_nothing_and_says_which(self):
        with mock.patch.dict('os.environ', KEY, clear=False), \
                mock.patch('vent_auth.pay.http_requests.post',
                           return_value=paystack_says('failed')):
            with self.assertRaises(pay.PayError) as caught:
                pay.cover(self.user, 8)
        self.assertEqual(caught.exception.code, pay.CARD_DECLINED)
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance, 2)
        self.assertFalse(Transaction.objects.exists())

    def test_a_gateway_that_does_not_answer_is_not_a_decline(self):
        with mock.patch.dict('os.environ', KEY, clear=False), \
                mock.patch('vent_auth.pay.http_requests.post', side_effect=OSError('down')):
            with self.assertRaises(pay.PayError) as caught:
                pay.cover(self.user, 8)
        self.assertEqual(caught.exception.code, pay.GATEWAY_ERROR)
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance, 2)

    def test_no_card_is_its_own_answer_so_the_caller_can_send_them_to_paystack(self):
        self.card.delete()
        with mock.patch.dict('os.environ', KEY, clear=False):
            with self.assertRaises(pay.PayError) as caught:
                pay.cover(self.user, 8)
        self.assertEqual(caught.exception.code, pay.NO_CARD)

    def test_no_paystack_key_refuses_before_anything_is_offered(self):
        with mock.patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': ''}, clear=False), \
                override_settings(DEBUG=False):
            with self.assertRaises(pay.PayError) as caught:
                pay.cover(self.user, 8)
        self.assertEqual(caught.exception.code, pay.CARDS_UNAVAILABLE)

    def test_one_reference_credits_once(self):
        with mock.patch.dict('os.environ', KEY, clear=False), \
                mock.patch('vent_auth.pay.http_requests.post', return_value=paystack_says()):
            out = pay.cover(self.user, 5)
        balance, again = pay._credit(self.user, 5, out['reference'], 'second time')
        self.assertFalse(again)
        self.assertEqual(balance, 7)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_with_no_saved_card_it_falls_through_to_a_paystack_page(self):
        self.card.delete()
        with mock.patch.dict('os.environ', KEY, clear=False), \
                mock.patch('vent_auth.pay.http_requests.post', return_value=paystack_says()):
            out = pay.cover_or_start(self.user, 6, 'https://v-ent.co/back')
        self.assertFalse(out['paid'])
        self.assertIn('checkout.paystack.test', out['authorization_url'])
        # The pending row is what `topup/verify` recognises when they return.
        row = Transaction.objects.get(reference=out['reference'])
        self.assertEqual((row.type, row.status, row.amount), ('top_up', 'pending', 6))
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance, 2)


class PayEndpointTests(TestCase):

    def setUp(self):
        self.user = a_user('endpoint', coins=4)
        self.client = client_for(self.user)

    def test_methods_says_what_to_offer(self):
        a_card(self.user, last4='0002')
        with mock.patch.dict('os.environ', KEY, clear=False):
            res = self.client.get('/auth/wallet/pay/methods/')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['data']['cards_enabled'])
        self.assertEqual(res.data['data']['saved_card']['last4'], '0002')
        self.assertEqual(res.data['data']['balance_vc'], 4)
        self.assertEqual(res.data['data']['ngn_per_coin'], 1000)

    def test_it_charges_only_the_shortfall(self):
        a_card(self.user)
        with mock.patch.dict('os.environ', KEY, clear=False), \
                mock.patch('vent_auth.pay.http_requests.post', return_value=paystack_says()) as post:
            res = self.client.post('/auth/wallet/pay/', {'coins': 10}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['data']['paid'])
        self.assertEqual(res.data['data']['needed_vc'], 6, 'ten wanted, four held')
        self.assertEqual(post.call_args.kwargs['json']['amount'], 600000)
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance, 10)

    def test_enough_coins_already_charges_nothing(self):
        a_card(self.user)
        with mock.patch.dict('os.environ', KEY, clear=False), \
                mock.patch('vent_auth.pay.http_requests.post') as post:
            res = self.client.post('/auth/wallet/pay/', {'coins': 3}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['data']['already_covered'])
        post.assert_not_called()

    def test_with_no_card_it_hands_back_a_page_to_go_to(self):
        with mock.patch.dict('os.environ', KEY, clear=False), \
                mock.patch('vent_auth.pay.http_requests.post', return_value=paystack_says()):
            res = self.client.post('/auth/wallet/pay/', {
                'coins': 9, 'callback_url': 'https://v-ent.co/events/x'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data['data']['paid'])
        self.assertIn('authorization_url', res.data['data'])

    def test_cards_not_set_up_is_503_with_a_code(self):
        with mock.patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': ''}, clear=False), \
                override_settings(DEBUG=False):
            res = self.client.post('/auth/wallet/pay/', {'coins': 9}, format='json')
        self.assertEqual(res.status_code, 503, res.data)
        self.assertEqual(res.data['code'], 'CARDS_UNAVAILABLE')

    def test_a_stranger_is_refused(self):
        res = APIClient().post('/auth/wallet/pay/', {'coins': 9}, format='json')
        self.assertGreaterEqual(res.status_code, 400)
        self.assertEqual(res.data['status'], 'error')

    def test_nothing_to_pay_is_refused_rather_than_charged(self):
        res = self.client.post('/auth/wallet/pay/', {'coins': 0}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'NOTHING_TO_PAY')

    def test_the_wallets_own_saved_card_top_up_goes_through_the_same_function(self):
        a_card(self.user)
        with mock.patch.dict('os.environ', KEY, clear=False), \
                mock.patch('vent_auth.pay.http_requests.post', return_value=paystack_says()):
            res = self.client.post('/auth/wallet/cards/charge/',
                                   {'amount_ngn': 5000}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['coins_added'], 5)
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance, 9)


@override_settings(ANIME_ENABLED=True)
class EveryDoorSaysWhatIsNeededTests(TestCase):
    """A refusal with no numbers cannot become a card payment without the
    screen asking the price a second time, and a second ask is a second
    answer the day a price changes between them."""

    def setUp(self):
        self.buyer = a_user('shortbuyer', coins=1)
        self.client = client_for(self.buyer)
        self.seller = a_user('seller', coins=0)
        self.game = Games.objects.get_or_create(game_title='Pay Game')[0]

    def _event_with_a_tier(self, price_vc=20):
        """A tier priced in naira, which is what the column holds; the
        wallet price is that over 1,000."""
        from datetime import timedelta
        from vent_event.models import Event, TicketTier
        event = Event.objects.create(
            name='Pay Con', game=self.game, creator=self.seller,
            event_type='physical', desc='x', entry_fee=0,
            reg_start_date=timezone.now() - timedelta(days=1),
            reg_end_date=timezone.now() + timedelta(days=5),
            event_date=(timezone.now() + timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00', location='Lagos')
        tier = TicketTier.objects.create(
            event=event, name='GA', price=price_vc * 1000, quantity=10)
        return event, tier

    def test_a_ticket(self):
        event, tier = self._event_with_a_tier()
        res = self.client.post('/event/%s/buy-ticket/' % event.slug, {
            'tier_id': tier.id, 'quantity': 1, 'pin': PIN}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_BALANCE')
        self.assertGreaterEqual(res.data['data']['needed_vc'], 20)
        self.assertEqual(res.data['data']['balance_vc'], 1)

    def test_a_tournament_entry(self):
        from datetime import timedelta
        from vent_tournament.models import Tournament
        t = Tournament.objects.create(
            tournament_title='Pay Cup', tournament_game=self.game,
            tournament_creator=self.seller, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Paid', entry_fee_price=15, prize_type='no_prize',
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            is_draft=False, status='registration_open')
        res = self.client.post('/tournament/join-tournament/', {
            'tournament_id': t.tournament_id, 'pin': PIN}, format='json')
        self.assertEqual(res.status_code, 422, res.data)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_BALANCE')
        self.assertEqual(res.data['needed_vc'], 15)
        self.assertEqual(res.data['balance_vc'], 1)

    def test_premium(self):
        AdminSetting.put('premium', price_vc_monthly=25, price_vc_yearly=250)
        res = self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        self.assertEqual(res.status_code, 402, res.data)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_FUNDS')
        self.assertEqual(res.data['data']['needed_vc'], 25)
        self.assertEqual(res.data['data']['balance_vc'], 1)

    def test_a_comic_chapter(self):
        from vent_anime.models import Chapter, Series
        series = Series.objects.create(
            author=self.seller, title='Paid comic', visibility='public',
            pricing='per_chapter', chapter_price_vc=9)
        chapter = Chapter.objects.create(series=series, number=1)
        res = self.client.post('/anime/chapters/%s/buy/' % chapter.slug, {}, format='json')
        self.assertEqual(res.status_code, 402, res.data)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_FUNDS')
        self.assertEqual(res.data['data']['needed_vc'], 9)
        self.assertEqual(res.data['data']['balance_vc'], 1)

    def test_the_refusals_carry_enough_to_price_a_card_payment(self):
        """The shape the screens rely on, held in one place: whatever the
        door, `needed_vc` and `balance_vc` are there to subtract."""
        event, tier = self._event_with_a_tier(price_vc=30)
        res = self.client.post('/event/%s/buy-ticket/' % event.slug, {
            'tier_id': tier.id, 'quantity': 2, 'pin': PIN}, format='json')
        data = res.data['data']
        self.assertEqual(data['needed_vc'] - data['balance_vc'], 59)
