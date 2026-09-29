"""Coins exist only when somebody buys them.

CEO, 29 September 2026: "can we remove all fake coins from the site now, the
only way coins should exist on the site is if someone buys them, cause coins
will soon be equivalent to real money."
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import Transaction, UserWallet, Users
from .tests_topup_ceiling import make_user


def seed_user(handle, balance):
    user = Users.objects.create(username='demo_%s' % handle, email='%s@seed.v-ent.co' % handle,
                                is_active=True, login_session_token=('d-%s' % handle)[:16])
    wallet = UserWallet.objects.create(user_wallet_id=('s%s' % user.user_id)[:10], user=user,
                                       wallet_balance=balance)
    return wallet


class ClearUnboughtCoinsTests(TestCase):

    def run_cmd(self, *args):
        out = StringIO()
        call_command('clear_unbought_coins', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        wallet = seed_user('temi', 16000)
        self.assertIn('would clear 1 demo wallet(s), 16000 VC', self.run_cmd())
        wallet.refresh_from_db()
        self.assertEqual(wallet.wallet_balance, 16000)

    def test_apply_zeroes_seed_wallets_with_a_row_saying_why(self):
        wallet = seed_user('uche', 5000)
        self.run_cmd('--apply')
        wallet.refresh_from_db()
        self.assertEqual(wallet.wallet_balance, 0)
        row = Transaction.objects.get(wallet=wallet)
        self.assertEqual((row.type, row.amount, row.status), ('deduction', -5000, 'completed'))
        self.assertIn('never bought', row.description)

    def test_a_real_person_is_never_changed_only_reported(self):
        user, wallet = make_user('realperson')
        UserWallet.objects.filter(pk=wallet.pk).update(wallet_balance=7)
        Transaction.objects.create(wallet=wallet, type='top_up', amount=1, description='x',
                                   status='completed', reference='FLW-TOP-REAL')
        out = self.run_cmd('--apply')
        wallet.refresh_from_db()
        self.assertEqual(wallet.wallet_balance, 7)
        self.assertIn('LOOK AT realperson: holds 7 VC, bought 1 VC', out)

    def test_a_demo_name_on_a_real_address_is_not_a_seed_account(self):
        user = Users.objects.create(username='demo_lookalike', email='someone@gmail.com',
                                    is_active=True, login_session_token='d-lookalike')
        wallet = UserWallet.objects.create(user_wallet_id='lookalike', user=user, wallet_balance=9)
        self.run_cmd('--apply')
        wallet.refresh_from_db()
        self.assertEqual(wallet.wallet_balance, 9)

    def test_running_twice_takes_nothing_twice(self):
        wallet = seed_user('segun', 800)
        self.run_cmd('--apply')
        self.run_cmd('--apply')
        self.assertEqual(Transaction.objects.filter(wallet=wallet).count(), 1)


class SeederWritesNoCoinsOutsideDevelopmentTests(TestCase):

    @override_settings(DEBUG=False)
    def test_seed_on_a_production_box_leaves_every_wallet_empty(self):
        call_command('seed_demo', stdout=StringIO())
        demo = UserWallet.objects.filter(user__username__startswith='demo_')
        self.assertTrue(demo.exists())
        self.assertEqual(sum(w.wallet_balance for w in demo), 0)
        self.assertFalse(Transaction.objects.filter(wallet__in=demo, amount__gt=0).exists())


class SeedPayoutsAreClosedTests(TestCase):
    """A pending withdrawal from a seed account is fake coins about to become naira."""

    def test_pending_seed_payout_is_rejected_and_the_coins_are_not_returned(self):
        from .models import WithdrawalRequest
        wallet = seed_user('temi', 100)
        row = WithdrawalRequest.objects.create(wallet=wallet, amount=4000, bank_name='GTBank',
                                               account_number='0123456789', account_name='T',
                                               status='pending')
        out = StringIO()
        call_command('clear_unbought_coins', '--apply', stdout=out)
        row.refresh_from_db()
        wallet.refresh_from_db()
        self.assertEqual(row.status, 'rejected')
        self.assertIn('never bought', row.admin_note)
        self.assertEqual(wallet.wallet_balance, 0)
        self.assertIn('rejected 1 demo payout(s)', out.getvalue())

    def test_a_real_persons_payout_is_untouched(self):
        from .models import WithdrawalRequest
        user, wallet = make_user('realpayout')
        row = WithdrawalRequest.objects.create(wallet=wallet, amount=5, bank_name='GTBank',
                                               account_number='1', account_name='R', status='pending')
        call_command('clear_unbought_coins', '--apply', stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')
