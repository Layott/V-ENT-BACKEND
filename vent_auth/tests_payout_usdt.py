"""Paying out in USDT, as far as it can be built without a custody answer.

CEO spec, 7 September 2026: "Request payouts in USDT to my crypto wallet, to
withdraw earnings or balance."

The send itself is not here, and cannot be: whose key signs it is the custody
decision in `tasks/specs/crypto-and-custody.md`, which has not been made. What
IS here is everything around the send, all of which is identical under all
three answers, and every test below is a failure that would be permanent if it
reached production, because a chain payment does not reverse.

The two that matter most:

- **the wrong network.** USDT on TRON sent to an Ethereum address is gone,
  quietly, with a successful receipt. The addresses are both hex-ish strings
  of similar length and people paste them into the wrong field constantly.
- **an address nobody proved.** Somebody who gets into a session and types a
  destination has taken the balance, once, permanently. So an address is
  proved against the MAILBOX before anything can be sent to it.
"""
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import payouts
from . import wallets
from .models import PayoutAddress, Transaction, UserWallet, Users, \
    WithdrawalRequest

PIN = '4417'

# Real-shaped addresses, not real ones. A TRON address is T plus 33 base58
# characters; an Ethereum address is 0x plus 40 hex digits.
TRON = 'TQ5NMqJjW8sSMv3hDvHmpFRp1ohgAgV5Ln'
ETH = '0x742d35Cc6634C0532925a3b844Bc454e4438f44e'


def make_user(name, coins=0, kyc=True):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        full_name=name.title(), login_session_token=('u-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    wallet = UserWallet.objects.create(
        user_wallet_id=('w%s' % user.user_id)[:10], user=user,
        wallet_balance=coins, kyc_verified=kyc, pin_hash=make_password(PIN))
    return user, wallet


class AddressShapeTests(TestCase):
    """An address on the wrong network is money destroyed, so it is refused."""

    def test_an_ethereum_address_filed_under_tron_is_refused(self):
        with self.assertRaises(payouts.PayoutError) as caught:
            payouts.clean_address(PayoutAddress.NETWORK_TRC20, ETH)
        self.assertEqual(caught.exception.code, 'WRONG_NETWORK')

    def test_a_tron_address_filed_under_ethereum_is_refused(self):
        with self.assertRaises(payouts.PayoutError) as caught:
            payouts.clean_address(PayoutAddress.NETWORK_ERC20, TRON)
        self.assertEqual(caught.exception.code, 'WRONG_NETWORK')

    def test_the_right_shape_on_the_right_network_is_accepted(self):
        self.assertEqual(payouts.clean_address('trc20', ' %s ' % TRON),
                         ('trc20', TRON))
        self.assertEqual(payouts.clean_address('erc20', ETH),
                         ('erc20', ETH))

    def test_something_that_is_not_an_address_at_all_is_refused(self):
        for junk in ('', 'my wallet', TRON[:-1], ETH + 'ff', '0xnothex'):
            with self.assertRaises(payouts.PayoutError):
                payouts.clean_address('trc20', junk)

    def test_a_tron_address_with_a_lookalike_character_is_refused(self):
        """Base58 has no 0, O, I or l precisely so this cannot happen."""
        with self.assertRaises(payouts.PayoutError):
            payouts.clean_address('trc20', 'T0' + TRON[2:])

    def test_a_network_nobody_offers_is_refused(self):
        with self.assertRaises(payouts.PayoutError) as caught:
            payouts.clean_address('bitcoin', TRON)
        self.assertEqual(caught.exception.code, 'VALIDATION_ERROR')


@override_settings(USDT_PAYOUTS_ENABLED=True)
class AddressProofTests(TestCase):
    """An address is proved against the mailbox before it can be paid to."""

    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = make_user('usdt_owner', coins=400)
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def add(self, address=TRON, network='trc20', label='Binance'):
        return self.client.post('/auth/wallet/payout-addresses/', {
            'action': 'add', 'network': network, 'address': address,
            'label': label}, format='json')

    def test_adding_files_it_unconfirmed_and_makes_a_code(self):
        res = self.add()
        self.assertEqual(res.status_code, 200, res.data)
        row = PayoutAddress.objects.get(user=self.user)
        self.assertFalse(row.confirmed)
        self.assertEqual(len(row.confirm_code), 6)
        self.assertEqual(res.data['data']['addresses'][0]['confirmed'], False)

    def test_the_code_is_never_in_the_payload(self):
        """It goes to the mailbox. A code the session can read proves nothing."""
        res = self.add()
        self.assertNotIn(PayoutAddress.objects.get(user=self.user).confirm_code,
                         str(res.data))

    def test_an_unconfirmed_address_cannot_be_paid_to(self):
        self.add()
        ref = PayoutAddress.objects.get(user=self.user).ref
        res = self.client.post('/auth/wallet/withdraw/initiate/', {
            'amount': 50, 'pin': PIN, 'method': 'usdt', 'address_ref': ref},
            format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'ADDRESS_NOT_CONFIRMED')
        # And nothing was taken while it was refused.
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 400)
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_the_wrong_code_does_not_confirm_it(self):
        self.add()
        row = PayoutAddress.objects.get(user=self.user)
        res = self.client.post('/auth/wallet/payout-addresses/', {
            'action': 'confirm', 'ref': row.ref, 'code': '000000'},
            format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'INVALID_CODE')
        row.refresh_from_db()
        self.assertFalse(row.confirmed)

    def test_the_right_code_confirms_it_and_spends_the_code(self):
        self.add()
        row = PayoutAddress.objects.get(user=self.user)
        code = row.confirm_code
        res = self.client.post('/auth/wallet/payout-addresses/', {
            'action': 'confirm', 'ref': row.ref, 'code': code}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        row.refresh_from_db()
        self.assertTrue(row.confirmed)
        self.assertEqual(row.confirm_code, '')

    def test_an_expired_code_does_not_confirm_it(self):
        self.add()
        row = PayoutAddress.objects.get(user=self.user)
        code = row.confirm_code
        row.confirm_sent_at = timezone.now() - timedelta(hours=2)
        row.save(update_fields=['confirm_sent_at'])
        res = self.client.post('/auth/wallet/payout-addresses/', {
            'action': 'confirm', 'ref': row.ref, 'code': code}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'CODE_EXPIRED')

    def test_adding_the_same_address_twice_resends_rather_than_duplicating(self):
        self.add()
        first = PayoutAddress.objects.get(user=self.user).confirm_code
        self.add()
        self.assertEqual(PayoutAddress.objects.filter(user=self.user).count(), 1)
        self.assertNotEqual(PayoutAddress.objects.get(user=self.user).confirm_code,
                            first)

    def test_an_address_already_proved_cannot_be_re_added(self):
        self.add()
        row = PayoutAddress.objects.get(user=self.user)
        payouts.confirm_address(self.user, row.ref, row.confirm_code)
        res = self.add()
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'ALREADY_ADDED')

    def test_nobody_sees_or_confirms_somebody_elses_address(self):
        self.add()
        row = PayoutAddress.objects.get(user=self.user)
        stranger, _ = make_user('usdt_stranger')
        other = APIClient()
        other.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % stranger.login_session_token)

        listing = other.get('/auth/wallet/payout-addresses/')
        self.assertEqual(listing.data['data']['addresses'], [])

        res = other.post('/auth/wallet/payout-addresses/', {
            'action': 'confirm', 'ref': row.ref, 'code': row.confirm_code},
            format='json')
        self.assertEqual(res.status_code, 404, res.data)
        row.refresh_from_db()
        self.assertFalse(row.confirmed)

    def test_signing_out_is_the_whole_of_the_permission(self):
        anon = APIClient()
        self.assertIn(anon.get('/auth/wallet/payout-addresses/').status_code,
                      (400, 401))
        self.assertIn(anon.post('/auth/wallet/payout-addresses/', {
            'action': 'add', 'network': 'trc20', 'address': TRON},
            format='json').status_code, (400, 401))
        self.assertFalse(PayoutAddress.objects.exists())


@override_settings(USDT_PAYOUTS_ENABLED=True)
class UsdtPayoutTests(TestCase):
    """One queue, two destinations. The hold behaves the same either way."""

    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = make_user('usdt_payer', coins=400)
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)
        self.address = payouts.add_address(self.user, 'trc20', TRON, 'Binance')
        payouts.confirm_address(self.user, self.address.ref,
                                self.address.confirm_code)

    def ask(self, amount=100, **extra):
        body = {'amount': amount, 'pin': PIN, 'method': 'usdt',
                'address_ref': self.address.ref}
        body.update(extra)
        return self.client.post('/auth/wallet/withdraw/initiate/', body,
                                format='json')

    def test_a_usdt_payout_holds_the_money_like_any_other(self):
        res = self.ask(100)
        self.assertEqual(res.status_code, 201, res.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 300)
        req = WithdrawalRequest.objects.get()
        self.assertEqual(req.method, 'usdt')
        self.assertEqual(req.payout_address_id, self.address.pk)
        self.assertEqual(req.hold.status, 'pending')
        self.assertEqual(req.hold.amount, -100)

    def test_the_statement_line_says_the_network_and_the_address(self):
        self.ask(100)
        line = Transaction.objects.get(wallet=self.wallet, type='withdrawal')
        self.assertIn('TRON', line.description)
        self.assertIn(TRON[:6], line.description)
        # Never the whole address on a statement line.
        self.assertNotIn(TRON, line.description)

    def test_one_payout_is_still_one_line(self):
        self.ask(100)
        self.assertEqual(Transaction.objects.filter(
            wallet=self.wallet, type='withdrawal').count(), 1)

    def test_denying_a_usdt_payout_gives_the_money_back(self):
        self.ask(100)
        req = WithdrawalRequest.objects.get()
        ok, why = wallets.return_payout(req, 'not this time')
        self.assertTrue(ok, why)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 400)

    def test_the_wrong_pin_moves_nothing(self):
        res = self.ask(100, pin='0000')
        self.assertEqual(res.status_code, 400, res.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 400)
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_an_address_ref_that_is_not_theirs_is_a_404(self):
        stranger, _ = make_user('usdt_third')
        theirs = payouts.add_address(stranger, 'erc20', ETH)
        res = self.ask(100, address_ref=theirs.ref)
        self.assertEqual(res.status_code, 404, res.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 400)

    def test_a_usdt_request_with_no_address_is_refused(self):
        res = self.client.post('/auth/wallet/withdraw/initiate/', {
            'amount': 100, 'pin': PIN, 'method': 'usdt'}, format='json')
        self.assertEqual(res.status_code, 404, res.data)

    def test_a_method_nobody_offers_is_refused(self):
        res = self.ask(100, method='paypal')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'VALIDATION_ERROR')

    def test_the_bank_route_still_works_untouched(self):
        res = self.client.post('/auth/wallet/withdraw/initiate/', {
            'amount': 100, 'pin': PIN, 'bank_name': 'GTBank',
            'account_number': '0123456789', 'account_name': 'Usdt Payer',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        req = WithdrawalRequest.objects.get()
        self.assertEqual(req.method, 'bank')
        self.assertIsNone(req.payout_address)
        self.assertIn('GTBank', req.hold.description)

    def test_the_history_says_where_each_one_went(self):
        self.ask(100)
        res = self.client.get('/auth/wallet/withdraw/status/')
        self.assertEqual(res.status_code, 200)
        row = res.data['data'][0]
        self.assertEqual(row['method'], 'usdt')
        self.assertIn('TRON', row['destination'])
        self.assertEqual(row['reference'], '')

    def test_an_address_that_has_paid_out_is_not_deleted_by_removing_it(self):
        """Where money went is not a thing somebody can erase from history."""
        self.ask(100)
        payouts.remove_address(self.user, self.address.ref)
        self.address.refresh_from_db()
        self.assertFalse(self.address.confirmed)
        self.assertTrue(PayoutAddress.objects.filter(pk=self.address.pk).exists())
        self.assertTrue(WithdrawalRequest.objects.filter(
            payout_address=self.address).exists())

    def test_an_address_that_has_paid_nothing_is_removed_outright(self):
        row = payouts.add_address(self.user, 'erc20', ETH)
        payouts.remove_address(self.user, row.ref)
        self.assertFalse(PayoutAddress.objects.filter(pk=row.pk).exists())


class NotOpenYetTests(TestCase):
    """USDT payouts are off until the custody decision, and say so.

    Off is not the same as absent. The whole request pipeline is built and
    every test above proves it; what it waits on is whose key signs the send
    and a float behind it. Holding somebody's balance for a payout nobody can
    complete is worse than not offering it, so the refusal happens BEFORE the
    hold and carries a code the screen can read.
    """

    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = make_user('usdt_closed', coins=400)
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def test_the_default_is_off(self):
        self.assertFalse(payouts.usdt_enabled())

    def test_asking_is_refused_and_nothing_is_held(self):
        with override_settings(USDT_PAYOUTS_ENABLED=True):
            addr = payouts.add_address(self.user, 'trc20', TRON)
            payouts.confirm_address(self.user, addr.ref, addr.confirm_code)

        res = self.client.post('/auth/wallet/withdraw/initiate/', {
            'amount': 50, 'pin': PIN, 'method': 'usdt',
            'address_ref': addr.ref}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'USDT_NOT_OPEN')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 400)
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_the_screen_is_told_which_way_the_switch_is(self):
        res = self.client.get('/auth/wallet/payout-addresses/')
        self.assertFalse(res.data['data']['usdt_enabled'])
        with override_settings(USDT_PAYOUTS_ENABLED=True):
            res = self.client.get('/auth/wallet/payout-addresses/')
            self.assertTrue(res.data['data']['usdt_enabled'])

    def test_the_bank_rail_is_unaffected_by_the_switch(self):
        res = self.client.post('/auth/wallet/withdraw/initiate/', {
            'amount': 50, 'pin': PIN, 'bank_name': 'GTBank',
            'account_number': '0123456789', 'account_name': 'Closed',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)


@override_settings(PAYOUT_MINIMUM_VC=5, PAYOUT_DAILY_MAX_VC=200)
class LimitTests(TestCase):
    """The ceilings are rail independent, which is why they are built now.

    A daily limit is the same number whether the money leaves as naira or as
    USDT, and it is what caps how much a stolen account can take before
    anybody looks at the queue.
    """

    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = make_user('usdt_limits', coins=1000)
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def bank(self, amount):
        return self.client.post('/auth/wallet/withdraw/initiate/', {
            'amount': amount, 'pin': PIN, 'bank_name': 'GTBank',
            'account_number': '0123456789', 'account_name': 'Limits',
        }, format='json')

    def test_below_the_minimum_is_refused(self):
        res = self.bank(2)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'BELOW_MINIMUM')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 1000)

    def test_the_daily_ceiling_counts_requests_not_approvals(self):
        """Twenty requests nobody has looked at is exactly the case."""
        self.assertEqual(self.bank(150).status_code, 201)
        res = self.bank(100)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'OVER_DAILY_LIMIT')
        self.assertEqual(WithdrawalRequest.objects.count(), 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 850)

    def test_a_rejected_request_does_not_use_up_the_day(self):
        first = self.bank(150)
        self.assertEqual(first.status_code, 201)
        row = WithdrawalRequest.objects.get()
        row.status = 'rejected'
        row.save(update_fields=['status'])
        self.assertEqual(self.bank(150).status_code, 201)

    def test_exactly_the_ceiling_is_allowed(self):
        self.assertEqual(self.bank(200).status_code, 201)

    @override_settings(PAYOUT_DAILY_MAX_VC=0)
    def test_zero_means_no_ceiling(self):
        self.assertEqual(self.bank(900).status_code, 201)


class DestinationSentenceTests(TestCase):
    """One function names where a payout went, so no two screens disagree."""

    def setUp(self):
        self.user, self.wallet = make_user('usdt_words', coins=100)

    def test_a_bank_payout_reads_as_a_bank_payout(self):
        row = WithdrawalRequest(wallet=self.wallet, amount=10,
                                bank_name='GTBank',
                                account_number='0123456789')
        self.assertEqual(payouts.describe_destination(row),
                         'Withdrawal to GTBank 6789')

    def test_a_usdt_payout_names_the_network(self):
        addr = payouts.add_address(self.user, 'erc20', ETH)
        row = WithdrawalRequest(wallet=self.wallet, amount=10,
                                method='usdt', payout_address=addr)
        said = payouts.describe_destination(row)
        self.assertIn('Ethereum', said)
        self.assertIn('ERC-20', said)
        self.assertIn(ETH[:6], said)
