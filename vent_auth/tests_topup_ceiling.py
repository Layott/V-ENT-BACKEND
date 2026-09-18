"""The daily top-up ceiling is a dashboard number, and it is enforced.

`topup_max_ngn_per_day` sat on the admin settings page from the day the page
was built and nothing read it. It is checked before Paystack is asked for a
link, because a refusal after somebody has paid is a refund, not a refusal.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AdminSetting, Transaction, UserWallet, Users


def make_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        full_name=name.title(), login_session_token=('u-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    wallet = UserWallet.objects.create(
        user_wallet_id=('w%s' % user.user_id)[:10], user=user, wallet_balance=0)
    return user, wallet


def paystack_says_yes(*args, **kwargs):
    reply = mock.Mock()
    reply.raise_for_status = lambda: None
    reply.json = lambda: {'status': True, 'data': {
        'authorization_url': 'https://checkout.paystack.test/x', 'reference': 'r'}}
    return reply


class TopUpCeilingTests(TestCase):

    def setUp(self):
        self.user, self.wallet = make_user('ceiling')
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def topup(self, ngn):
        with mock.patch('vent_auth.views_wallet.http_requests.post', paystack_says_yes):
            return self.client.post('/auth/wallet/topup/initiate/',
                                    {'amount_ngn': ngn}, format='json')

    def test_zero_means_no_ceiling(self):
        self.assertEqual(self.topup(900000).status_code, 200)

    def test_over_the_ceiling_is_refused_before_paystack_is_asked(self):
        AdminSetting.put('platform_fees', topup_max_ngn_per_day=50000)
        self.assertEqual(self.topup(30000).status_code, 200)
        res = self.topup(30000)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'OVER_TOPUP_LIMIT')
        self.assertEqual(res.data['daily_max_ngn'], 50000)
        self.assertEqual(res.data['already_ngn'], 30000)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 1,
                         'the refused top-up wrote no pending row')

    def test_exactly_the_ceiling_is_allowed(self):
        AdminSetting.put('platform_fees', topup_max_ngn_per_day=50000)
        self.assertEqual(self.topup(30000).status_code, 200)
        self.assertEqual(self.topup(20000).status_code, 200)

    def test_yesterday_does_not_count(self):
        AdminSetting.put('platform_fees', topup_max_ngn_per_day=50000)
        old = Transaction.objects.create(
            wallet=self.wallet, type='top_up', amount=40, status='completed',
            description='old', reference='old-ref')
        Transaction.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=2))
        self.assertEqual(self.topup(50000).status_code, 200)

    def test_pending_rows_count_because_they_are_the_case_a_ceiling_exists_for(self):
        AdminSetting.put('platform_fees', topup_max_ngn_per_day=50000)
        for _ in range(5):
            self.assertEqual(self.topup(10000).status_code, 200)
        res = self.topup(1000)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'OVER_TOPUP_LIMIT')

    def test_the_minimum_carries_a_code_now(self):
        res = self.topup(500)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'BELOW_MINIMUM_TOPUP')
