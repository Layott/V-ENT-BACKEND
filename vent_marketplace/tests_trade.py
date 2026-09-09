"""Money, and who may move it.

The escrow is the part of this module that can lose somebody real coins, so
these tests are about balances rather than status codes: after every move, both
wallets are checked, and the sum of what everybody holds is checked against what
was there at the start.
"""
from django.test import TestCase, override_settings

from vent_auth.models import AdminSetting, Transaction, UserReport, UserWallet

from . import holds
from .models import Bid, Listing, Purchase, Review
from .tests_switch import client_for, make_user


def balances(*users):
    return [UserWallet.objects.get(user=u).wallet_balance for u in users]


@override_settings(MARKETPLACE_ENABLED=True)
class Base(TestCase):

    def setUp(self):
        self.seller = make_user(10, coins=0)
        self.buyer = make_user(11, coins=5000)
        self.listing = Listing.objects.create(
            seller=self.seller, kind='sale', category='merchandise',
            title='A signed jersey', price=1000, quantity=2, status='active')
        self.as_seller = client_for(self.seller)
        self.as_buyer = client_for(self.buyer)

    def _rate(self, pct):
        row = AdminSetting.load()
        data = dict(row.data or {})
        fees = dict(data.get('platform_fees') or {})
        fees['listing_fee_pct'] = pct
        data['platform_fees'] = fees
        row.data = data
        row.save(update_fields=['data'])


class QuoteTests(Base):

    def test_no_rate_means_no_commission(self):
        self.assertEqual(holds.quote(1000), (0, 1000))

    def test_the_rate_is_read_from_the_settings_row(self):
        self._rate(10)
        self.assertEqual(holds.quote(1000), (100, 900))

    def test_it_rounds_down(self):
        """Rounding up means taking a coin V-ENT did not earn, every sale."""
        self._rate(10)
        commission, seller = holds.quote(15)
        self.assertEqual((commission, seller), (1, 14))

    def test_the_buyer_is_told_before_they_pay(self):
        self._rate(10)
        res = self.as_buyer.get('/marketplace/listings/%s/buy/' % self.listing.slug)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['total'], 1000)
        self.assertEqual(res.json()['data']['commission'], 100)
        self.assertEqual(res.json()['data']['seller_receives'], 900)


class HoldTests(Base):

    def test_buying_needs_a_confirmation(self):
        res = self.as_buyer.post('/marketplace/listings/%s/buy/' % self.listing.slug,
                                 {}, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'CONFIRM_REQUIRED')
        self.assertEqual(balances(self.buyer), [5000])

    def test_a_hold_takes_the_coins_and_gives_them_to_nobody(self):
        res = self.as_buyer.post('/marketplace/listings/%s/buy/' % self.listing.slug,
                                 {'confirm': True}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(balances(self.buyer, self.seller), [4000, 0])
        purchase = Purchase.objects.get()
        self.assertEqual(purchase.status, 'held')
        self.assertEqual(purchase.amount, 1000)

    def test_the_stock_goes_down_when_the_money_is_held(self):
        """Two people cannot both buy the last one while the first waits."""
        self.as_buyer.post('/marketplace/listings/%s/buy/' % self.listing.slug,
                           {'confirm': True}, format='json')
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.quantity, 1)

    def test_the_last_one_marks_it_sold(self):
        self.as_buyer.post('/marketplace/listings/%s/buy/' % self.listing.slug,
                           {'confirm': True, 'quantity': 2}, format='json')
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.quantity, 0)
        self.assertEqual(self.listing.status, 'sold')

    def test_not_enough_coins_is_refused_with_both_numbers(self):
        poor = make_user(12, coins=10)
        res = client_for(poor).post(
            '/marketplace/listings/%s/buy/' % self.listing.slug,
            {'confirm': True}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INSUFFICIENT_FUNDS')
        self.assertIn('1000', res.json()['message'])
        self.assertIn('10', res.json()['message'])

    def test_a_seller_cannot_buy_their_own(self):
        res = self.as_seller.post(
            '/marketplace/listings/%s/buy/' % self.listing.slug,
            {'confirm': True}, format='json')
        self.assertEqual(res.json()['code'], 'OWN_LISTING')

    def test_not_more_than_there_are(self):
        res = self.as_buyer.post(
            '/marketplace/listings/%s/buy/' % self.listing.slug,
            {'confirm': True, 'quantity': 9}, format='json')
        self.assertEqual(res.json()['code'], 'NOT_ENOUGH')


class SettleTests(Base):

    def setUp(self):
        super().setUp()
        self._rate(10)
        self.purchase = holds.hold(self.listing, self.buyer)

    def _post(self, client, action, **extra):
        return client.post('/marketplace/purchases/%s/' % self.purchase.slug,
                           {'action': action, **extra}, format='json')

    def test_the_buyer_releases_and_the_seller_is_paid_less_the_fee(self):
        res = self._post(self.as_buyer, 'release')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(balances(self.buyer, self.seller), [4000, 900])
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, 'released')
        # And the 100 that is neither side's is the platform's, recorded on the
        # row rather than computed later from a rate that may have changed.
        self.assertEqual(self.purchase.commission, 100)

    def test_the_seller_cannot_release_to_themselves(self):
        res = self._post(self.as_seller, 'release')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(balances(self.seller), [0])

    def test_a_refund_returns_everything_including_the_fee(self):
        res = self._post(self.as_seller, 'refund')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(balances(self.buyer, self.seller), [5000, 0])

    def test_a_refund_puts_the_stock_back(self):
        self._post(self.as_seller, 'refund')
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.quantity, 2)

    def test_a_dispute_moves_nothing_and_raises_a_report(self):
        res = self._post(self.as_buyer, 'dispute', note='Never arrived.')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(balances(self.buyer, self.seller), [4000, 0])
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, 'disputed')
        report = UserReport.objects.get()
        self.assertEqual(report.reporter_id, self.buyer.user_id)
        self.assertEqual(report.reported_id, self.seller.user_id)
        self.assertTrue(report.context.startswith('marketplace:'))

    def test_a_stranger_cannot_settle_anything(self):
        stranger = client_for(make_user(13))
        self.assertEqual(self._post(stranger, 'release').status_code, 403)

    def test_it_cannot_be_settled_twice(self):
        self._post(self.as_buyer, 'release')
        again = self._post(self.as_buyer, 'release')
        self.assertEqual(again.status_code, 409)
        self.assertEqual(balances(self.seller), [900])

    def test_an_admin_can_settle_a_dispute_either_way(self):
        self._post(self.as_buyer, 'dispute')
        # A real admin: staff, through the authenticator, with a role that
        # carries the permission. Any one of the three missing means no.
        from django.utils import timezone
        admin = make_user(14)
        admin.is_staff = True
        admin.admin_role = 'finance_admin'
        admin.login_session_2fa_at = timezone.now()
        admin.save(update_fields=['is_staff', 'admin_role', 'login_session_2fa_at'])
        res = client_for(admin).post(
            '/marketplace/purchases/%s/' % self.purchase.slug,
            {'action': 'refund', 'note': 'Seller did not answer.'},
            format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(balances(self.buyer), [5000])

    def test_the_coins_are_never_in_two_places(self):
        """Whatever happens, the two wallets plus the hold add up."""
        start = sum(balances(self.buyer, self.seller)) + self.purchase.amount
        self._post(self.as_buyer, 'release')
        end = sum(balances(self.buyer, self.seller)) + self.purchase.commission
        self.assertEqual(start, end)

    def test_every_move_leaves_a_transaction(self):
        self._post(self.as_buyer, 'release')
        kinds = set(Transaction.objects.values_list('type', flat=True))
        self.assertEqual(kinds, {'deduction', 'receive'})


class ReviewTests(Base):

    def test_only_after_a_real_purchase(self):
        purchase = holds.hold(self.listing, self.buyer)
        early = self.as_buyer.post(
            '/marketplace/purchases/%s/review/' % purchase.slug,
            {'rating': 5}, format='json')
        self.assertEqual(early.status_code, 409)
        self.assertEqual(early.json()['code'], 'NOT_SETTLED')

        holds.release(purchase, by=self.buyer)
        ok = self.as_buyer.post(
            '/marketplace/purchases/%s/review/' % purchase.slug,
            {'rating': 5, 'body': 'Arrived the same day.'}, format='json')
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(Review.objects.count(), 1)

    def test_one_review_per_purchase(self):
        purchase = holds.hold(self.listing, self.buyer)
        holds.release(purchase, by=self.buyer)
        url = '/marketplace/purchases/%s/review/' % purchase.slug
        self.as_buyer.post(url, {'rating': 5}, format='json')
        again = self.as_buyer.post(url, {'rating': 1}, format='json')
        self.assertEqual(again.status_code, 409)

    def test_the_seller_cannot_review_their_own_sale(self):
        purchase = holds.hold(self.listing, self.buyer)
        holds.release(purchase, by=self.buyer)
        res = self.as_seller.post(
            '/marketplace/purchases/%s/review/' % purchase.slug,
            {'rating': 5}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_the_record_a_buyer_sees(self):
        purchase = holds.hold(self.listing, self.buyer)
        holds.release(purchase, by=self.buyer)
        self.as_buyer.post('/marketplace/purchases/%s/review/' % purchase.slug,
                           {'rating': 4}, format='json')
        res = self.as_buyer.get('/marketplace/sellers/%s/' % self.seller.username)
        record = res.json()['data']['seller']
        self.assertEqual(record['sales'], 1)
        self.assertEqual(record['reviews'], 1)
        self.assertEqual(record['rating'], 4.0)


class BiddingTests(Base):

    def setUp(self):
        super().setUp()
        self.listing.bidding = True
        self.listing.save(update_fields=['bidding'])

    def _bid(self, amount):
        return self.as_buyer.post(
            '/marketplace/listings/%s/bids/' % self.listing.slug,
            {'amount': amount}, format='json')

    def test_an_offer_is_recorded_and_the_seller_told(self):
        from vent_auth.models import Notification
        res = self._bid(700)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Bid.objects.get().amount, 700)
        self.assertTrue(Notification.objects.filter(category='marketplace').exists())

    def test_a_second_offer_replaces_the_first(self):
        self._bid(700)
        self._bid(800)
        self.assertEqual(Bid.objects.filter(status='open').count(), 1)
        self.assertEqual(Bid.objects.get(status='open').amount, 800)

    def test_a_bidder_sees_only_their_own_and_the_seller_sees_all(self):
        self._bid(700)
        other = make_user(15, coins=5000)
        client_for(other).post(
            '/marketplace/listings/%s/bids/' % self.listing.slug,
            {'amount': 900}, format='json')

        mine = self.as_buyer.get('/marketplace/listings/%s/bids/' % self.listing.slug)
        self.assertEqual(len(mine.json()['data']['bids']), 1)
        theirs = self.as_seller.get('/marketplace/listings/%s/bids/' % self.listing.slug)
        self.assertEqual(len(theirs.json()['data']['bids']), 2)

    def test_accepting_moves_no_money(self):
        """Taking coins on somebody else's press is not a thing to build."""
        self._bid(700)
        bid = Bid.objects.get()
        res = self.as_seller.post('/marketplace/bids/%s/' % bid.id,
                                  {'decision': 'accepted'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(balances(self.buyer), [5000])

    def test_an_accepted_offer_is_what_the_buyer_then_pays(self):
        self._bid(700)
        bid = Bid.objects.get()
        self.as_seller.post('/marketplace/bids/%s/' % bid.id,
                            {'decision': 'accepted'}, format='json')
        bought = self.as_buyer.post(
            '/marketplace/listings/%s/buy/' % self.listing.slug,
            {'confirm': True}, format='json')
        self.assertEqual(bought.status_code, 201, bought.content)
        self.assertEqual(Purchase.objects.get().amount, 700)
        self.assertEqual(balances(self.buyer), [4300])

    def test_only_the_seller_settles_an_offer(self):
        self._bid(700)
        bid = Bid.objects.get()
        res = self.as_buyer.post('/marketplace/bids/%s/' % bid.id,
                                 {'decision': 'accepted'}, format='json')
        self.assertEqual(res.status_code, 403)
