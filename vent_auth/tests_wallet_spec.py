"""The VENT WALLET spec, in tests.

CEO, 7 September 2026, row 192. The lines this file covers are the ones where
being wrong is expensive and silent:

- a person sending to a TEAM or an ORGANISATION, which the spec asks for in its
  first user-wallet line and which the endpoint could not do until 8 September
- a payout HOLDING the money when it is asked for, so a balance on a screen is
  a balance somebody can rely on
- a denial giving the held money back
- the second factor, on top of the PIN, for the people who have turned it on
- two debits arriving at once, where the wrong answer creates money

Every test here was written from the request a screen actually makes. A payload
nothing sends is a passing test about nothing.
"""
import threading

from django.contrib.auth.hashers import check_password, make_password
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import totp as totp_lib
from . import wallets
from .models import (Games, KYCDocument, OrgMember, Organization, TeamMembers,
                     Teams, Transaction, UserTOTP, UserWallet, Users,
                     WithdrawalRequest)

PIN = '4417'


def make_user(name, coins=0, pin=PIN, kyc=False):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        full_name=name.title(), login_session_token=('s-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    wallet = UserWallet.objects.create(
        user_wallet_id=('u%s' % user.user_id)[:10], user=user,
        wallet_balance=coins, kyc_verified=kyc,
        pin_hash=make_password(pin) if pin else None)
    return user, wallet


def auth(user):
    return {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


# ---------------------------------------------------------------------------
# "Send VENT COINS to other users, organizations, teams, tournament organizers"
# ---------------------------------------------------------------------------

class SendingToEveryHolderTests(TestCase):
    """The user wallet could only ever pay another person.

    A team wallet that only another team could put money into is a wallet
    nobody can use, and it stayed that way for as long as it did because the
    endpoint's own name, `recipient_username`, said person and nobody read it
    as a limitation.
    """

    def setUp(self):
        self.client = APIClient()
        self.payer, self.payer_wallet = make_user('spec_payer', coins=500)
        self.payee, _ = make_user('spec_payee')
        game = Games.objects.create(game_title='EA FC Spec')
        self.team = Teams.objects.create(
            team_name='Spec Rangers', game=game, description='x',
            team_creator=self.payer, team_owner=self.payer,
            penalty_points=0, number_of_members=1)
        self.org = Organization.objects.create(
            org_name='Spec Org', org_creator=self.payer, org_owner=self.payer)

    def send(self, **body):
        return self.client.post('/auth/wallet/send/', dict(pin=PIN, **body),
                                format='json', **auth(self.payer))

    def test_a_person_pays_a_person(self):
        res = self.send(to_kind='user', to=self.payee.username, amount=100)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            UserWallet.objects.get(user=self.payee).wallet_balance, 100)
        self.payer_wallet.refresh_from_db()
        self.assertEqual(self.payer_wallet.wallet_balance, 400)

    def test_a_person_pays_a_team(self):
        res = self.send(to_kind='team', to=self.team.slug, amount=150)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            wallets.wallet_for_team(self.team).wallet_balance, 150)

    def test_a_person_pays_an_organisation(self):
        res = self.send(to_kind='org', to=self.org.slug, amount=75)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(wallets.wallet_for_org(self.org).wallet_balance, 75)

    def test_the_old_payload_the_send_screen_sends_still_works(self):
        """`/wallets/send` posts `recipient_username`, and has since it shipped.

        Breaking it to make room for `to_kind` would have been a rename
        disguised as a feature.
        """
        res = self.client.post(
            '/auth/wallet/send/',
            {'recipient_username': self.payee.username, 'amount': 40,
             'pin': PIN},
            format='json', **auth(self.payer))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            UserWallet.objects.get(user=self.payee).wallet_balance, 40)

    def test_person_to_person_still_reads_as_send_and_receive(self):
        """Two words on the two statements, because it has always had two.

        Renaming both to `transfer` would rewrite the meaning of every row
        already written.
        """
        self.send(to_kind='user', to=self.payee.username, amount=10)
        self.assertTrue(Transaction.objects.filter(
            wallet=self.payer_wallet, type='send').exists())
        self.assertTrue(Transaction.objects.filter(
            wallet__user=self.payee, type='receive').exists())

    def test_money_to_a_team_is_a_transfer_on_both_sides(self):
        self.send(to_kind='team', to=self.team.slug, amount=10)
        self.assertTrue(Transaction.objects.filter(
            wallet=self.payer_wallet, type='transfer').exists())
        self.assertTrue(Transaction.objects.filter(
            team_wallet__team=self.team, type='transfer').exists())

    def test_the_wrong_pin_moves_nothing_wherever_it_is_going(self):
        for kind, ref in (('user', self.payee.username),
                          ('team', self.team.slug), ('org', self.org.slug)):
            res = self.client.post(
                '/auth/wallet/send/',
                {'to_kind': kind, 'to': ref, 'amount': 10, 'pin': '0000'},
                format='json', **auth(self.payer))
            self.assertEqual(res.status_code, 400, (kind, res.data))
        self.payer_wallet.refresh_from_db()
        self.assertEqual(self.payer_wallet.wallet_balance, 500)

    def test_no_pin_at_all_moves_nothing(self):
        """Send and Withdraw once shipped without sending the PIN and could
        never succeed. The refusal is what proves the page has to send it."""
        res = self.client.post(
            '/auth/wallet/send/',
            {'to_kind': 'user', 'to': self.payee.username, 'amount': 10},
            format='json', **auth(self.payer))
        self.assertEqual(res.status_code, 400, res.data)
        self.payer_wallet.refresh_from_db()
        self.assertEqual(self.payer_wallet.wallet_balance, 500)

    def test_more_than_the_balance_moves_nothing(self):
        res = self.send(to_kind='team', to=self.team.slug, amount=5000)
        self.assertEqual(res.status_code, 400, res.data)
        self.payer_wallet.refresh_from_db()
        self.assertEqual(self.payer_wallet.wallet_balance, 500)
        self.assertEqual(wallets.wallet_for_team(self.team).wallet_balance, 0)

    def test_a_team_that_does_not_exist_is_a_404_not_a_guess(self):
        res = self.send(to_kind='team', to='no-such-team', amount=10)
        self.assertEqual(res.status_code, 404, res.data)

    def test_a_kind_that_is_not_one_of_the_three_is_refused(self):
        res = self.send(to_kind='tournament', to='anything', amount=10)
        self.assertEqual(res.status_code, 400, res.data)

    def test_nobody_can_send_to_themselves(self):
        res = self.send(to_kind='user', to=self.payer.username, amount=10)
        self.assertEqual(res.status_code, 400, res.data)


# ---------------------------------------------------------------------------
# "Request payouts ... Admin: approve or deny payout requests"
# ---------------------------------------------------------------------------

class PayoutHoldTests(TestCase):
    """A payout used to hold nothing.

    The request wrote a pending row and touched no balance, so somebody could
    ask for their whole balance, spend it, and have the approval fail on them
    days later. Approval then wrote a SECOND withdrawal line, so one payout
    appeared on the statement twice and a rejection left the first pending for
    ever.
    """

    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = make_user('spec_payout', coins=200, kyc=True)
        self.other, _ = make_user('spec_other')
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.user.login_session_token)

    def request_payout(self, amount=80):
        return self.client.post('/auth/wallet/withdraw/initiate/', {
            'amount': amount, 'pin': PIN, 'bank_name': 'GTBank',
            'account_number': '0123456789', 'account_name': 'Spec Payout',
        }, format='json')

    def test_asking_takes_the_money_out_of_the_spendable_balance(self):
        res = self.request_payout(80)
        self.assertEqual(res.status_code, 201, res.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 120)

    def test_the_held_money_cannot_then_be_spent(self):
        """The whole point of a hold, in one test."""
        self.request_payout(200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 0)
        res = self.client.post('/auth/wallet/send/', {
            'to_kind': 'user', 'to': self.other.username,
            'amount': 50, 'pin': PIN}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_BALANCE')

    def test_one_payout_is_one_line_on_the_statement(self):
        self.request_payout(80)
        self.assertEqual(Transaction.objects.filter(
            wallet=self.wallet, type='withdrawal').count(), 1)

    def test_the_request_carries_the_line_the_money_is_sitting_on(self):
        self.request_payout(80)
        req = WithdrawalRequest.objects.get(wallet=self.wallet)
        self.assertIsNotNone(req.hold)
        self.assertEqual(req.hold.status, 'pending')
        self.assertEqual(req.hold.amount, -80)

    def test_approving_settles_the_line_it_does_not_write_another(self):
        self.request_payout(80)
        req = WithdrawalRequest.objects.get(wallet=self.wallet)
        ok, why = wallets.settle_payout(req)
        self.assertTrue(ok, why)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 120)
        self.assertEqual(Transaction.objects.filter(
            wallet=self.wallet, type='withdrawal').count(), 1)
        req.hold.refresh_from_db()
        self.assertEqual(req.hold.status, 'completed')

    def test_denying_gives_the_held_money_back(self):
        self.request_payout(80)
        req = WithdrawalRequest.objects.get(wallet=self.wallet)
        wallets.return_payout(req, 'Account name does not match')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 200)
        req.hold.refresh_from_db()
        self.assertEqual(req.hold.status, 'cancelled')
        self.assertIn('returned', req.hold.description)

    def test_a_denial_does_not_write_a_second_positive_line(self):
        """A refund line beside a debit line still on the statement would make
        the statement sum to more than the balance."""
        self.request_payout(80)
        req = WithdrawalRequest.objects.get(wallet=self.wallet)
        wallets.return_payout(req, 'no')
        self.wallet.refresh_from_db()
        completed = sum(Transaction.objects.filter(
            wallet=self.wallet, status='completed'
        ).values_list('amount', flat=True))
        self.assertEqual(completed, 0)
        self.assertEqual(self.wallet.wallet_balance, 200)

    def test_returning_twice_gives_the_money_back_once(self):
        self.request_payout(80)
        req = WithdrawalRequest.objects.get(wallet=self.wallet)
        wallets.return_payout(req, 'no')
        req.refresh_from_db()
        wallets.return_payout(req, 'no again')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 200)

    def test_a_request_made_before_holds_existed_is_still_debited_on_approval(self):
        """Every row written before 8 September has no hold and was never
        debited. Approving one of those must still take the money, and the
        only thing that can tell the two apart is whether a hold exists."""
        old = WithdrawalRequest.objects.create(
            wallet=self.wallet, amount=50, bank_name='GTBank',
            account_number='0123456789', account_name='Spec Payout')
        self.assertIsNone(old.hold)
        ok, why = wallets.settle_payout(old)
        self.assertTrue(ok, why)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 150)

    def test_an_old_request_larger_than_the_balance_is_refused_not_paid(self):
        old = WithdrawalRequest.objects.create(
            wallet=self.wallet, amount=5000, bank_name='GTBank',
            account_number='0123456789', account_name='Spec Payout')
        ok, why = wallets.settle_payout(old)
        self.assertFalse(ok)
        self.assertIn('Insufficient', why)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 200)

    # The ceiling is switched off for this one, because 5000 is over BOTH the
    # daily limit and the balance and the limit is checked first. Without
    # this the test still passes and proves the other thing, which is how a
    # test quietly stops covering what its name says.
    @override_settings(PAYOUT_DAILY_MAX_VC=0)
    def test_asking_for_more_than_there_is_records_no_request(self):
        res = self.request_payout(5000)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_BALANCE')
        self.assertFalse(WithdrawalRequest.objects.exists())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 200)


# ---------------------------------------------------------------------------
# "Secure my wallet with two-factor authentication and a PIN"
# ---------------------------------------------------------------------------

def enrol(user):
    """Give this account a confirmed authenticator, and return a live code."""
    factor = UserTOTP.objects.create(
        user=user, secret=totp_lib.generate_secret(), confirmed=True,
        confirmed_at=timezone.now())
    return factor


def code_for(factor):
    from . import totp as t
    return t._code_for_step(factor.secret, t.current_step())


class SecondFactorTests(TestCase):
    """The PIN and the code are not interchangeable.

    A PIN is typed into this site and lives in its database. The code comes off
    a device the site has never seen. Somebody who set up an authenticator has
    said they want the second one, so their money is held to it on every debit,
    with no separate switch to forget to turn on.
    """

    def setUp(self):
        self.client = APIClient()
        self.user, self.wallet = make_user('spec_2fa', coins=300, kyc=True)
        self.payee, _ = make_user('spec_2fa_payee')

    def send(self, **body):
        return self.client.post(
            '/auth/wallet/send/',
            dict(to_kind='user', to=self.payee.username, amount=10, pin=PIN,
                 **body),
            format='json', **auth(self.user))

    def test_somebody_with_no_authenticator_is_not_asked_for_a_code(self):
        """Nothing changes for the people who have not enrolled."""
        res = self.send()
        self.assertEqual(res.status_code, 200, res.data)

    def test_an_enrolled_account_cannot_send_without_a_code(self):
        enrol(self.user)
        res = self.send()
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'TWO_FACTOR_REQUIRED')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 300)

    def test_a_wrong_code_moves_nothing(self):
        enrol(self.user)
        res = self.send(code='000000')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'INVALID_CODE')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 300)

    def test_the_right_code_lets_it_through(self):
        factor = enrol(self.user)
        res = self.send(code=code_for(factor))
        self.assertEqual(res.status_code, 200, res.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 290)

    def test_a_code_cannot_be_spent_twice(self):
        """The step is burned on use, which is what stops somebody replaying
        a code they watched being typed."""
        factor = enrol(self.user)
        code = code_for(factor)
        self.assertEqual(self.send(code=code).status_code, 200)
        res = self.send(code=code)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'INVALID_CODE')

    def test_a_payout_asks_for_it_too(self):
        enrol(self.user)
        res = self.client.post('/auth/wallet/withdraw/initiate/', {
            'amount': 50, 'pin': PIN, 'bank_name': 'GTBank',
            'account_number': '0123456789', 'account_name': 'Spec',
        }, format='json', **auth(self.user))
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'TWO_FACTOR_REQUIRED')
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_the_balance_says_whether_a_code_will_be_asked_for(self):
        """So the screen asks for one only when there is something to type,
        rather than showing a field nobody can fill in."""
        res = self.client.get('/auth/wallet/balance/', **auth(self.user))
        self.assertFalse(res.data['data']['requires_2fa'])
        enrol(self.user)
        res = self.client.get('/auth/wallet/balance/', **auth(self.user))
        self.assertTrue(res.data['data']['requires_2fa'])


class SharedWalletSecondFactorTests(TestCase):
    """The code belongs to the person pressing send, not to the team.

    A shared wallet has no device of its own, and the person who moved the
    money is who anybody would want to ask about it afterwards.
    """

    def setUp(self):
        self.client = APIClient()
        self.owner, _ = make_user('spec_tw_owner', coins=0)
        self.payee, _ = make_user('spec_tw_payee')
        game = Games.objects.create(game_title='EA FC TW')
        self.team = Teams.objects.create(
            team_name='Spec Wallet FC', game=game, description='x',
            team_creator=self.owner, team_owner=self.owner,
            penalty_points=0, number_of_members=1)
        self.team_wallet = wallets.wallet_for_team(self.team)
        self.team_wallet.wallet_balance = 100
        self.team_wallet.pin_hash = make_password(PIN)
        self.team_wallet.save()

    def send(self, **body):
        return self.client.post(
            '/auth/team/%s/wallet/' % self.team.slug,
            dict(action='send', to_kind='user', to=self.payee.username,
                 amount=10, pin=PIN, **body),
            format='json', **auth(self.owner))

    def test_an_enrolled_leader_must_produce_a_code_to_spend_team_money(self):
        enrol(self.owner)
        res = self.send()
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'TWO_FACTOR_REQUIRED')
        self.team_wallet.refresh_from_db()
        self.assertEqual(self.team_wallet.wallet_balance, 100)

    def test_with_the_code_the_team_money_moves(self):
        factor = enrol(self.owner)
        res = self.send(code=code_for(factor))
        self.assertEqual(res.status_code, 200, res.data)
        self.team_wallet.refresh_from_db()
        self.assertEqual(self.team_wallet.wallet_balance, 90)


# ---------------------------------------------------------------------------
# Every direction the spec names, through the one function
# ---------------------------------------------------------------------------

class EveryDirectionTests(TestCase):
    """Seven directions are named in the spec and all seven are one function.

    Written out separately they would be seven copies of "take from one, give
    to the other, write both lines", which is seven chances for one of them to
    forget a line.
    """

    def setUp(self):
        self.person, self.person_wallet = make_user('spec_dir_person', coins=1000)
        self.member, _ = make_user('spec_dir_member')
        game = Games.objects.create(game_title='EA FC Dir')
        self.team = Teams.objects.create(
            team_name='Direction United', game=game, description='x',
            team_creator=self.person, team_owner=self.person,
            penalty_points=0, number_of_members=2)
        TeamMembers.objects.create(team=self.team, user=self.member)
        self.org = Organization.objects.create(
            org_name='Direction Org', org_creator=self.person,
            org_owner=self.person)
        OrgMember.objects.create(org=self.org, user=self.member, role='member')
        self.team_wallet = wallets.wallet_for_team(self.team)
        self.org_wallet = wallets.wallet_for_org(self.org)

    def test_all_seven(self):
        member_wallet = UserWallet.objects.get(user=self.member)
        self.team_wallet.wallet_balance = 500
        self.team_wallet.save()
        self.org_wallet.wallet_balance = 500
        self.org_wallet.save()

        moves = [
            ('user to user', self.person_wallet, member_wallet),
            ('user to team', self.person_wallet, self.team_wallet),
            ('user to org', self.person_wallet, self.org_wallet),
            ('team to member', self.team_wallet, member_wallet),
            ('team to org', self.team_wallet, self.org_wallet),
            ('org to team', self.org_wallet, self.team_wallet),
            ('org to user', self.org_wallet, self.person_wallet),
        ]
        for label, src, dst in moves:
            # Read FRESH both sides. An earlier direction in this loop may have
            # moved the very wallet being measured.
            src.refresh_from_db()
            dst.refresh_from_db()
            before_src = src.wallet_balance
            before_dst = dst.wallet_balance
            wallets.transfer(src, dst, 10)
            src.refresh_from_db()
            dst.refresh_from_db()
            self.assertEqual(src.wallet_balance, before_src - 10, label)
            self.assertEqual(dst.wallet_balance, before_dst + 10, label)

    def test_a_statement_always_adds_up_to_its_balance(self):
        member_wallet = UserWallet.objects.get(user=self.member)
        for _ in range(5):
            wallets.transfer(self.person_wallet, member_wallet, 7)
            wallets.transfer(self.person_wallet, self.team_wallet, 3)
        for wallet, started in ((self.person_wallet, 1000),
                                (member_wallet, 0),
                                (self.team_wallet, 0)):
            wallet.refresh_from_db()
            moved = sum(row['amount'] for row in wallets.statement(wallet))
            self.assertEqual(wallet.wallet_balance, started + moved)


# ---------------------------------------------------------------------------
# Two debits arriving at once
# ---------------------------------------------------------------------------

class ConcurrentDebitTests(TransactionTestCase):
    """Two people spending the same wallet at the same moment.

    Reading a balance and then writing it back is how money is made out of
    nothing under load, and it is invisible in every single-threaded test. The
    unsafe version of this was measured: two threads each spending 60 from a
    balance of 100 both succeeded, and the balance finished at MINUS 20.

    ## Why this runs the race several times

    The two databases lose differently. MySQL holds the row with
    `select_for_update`, so the loser reads the new balance and is refused for
    being short. SQLite has no row locks and serialises writers with a lock over
    the whole database, so under load BOTH threads can come back with
    "database is locked" and nothing moves at all.

    That second behaviour also limits what this test can prove HERE. SQLite
    serialising every writer means a transfer with the row lock taken out can
    still come out correct under this test, so a green result on SQLite is not
    on its own evidence that the lock exists. `RowLockTests` below is what
    covers that, structurally, on any backend.

    Nothing moving is correct. It is just not informative, and a test that
    accepts it every time would also pass on code that never works. So every
    round asserts the INVARIANT, which must hold whatever happens, and the test
    additionally requires that at least one round got far enough to be decisive.
    Without that second requirement a green result would mean nothing.
    """

    reset_sequences = True

    ROUNDS = 6

    def _one_race(self, wallet, payee_wallet):
        """Two threads, one affordable amount. Returns the outcomes."""
        start = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def spend():
            start.wait(timeout=10)
            try:
                wallets.transfer(wallet, payee_wallet, 60)
                outcome = 'sent'
            except wallets.WalletError as exc:
                outcome = exc.code
            except Exception as exc:                       # database contention
                outcome = type(exc).__name__
            finally:
                connections.close_all()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=spend) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return results

    def test_only_one_of_two_simultaneous_debits_can_succeed(self):
        user, wallet = make_user('spec_race', coins=100)
        payee, _ = make_user('spec_race_payee')
        payee_wallet = UserWallet.objects.get(user=payee)

        decisive = 0
        for attempt in range(self.ROUNDS):
            Transaction.objects.all().delete()
            UserWallet.objects.filter(pk=wallet.pk).update(wallet_balance=100)
            UserWallet.objects.filter(pk=payee_wallet.pk).update(wallet_balance=0)

            results = self._one_race(wallet, payee_wallet)
            wallet.refresh_from_db()
            payee_wallet.refresh_from_db()
            sent = results.count('sent')
            where = 'round %d, outcomes %r' % (attempt + 1, results)

            # The invariant, and it holds however the database behaved.
            self.assertEqual(len(results), 2, where)
            self.assertLessEqual(sent, 1,
                                 'both debits succeeded out of one affordable '
                                 'balance: %s' % where)
            self.assertGreaterEqual(wallet.wallet_balance, 0,
                                    'a balance went negative: %s' % where)
            self.assertEqual(wallet.wallet_balance, 100 - (60 * sent), where)
            self.assertEqual(payee_wallet.wallet_balance, 60 * sent, where)
            # The money that left is the money that arrived. Nothing created,
            # nothing lost, and no half-written pair.
            self.assertEqual(Transaction.objects.filter(
                wallet=wallet, amount__lt=0).count(), sent, where)
            self.assertEqual(Transaction.objects.filter(
                wallet=payee_wallet, amount__gt=0).count(), sent, where)

            if sent == 1:
                decisive += 1

        self.assertGreater(
            decisive, 0,
            'every round ended with both threads refused, so nothing was '
            'actually raced. The test proved nothing and must not report green.')


class RowLockTests(TestCase):
    """The balance is READ under a row lock, asserted structurally.

    Measured, not assumed: a transfer that wraps its read and write in
    `transaction.atomic()` but does NOT lock the row is genuinely unsafe on
    MySQL, and still passes the threaded test above on SQLite, because SQLite
    serialises every writer with a database-wide lock and hides the fault. The
    crudest version, with no atomic block either, was measured at a balance of
    MINUS 20 after two debits of 60 against 100.

    So the lock is asserted directly. This fails on any backend the moment
    somebody takes `select_for_update` out, which is the change that would do
    the damage and the one the threaded test cannot see here.
    """

    def setUp(self):
        self.payer, self.payer_wallet = make_user('lock_payer', coins=100,
                                                  kyc=True)
        self.payee, self.payee_wallet = make_user('lock_payee')

    def _locks_taken_by(self, action):
        from django.db.models.query import QuerySet
        seen = []
        original = QuerySet.select_for_update

        def spy(qs, *args, **kwargs):
            seen.append(qs.model.__name__)
            return original(qs, *args, **kwargs)

        QuerySet.select_for_update = spy
        try:
            action()
        finally:
            QuerySet.select_for_update = original
        return seen

    def test_a_transfer_locks_both_wallets(self):
        seen = self._locks_taken_by(
            lambda: wallets.transfer(self.payer_wallet, self.payee_wallet, 10))
        self.assertEqual(sorted(seen), ['UserWallet', 'UserWallet'],
                         'a transfer read a balance without locking the row')

    def test_a_payout_hold_locks_the_wallet(self):
        seen = self._locks_taken_by(
            lambda: wallets.hold_for_payout(self.payer_wallet, 10, 'test'))
        self.assertEqual(seen, ['UserWallet'],
                         'a payout held money without locking the row')

    def test_settling_a_payout_locks_the_wallet(self):
        hold = wallets.hold_for_payout(self.payer_wallet, 10, 'test')
        row = WithdrawalRequest.objects.create(
            wallet=self.payer_wallet, amount=10, bank_name='GTBank',
            account_number='0123456789', account_name='Lock Payer', hold=hold)
        seen = self._locks_taken_by(lambda: wallets.settle_payout(row))
        self.assertEqual(seen, ['UserWallet'])

    def test_returning_a_payout_locks_the_wallet(self):
        hold = wallets.hold_for_payout(self.payer_wallet, 10, 'test')
        row = WithdrawalRequest.objects.create(
            wallet=self.payer_wallet, amount=10, bank_name='GTBank',
            account_number='0123456789', account_name='Lock Payer', hold=hold)
        seen = self._locks_taken_by(lambda: wallets.return_payout(row, 'no'))
        self.assertEqual(seen, ['UserWallet'])


# ---------------------------------------------------------------------------
# "Complete KYC verification through a third-party service"
# ---------------------------------------------------------------------------

class KycSeamTests(TestCase):
    """The seam is real; the provider is an open decision.

    Writing a fake provider client that returns `{"verified": true}` would make
    the screens look finished and would be a lie told to the one part of the
    platform where a lie is a regulatory problem rather than a bug.
    """

    def setUp(self):
        self.user, self.wallet = make_user('spec_kyc')
        self.reviewer, _ = make_user('spec_reviewer')

    def make_doc(self):
        return KYCDocument.objects.create(
            user=self.user, document_type='national_id',
            document_image='kyc/spec.png')

    def test_a_submission_records_which_provider_has_it(self):
        from . import kyc
        doc = kyc.submit(self.make_doc())
        self.assertEqual(doc.provider, 'in_house')
        self.assertIn('queued_at', doc.provider_response)

    def test_verifying_records_who_decided_and_when(self):
        from . import kyc
        doc = kyc.verify(self.make_doc(), reviewer=self.reviewer)
        self.assertEqual(doc.status, 'approved')
        self.assertEqual(doc.reviewed_by, self.reviewer)
        self.assertIsNotNone(doc.reviewed_at)

    def test_verifying_is_the_only_thing_that_sets_the_wallet_flag(self):
        from . import kyc
        self.assertFalse(self.wallet.kyc_verified)
        kyc.verify(self.make_doc(), reviewer=self.reviewer)
        self.wallet.refresh_from_db()
        self.assertTrue(self.wallet.kyc_verified)

    def test_a_refusal_records_who_refused_and_why(self):
        from . import kyc
        doc = kyc.refuse(self.make_doc(), reviewer=self.reviewer,
                         reason='The photograph is unreadable')
        self.assertEqual(doc.status, 'rejected')
        self.assertEqual(doc.reviewed_by, self.reviewer)
        self.assertIn('unreadable', doc.rejection_reason)
        self.wallet.refresh_from_db()
        self.assertFalse(self.wallet.kyc_verified)

    def test_an_unknown_provider_name_falls_back_to_a_person(self):
        """A misspelled setting must not make identity checking unavailable."""
        from . import kyc
        self.assertIs(kyc.provider_for('smile_id_typo'),
                      kyc.PROVIDERS['in_house'])

    def test_the_status_endpoint_says_who_checked_it(self):
        from . import kyc
        client = APIClient()
        kyc.verify(kyc.submit(self.make_doc()), reviewer=self.reviewer)
        res = client.get('/auth/wallet/kyc/status/', **auth(self.user))
        self.assertEqual(res.status_code, 200, res.data)
        checked = res.data['data']['latest_submission']['checked_by']
        self.assertEqual(checked['provider'], 'in_house')
        self.assertEqual(checked['reviewed_by'], self.reviewer.username)


# ---------------------------------------------------------------------------
# Who may act on somebody else's wallet
# ---------------------------------------------------------------------------

class SharedWalletPermissionTests(TestCase):
    """Read is wide, spend is narrow, and a stranger gets neither.

    A team wallet is reachable by everybody in the team, so if belonging were
    enough to spend it then any member could empty it. The split is the whole
    permission model for shared wallets, and it is worth a test for each rung
    of the ladder rather than one test of the happy case: the rung that is
    wrong is never the owner's.

    The organisation manager rung is here because it moved. A manager spent an
    organisation's money on the strength of the TEAMS scope until 8 September,
    which handed the wallet to somebody given the roster.
    """

    def setUp(self):
        self.owner, _ = make_user('perm_owner', coins=0)
        self.captain, _ = make_user('perm_captain')
        self.member, _ = make_user('perm_member')
        self.stranger, _ = make_user('perm_stranger')
        self.payee, _ = make_user('perm_payee')

        game = Games.objects.create(game_title='EA FC Perm')
        self.team = Teams.objects.create(
            team_name='Permission United', game=game, description='x',
            team_creator=self.owner, team_owner=self.owner,
            penalty_points=0, number_of_members=3)
        TeamMembers.objects.create(team=self.team, user=self.captain,
                                   role='captain')
        TeamMembers.objects.create(team=self.team, user=self.member,
                                   role='member')

        self.org = Organization.objects.create(
            org_name='Permission Org', org_creator=self.owner,
            org_owner=self.owner)
        self.admin, _ = make_user('perm_org_admin')
        self.mgr_teams, _ = make_user('perm_mgr_teams')
        self.mgr_finance, _ = make_user('perm_mgr_finance')
        self.plain, _ = make_user('perm_org_member')
        OrgMember.objects.create(org=self.org, user=self.admin,
                                 role=OrgMember.ROLE_ADMIN)
        OrgMember.objects.create(org=self.org, user=self.mgr_teams,
                                 role=OrgMember.ROLE_MANAGER,
                                 scopes=[OrgMember.SCOPE_TEAMS])
        OrgMember.objects.create(org=self.org, user=self.mgr_finance,
                                 role=OrgMember.ROLE_MANAGER,
                                 scopes=[OrgMember.SCOPE_FINANCE])
        OrgMember.objects.create(org=self.org, user=self.plain, role='member')

        self.team_wallet = wallets.wallet_for_team(self.team)
        self.team_wallet.wallet_balance = 500
        self.team_wallet.pin_hash = make_password(PIN)
        self.team_wallet.save()
        self.org_wallet = wallets.wallet_for_org(self.org)
        self.org_wallet.wallet_balance = 500
        self.org_wallet.pin_hash = make_password(PIN)
        self.org_wallet.save()

        self.client = APIClient()

    def team_url(self):
        return '/auth/team/%s/wallet/' % (self.team.slug or self.team.pk)

    def org_url(self):
        return '/auth/organization/%s/wallet/' % (self.org.slug or self.org.pk)

    def send(self, url, user, amount=10):
        return self.client.post(url, {
            'action': 'send', 'to_kind': 'user', 'to': self.payee.username,
            'amount': amount, 'pin': PIN,
        }, format='json', **auth(user))

    # -- reading ----------------------------------------------------------

    def test_a_signed_out_caller_is_refused_both_wallets(self):
        """No token at all. The API is what actually stops anybody."""
        for url in (self.team_url(), self.org_url()):
            self.assertIn(self.client.get(url).status_code, (401, 403), url)
            self.assertIn(
                self.client.post(url, {'action': 'send'},
                                 format='json').status_code,
                (401, 403), url)

    def test_a_stranger_cannot_even_read(self):
        for url in (self.team_url(), self.org_url()):
            res = self.client.get(url, **auth(self.stranger))
            self.assertEqual(res.status_code, 403, url)

    def test_every_member_can_read_the_statement(self):
        """A team whose members cannot see where the money went is worse than
        no wallet."""
        for user, url in ((self.member, self.team_url()),
                          (self.plain, self.org_url())):
            res = self.client.get(url, **auth(user))
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(res.data['data']['balance'], 500)
            self.assertFalse(res.data['data']['can_spend'])

    # -- spending, team ---------------------------------------------------

    def test_the_team_owner_can_spend(self):
        self.assertEqual(self.send(self.team_url(), self.owner).status_code, 200)

    def test_the_captain_can_spend(self):
        self.assertEqual(self.send(self.team_url(), self.captain).status_code,
                         200)

    def test_an_ordinary_member_cannot_spend(self):
        before = self.team_wallet.wallet_balance
        res = self.send(self.team_url(), self.member)
        self.assertEqual(res.status_code, 403, res.data)
        self.team_wallet.refresh_from_db()
        self.assertEqual(self.team_wallet.wallet_balance, before)

    # -- spending, organisation -------------------------------------------

    def test_the_org_owner_and_an_admin_can_spend(self):
        self.assertEqual(self.send(self.org_url(), self.owner).status_code, 200)
        self.assertEqual(self.send(self.org_url(), self.admin).status_code, 200)

    def test_a_manager_with_the_finance_scope_can_spend(self):
        res = self.send(self.org_url(), self.mgr_finance)
        self.assertEqual(res.status_code, 200, res.data)

    def test_a_manager_without_the_finance_scope_cannot(self):
        """Somebody handed the roster to run was handed no money."""
        before = self.org_wallet.wallet_balance
        res = self.send(self.org_url(), self.mgr_teams)
        self.assertEqual(res.status_code, 403, res.data)
        self.org_wallet.refresh_from_db()
        self.assertEqual(self.org_wallet.wallet_balance, before)

    def test_an_ordinary_org_member_cannot_spend(self):
        res = self.send(self.org_url(), self.plain)
        self.assertEqual(res.status_code, 403, res.data)

    # -- the PIN is a spend control too -----------------------------------

    def test_a_member_cannot_set_the_pin_either(self):
        """Setting the PIN is spending: whoever holds it holds the wallet."""
        res = self.client.post(self.team_url(),
                               {'action': 'set_pin', 'pin': '9999'},
                               format='json', **auth(self.member))
        self.assertEqual(res.status_code, 403, res.data)
        self.team_wallet.refresh_from_db()
        self.assertTrue(check_password(PIN, self.team_wallet.pin_hash))


# ---------------------------------------------------------------------------
# The PIN is required, on EVERY surface that spends
# ---------------------------------------------------------------------------

class PinIsRequiredEverywhereTests(TestCase):
    """A send that omits the PIN field entirely must move nothing.

    This is the second time the PIN has gone missing between the two wallet
    surfaces, so it is a test rather than a note. The first time the PAGES did
    not send it and nothing could succeed, which is loud. This time the SERVER
    did not require it and everything succeeded, which is silent and is the
    worse direction.

    The cause is worth stating because it will recur: `wallets.transfer` takes
    `pin=None` to mean "no PIN check", which is correct for a prize payout or
    an event settlement, and catastrophic for an endpoint that simply forgot to
    pass one. `.get('pin')` on a payload with no `pin` key returns exactly that
    None. Nothing distinguishes "the platform is moving its own money" from
    "somebody left a field out" except the caller remembering, so each
    user-facing endpoint is held to it here.

    Every case below is the real payload with one field removed, not an
    invented one.
    """

    def setUp(self):
        self.owner, self.owner_wallet = make_user('pin_req_owner', coins=100)
        self.payee, _ = make_user('pin_req_payee')

        game = Games.objects.create(game_title='EA FC Pin')
        self.team = Teams.objects.create(
            team_name='Pin Required United', game=game, description='x',
            team_creator=self.owner, team_owner=self.owner,
            penalty_points=0, number_of_members=1)
        self.org = Organization.objects.create(
            org_name='Pin Required Org', org_creator=self.owner,
            org_owner=self.owner)

        self.wallets = {}
        for key, w in (('team', wallets.wallet_for_team(self.team)),
                       ('org', wallets.wallet_for_org(self.org))):
            w.wallet_balance = 500
            w.pin_hash = make_password(PIN)
            w.save()
            self.wallets[key] = w

        self.client = APIClient()

    def urls(self):
        return {
            'team': '/auth/team/%s/wallet/' % (self.team.slug or self.team.pk),
            'org': '/auth/organization/%s/wallet/' % (self.org.slug
                                                      or self.org.pk),
        }

    def payload(self, **over):
        body = {'action': 'send', 'to_kind': 'user',
                'to': self.payee.username, 'amount': 10}
        body.update(over)
        return body

    def assert_moved_nothing(self, key, res):
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data.get('code'), 'PIN_REQUIRED', res.data)
        wallet = self.wallets[key]
        wallet.refresh_from_db()
        self.assertEqual(wallet.wallet_balance, 500, key)
        self.assertEqual(UserWallet.objects.get(user=self.payee)
                         .wallet_balance, 0, key)

    def test_no_pin_key_at_all_moves_nothing(self):
        """The exact request that emptied an organisation wallet on 8 Sept."""
        for key, url in self.urls().items():
            res = self.client.post(url, self.payload(), format='json',
                                   **auth(self.owner))
            self.assert_moved_nothing(key, res)

    def test_an_empty_pin_moves_nothing(self):
        """A form submitted with the field untouched sends '', not nothing."""
        for key, url in self.urls().items():
            res = self.client.post(url, self.payload(pin=''), format='json',
                                   **auth(self.owner))
            self.assert_moved_nothing(key, res)

    def test_a_null_pin_moves_nothing(self):
        """JSON null, which is what an unset React state serialises to."""
        for key, url in self.urls().items():
            res = self.client.post(url, self.payload(pin=None), format='json',
                                   **auth(self.owner))
            self.assert_moved_nothing(key, res)

    def test_the_right_pin_still_works(self):
        """The guard must refuse an absent PIN without refusing a real one.

        A check that fails closed on everything is not a check, it is an
        outage, and it would pass every test above.
        """
        for key, url in self.urls().items():
            res = self.client.post(url, self.payload(pin=PIN), format='json',
                                   **auth(self.owner))
            self.assertEqual(res.status_code, 200, res.data)
            self.wallets[key].refresh_from_db()
            self.assertEqual(self.wallets[key].wallet_balance, 490, key)

    def test_the_persons_own_wallet_refuses_it_too(self):
        """The surface that always had the guard, so the two stay in step."""
        before = self.owner_wallet.wallet_balance
        res = self.client.post('/auth/wallet/send/', {
            'to_kind': 'user', 'to': self.payee.username, 'amount': 10,
        }, format='json', **auth(self.owner))
        self.assertEqual(res.status_code, 400, res.data)
        self.owner_wallet.refresh_from_db()
        self.assertEqual(self.owner_wallet.wallet_balance, before)


class WrongPinDoesNotBurnTheCodeTests(TestCase):
    """A mistyped PIN must not consume the authenticator code.

    `spend_code` marks a code used so it cannot be replayed, which is right.
    But the second factor was being checked BEFORE the PIN, so getting the PIN
    wrong spent the code as a side effect: the retry was refused with "that
    code has been used" and the person had to wait out the 30 second window,
    having done nothing wrong except mistype four digits.

    It also reported the wrong reason, which is its own fault. Somebody told
    their code is used looks at their authenticator, not at the PIN field.
    """

    def setUp(self):
        self.owner, _ = make_user('burn_owner', coins=0)
        self.payee, _ = make_user('burn_payee')
        game = Games.objects.create(game_title='EA FC Burn')
        self.team = Teams.objects.create(
            team_name='Burn United', game=game, description='x',
            team_creator=self.owner, team_owner=self.owner,
            penalty_points=0, number_of_members=1)
        self.wallet = wallets.wallet_for_team(self.team)
        self.wallet.wallet_balance = 100
        self.wallet.pin_hash = make_password(PIN)
        self.wallet.save()

        self.factor = enrol(self.owner)
        self.client = APIClient()

    def url(self):
        return '/auth/team/%s/wallet/' % (self.team.slug or self.team.pk)

    def send(self, pin, code):
        return self.client.post(self.url(), {
            'action': 'send', 'to_kind': 'user', 'to': self.payee.username,
            'amount': 10, 'pin': pin, 'code': code,
        }, format='json', **auth(self.owner))

    def test_a_wrong_pin_leaves_the_code_usable(self):
        code = code_for(self.factor)

        wrong = self.send('9999', code)
        self.assertEqual(wrong.status_code, 400, wrong.data)
        self.assertEqual(wrong.data.get('code'), 'INVALID_PIN', wrong.data)

        # The same code, immediately afterwards, still works.
        right = self.send(PIN, code)
        self.assertEqual(right.status_code, 200, right.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 90)

    def test_a_used_code_is_still_refused_the_second_time(self):
        """The replay guard must survive the reordering."""
        code = code_for(self.factor)
        self.assertEqual(self.send(PIN, code).status_code, 200)
        again = self.send(PIN, code)
        self.assertEqual(again.status_code, 400, again.data)
        self.assertEqual(again.data.get('code'), 'INVALID_CODE', again.data)
