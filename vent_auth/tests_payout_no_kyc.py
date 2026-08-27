"""Approving a payout while KYC is switched off.

The console's Approve button answered 400 "Cannot approve - user is not KYC
verified" for every pending withdrawal. KYC is not in use, so nobody can become
verified, so no payout could ever be approved: the entire payouts flow was dead
behind a gate for a feature that is turned off.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Transaction, UserWallet, Users, WithdrawalRequest


class PayoutApprovalWithoutKycTests(TestCase):
    def setUp(self):
        self.admin = Users.objects.create(
            username='payout_admin', email='payout_admin@example.com',
            is_staff=True, admin_role='super_admin',
            admin_session_token='payout-admin-grant')
        self.admin.admin_session_created_at = timezone.now()
        self.admin.save(update_fields=['admin_session_created_at'])

        self.player = Users.objects.create(username='cashing_out',
                                           email='cashing_out@example.com')
        self.wallet = UserWallet.objects.create(
            user=self.player, wallet_balance=Decimal('5000'), kyc_verified=False)
        self.withdrawal = WithdrawalRequest.objects.create(
            wallet=self.wallet, amount=Decimal('1000'), status='pending')

    def test_an_unverified_wallet_can_still_be_paid_out(self):
        response = self.client.post(
            reverse('admin_approve_payout', args=[self.withdrawal.id]),
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer payout-admin-grant')

        self.assertEqual(response.status_code, 200, response.content[:200])
        self.withdrawal.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(self.withdrawal.status, 'approved')
        # The money actually moved, and left a transaction behind.
        self.assertEqual(self.wallet.wallet_balance, Decimal('4000'))
        self.assertTrue(Transaction.objects.filter(wallet=self.wallet,
                                                   type='withdrawal').exists())

    def test_a_wallet_without_the_money_is_still_refused(self):
        """Removing the KYC gate must not remove the balance check."""
        self.wallet.wallet_balance = Decimal('10')
        self.wallet.save(update_fields=['wallet_balance'])

        response = self.client.post(
            reverse('admin_approve_payout', args=[self.withdrawal.id]),
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer payout-admin-grant')

        self.assertNotEqual(response.status_code, 200)
        self.withdrawal.refresh_from_db()
        self.assertEqual(self.withdrawal.status, 'pending')
