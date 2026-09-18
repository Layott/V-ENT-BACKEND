"""The platform's cut of a comic sale is a dashboard number, 0 today.

`anime_fee_pct` in `AdminSetting.platform_fees`. Whole coins, rounded down,
off the author's credit; the reader pays the price on the label; the chapter
row carries what was taken at the rate of that moment.
"""
import uuid

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import AdminSetting, Transaction, UserWallet, Users

from .models import Chapter, ChapterPurchase, Series


def make_user(i, *, coins=0):
    user = Users.objects.create(
        username='af%s' % i, email='af%s@test.co' % i,
        login_session_token='aft%s' % str(i).zfill(11),
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
class AnimeFeeTests(TestCase):

    def setUp(self):
        self.author = make_user(1)
        self.reader = make_user(2, coins=100)
        self.series = Series.objects.create(
            author=self.author, title='Paid work', visibility='public',
            pricing='per_chapter', chapter_price_vc=20)
        self.chapter = Chapter.objects.create(series=self.series, number=1)

    def buy(self):
        return client_for(self.reader).post(
            '/anime/chapters/%s/buy/' % self.chapter.slug, {}, format='json')

    def test_at_the_default_the_author_gets_it_all(self):
        self.assertEqual(self.buy().status_code, 200)
        self.assertEqual(balance(self.reader), 80)
        self.assertEqual(balance(self.author), 20)
        self.assertEqual(ChapterPurchase.objects.get().fee_vc, 0)

    def test_the_dashboard_rate_comes_off_the_author_not_the_reader(self):
        AdminSetting.put('platform_fees', anime_fee_pct=10)
        self.assertEqual(self.buy().status_code, 200)
        self.assertEqual(balance(self.reader), 80, 'the reader pays the label')
        self.assertEqual(balance(self.author), 18)
        row = ChapterPurchase.objects.get()
        self.assertEqual((row.coins, row.fee_vc), (20, 2))
        credit = Transaction.objects.get(wallet__user=self.author)
        self.assertEqual(credit.amount, 18)
        self.assertIn('2 VC V-ENT fee', credit.description)

    def test_under_a_coin_rounds_to_nothing(self):
        AdminSetting.put('platform_fees', anime_fee_pct=4)
        self.assertEqual(self.buy().status_code, 200)
        self.assertEqual(balance(self.author), 20)
        self.assertEqual(ChapterPurchase.objects.get().fee_vc, 0)

    def test_a_change_after_the_sale_does_not_rewrite_it(self):
        AdminSetting.put('platform_fees', anime_fee_pct=10)
        self.buy()
        AdminSetting.put('platform_fees', anime_fee_pct=50)
        self.assertEqual(ChapterPurchase.objects.get().fee_vc, 2)

    def test_a_subscription_takes_the_same_cut(self):
        AdminSetting.put('platform_fees', anime_fee_pct=10)
        series = Series.objects.create(
            author=self.author, title='Monthly', visibility='public',
            pricing='subscription', subscription_price_vc=30)
        res = client_for(self.reader).post(
            '/anime/series/%s/subscribe/' % series.slug, {'months': 1}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(balance(self.reader), 70)
        self.assertEqual(balance(self.author), 27)
