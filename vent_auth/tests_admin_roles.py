"""Different admin roles really do get different access.

Asked for directly: "Then different admin roles also with different access."

The map exists in ROLE_PERMISSIONS and the console hides controls a role may not
use. That is courtesy, not enforcement - the question these tests answer is
whether the API refuses a finance admin who calls the ban endpoint anyway, which
is the only thing standing between a limited admin and a full one.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    KYCDocument, Transaction, UserWallet, Users, WithdrawalRequest,
)


def admin_with(role, name):
    user = Users.objects.create(
        username=f'{name}_admin', email=f'{name}@example.com',
        is_staff=True, admin_role=role,
        admin_session_token=f'{name}-grant',
    )
    user.admin_session_created_at = timezone.now()
    user.save(update_fields=['admin_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': f'Bearer {name}-grant'}


class AdminRoleEnforcementTests(TestCase):
    def setUp(self):
        self.superr, self.super_auth = admin_with('super_admin', 'sup')
        self.finance, self.finance_auth = admin_with('finance_admin', 'fin')
        self.mod, self.mod_auth = admin_with('mod_admin', 'mod')
        self.support, self.support_auth = admin_with('support_admin', 'sup2')

        self.victim = Users.objects.create(username='victim', email='victim@example.com')
        self.wallet = UserWallet.objects.create(
            user=self.victim, wallet_balance=Decimal('5000'), kyc_verified=True)
        self.withdrawal = WithdrawalRequest.objects.create(
            wallet=self.wallet, amount=Decimal('100'), status='pending')

    # ------------------------------------------------------------- banning
    def test_a_moderator_may_ban_and_a_finance_admin_may_not(self):
        url = reverse('admin_ban_user', args=[self.victim.pk])
        body = {'ban': True, 'reason': 'testing'}

        refused = self.client.patch(url, body, content_type='application/json',
                                    **self.finance_auth)
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()['code'], 'DO_NOT_PERMISSION_PERFORM')

        allowed = self.client.patch(url, body, content_type='application/json',
                                    **self.mod_auth)
        self.assertEqual(allowed.status_code, 200)

    # ------------------------------------------------------------- payouts
    def test_a_finance_admin_may_approve_a_payout_and_a_moderator_may_not(self):
        url = reverse('admin_approve_payout', args=[self.withdrawal.pk])

        refused = self.client.post(url, content_type='application/json', **self.mod_auth)
        self.assertEqual(refused.status_code, 403)

        allowed = self.client.post(url, content_type='application/json', **self.finance_auth)
        self.assertEqual(allowed.status_code, 200)

    # --------------------------------------------------- super admin only
    def test_only_a_super_admin_may_change_somebody_s_role(self):
        url = reverse('admin_set_user_role', args=[self.victim.pk])
        body = {'role': 'organizer'}

        for auth in (self.finance_auth, self.mod_auth, self.support_auth):
            res = self.client.patch(url, body, content_type='application/json', **auth)
            self.assertEqual(res.status_code, 403, 'a non-super admin set a role')

        ok = self.client.patch(url, body, content_type='application/json', **self.super_auth)
        self.assertEqual(ok.status_code, 200)

    def test_only_a_super_admin_may_delete_an_account(self):
        url = reverse('admin_delete_user', args=[self.victim.pk])

        for auth in (self.finance_auth, self.mod_auth, self.support_auth):
            res = self.client.delete(f'{url}?confirm=true', **auth)
            self.assertEqual(res.status_code, 403, 'a non-super admin deleted an account')

    # --------------------------------------------------- everybody can read
    def test_every_admin_role_can_see_the_dashboard(self):
        for auth in (self.super_auth, self.finance_auth, self.mod_auth, self.support_auth):
            res = self.client.get(reverse('admin_metrics'), **auth)
            self.assertEqual(res.status_code, 200)

    def test_the_refusal_says_what_role_you_have_and_what_was_needed(self):
        """A 403 that does not say why sends the admin to ask somebody."""
        res = self.client.patch(reverse('admin_ban_user', args=[self.victim.pk]),
                                {'ban': True}, content_type='application/json',
                                **self.finance_auth)

        data = res.json()['data']
        self.assertEqual(data['your_role'], 'finance_admin')
        self.assertIn('mod_admin', data['required'])

    def test_me_reports_the_permission_map_the_console_hides_controls_with(self):
        res = self.client.get(reverse('admin_me'), **self.support_auth)

        perms = res.json()['data']['permissions']
        self.assertFalse(perms['ban_users'])
        self.assertFalse(perms['approve_payouts'])
        self.assertTrue(perms['view_dashboard'])
