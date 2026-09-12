"""Coins, and the author being paid.

Every sale here is the same three steps, so the tests are mostly about what
happens when one of them should not run: buying something you already have,
buying your own comic, buying with an empty wallet, and buying something that is
not for sale.
"""
import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Transaction, UserWallet, Users

from . import money
from .models import Chapter, ChapterPurchase, Series, SeriesSubscription


def make_user(i, *, coins=0):
    user = Users.objects.create(
        username='am%s' % i, email='am%s@test.co' % i,
        login_session_token='amt%s' % str(i).zfill(11),
        login_session_created_at=timezone.now(), is_active=True)
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user,
                              wallet_balance=coins)
    return user


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return c


def balance(user):
    return UserWallet.objects.get(user=user).wallet_balance


@override_settings(ANIME_ENABLED=True)
class BuyingAChapterTests(TestCase):

    def setUp(self):
        self.author = make_user(1)
        self.reader = make_user(2, coins=50)
        self.series = Series.objects.create(
            author=self.author, title='Paid work', visibility='public',
            pricing='per_chapter', chapter_price_vc=8)
        self.chapter = Chapter.objects.create(series=self.series, number=1)

    def test_the_coins_move_from_the_reader_to_the_author(self):
        res = client_for(self.reader).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(balance(self.reader), 42)
        self.assertEqual(balance(self.author), 8)

    def test_both_sides_get_a_transaction(self):
        client_for(self.reader).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        out = Transaction.objects.filter(wallet__user=self.reader).latest('id')
        into = Transaction.objects.filter(wallet__user=self.author).latest('id')
        self.assertEqual(out.amount, -8)
        self.assertEqual(into.amount, 8)

    def test_it_opens_the_chapter(self):
        client_for(self.reader).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        res = client_for(self.reader).get(
            '/anime/chapters/%s/' % self.chapter.slug)
        self.assertTrue(res.data['data']['can_read'])

    def test_buying_twice_is_refused(self):
        client_for(self.reader).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        res = client_for(self.reader).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'ALREADY_BOUGHT')
        self.assertEqual(balance(self.reader), 42)

    def test_an_empty_wallet_is_refused_and_nothing_moves(self):
        poor = make_user(3, coins=2)
        res = client_for(poor).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        self.assertEqual(res.status_code, 402)
        self.assertEqual(balance(poor), 2)
        self.assertEqual(balance(self.author), 0)
        self.assertEqual(ChapterPurchase.objects.count(), 0)

    def test_an_author_cannot_buy_their_own(self):
        res = client_for(self.author).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'YOUR_OWN')

    def test_a_free_series_has_nothing_to_sell(self):
        self.series.pricing = 'free'
        self.series.save(update_fields=['pricing'])
        res = client_for(self.reader).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'NOT_FOR_SALE')

    def test_a_stranger_cannot_buy(self):
        res = APIClient().post('/anime/chapters/%s/buy/' % self.chapter.slug,
                               {}, format='json')
        self.assertEqual(res.status_code, 401)


@override_settings(ANIME_ENABLED=True)
class EarlyAccessTests(TestCase):

    def setUp(self):
        self.author = make_user(4)
        self.reader = make_user(5, coins=50)
        self.series = Series.objects.create(
            author=self.author, title='Ahead', visibility='public',
            pricing='free')
        self.chapter = Chapter.objects.create(
            series=self.series, number=1, early_access_vc=6,
            published_at=timezone.now() + timedelta(days=2))

    def test_paying_opens_it_before_the_date(self):
        res = client_for(self.reader).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['reason'], 'early')
        self.assertEqual(balance(self.reader), 44)
        self.assertTrue(res.data['data']['chapter']['can_read'])

    def test_everybody_else_still_waits(self):
        other = make_user(6, coins=50)
        res = client_for(other).get('/anime/chapters/%s/' % self.chapter.slug)
        self.assertFalse(res.data['data']['can_read'])
        self.assertEqual(res.data['data']['refusal']['code'],
                         'EARLY_ACCESS_REQUIRED')

    def test_the_date_passing_costs_nobody_anything(self):
        """It is early ACCESS, not a paywall with a countdown."""
        self.chapter.published_at = timezone.now() - timedelta(minutes=1)
        self.chapter.save(update_fields=['published_at'])
        other = make_user(7)
        res = client_for(other).get('/anime/chapters/%s/' % self.chapter.slug)
        self.assertTrue(res.data['data']['can_read'])

    def test_buying_early_on_a_chapter_that_is_out_is_refused(self):
        self.chapter.published_at = timezone.now() - timedelta(minutes=1)
        self.chapter.save(update_fields=['published_at'])
        res = client_for(self.reader).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')
        # It falls through to the ordinary purchase, which a free series
        # refuses. Either way nothing is charged for something already open.
        self.assertEqual(res.status_code, 400)
        self.assertEqual(balance(self.reader), 50)


@override_settings(ANIME_ENABLED=True)
class SubscriptionTests(TestCase):

    def setUp(self):
        self.author = make_user(8)
        self.reader = make_user(9, coins=100)
        self.series = Series.objects.create(
            author=self.author, title='By the month', visibility='public',
            pricing='subscription', subscription_price_vc=20)
        Chapter.objects.create(series=self.series, number=1)

    def test_a_month_is_thirty_days(self):
        before = timezone.now()
        res = client_for(self.reader).post(
            '/anime/series/%s/subscribe/' % self.series.slug, {'months': 1},
            format='json')
        self.assertEqual(res.status_code, 200)
        row = SeriesSubscription.objects.get()
        self.assertAlmostEqual((row.until - before).days, 30, delta=1)
        self.assertEqual(balance(self.reader), 80)

    def test_three_months_costs_three_times(self):
        client_for(self.reader).post(
            '/anime/series/%s/subscribe/' % self.series.slug, {'months': 3},
            format='json')
        self.assertEqual(balance(self.reader), 40)

    def test_subscribing_again_extends_rather_than_overwrites(self):
        client_for(self.reader).post(
            '/anime/series/%s/subscribe/' % self.series.slug, {'months': 1},
            format='json')
        first = SeriesSubscription.objects.get().until
        client_for(self.reader).post(
            '/anime/series/%s/subscribe/' % self.series.slug, {'months': 1},
            format='json')
        second = SeriesSubscription.objects.get().until
        self.assertAlmostEqual((second - first).days, 30, delta=1)

    def test_a_lapsed_subscription_starts_again_today(self):
        SeriesSubscription.objects.create(
            user=self.reader, series=self.series,
            until=timezone.now() - timedelta(days=40))
        client_for(self.reader).post(
            '/anime/series/%s/subscribe/' % self.series.slug, {'months': 1},
            format='json')
        row = SeriesSubscription.objects.get()
        self.assertGreater(row.until, timezone.now() + timedelta(days=29))

    def test_subscribing_to_a_per_chapter_series_is_refused(self):
        self.series.pricing = 'per_chapter'
        self.series.save(update_fields=['pricing'])
        res = client_for(self.reader).post(
            '/anime/series/%s/subscribe/' % self.series.slug, {'months': 1},
            format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'NOT_A_SUBSCRIPTION')
        self.assertEqual(balance(self.reader), 100)
