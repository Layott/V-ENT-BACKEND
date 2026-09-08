"""A team's wallet and an organisation's wallet.

CEO, 7 September 2026: "Teams should have their own wallets and organizations
should also have their own wallets."

Both models existed for months as a balance and a plain integer PIN, with no
endpoint, no screen and - the part that matters - no HISTORY, because
`Transaction` linked only to `UserWallet`. A balance with no transactions is a
number somebody has to take on trust.

The decisions worth pinning:

- **A balance is never edited without a transaction beside it.** Both are
  written in one atomic block, so a statement that does not add up is not a
  state this can produce.
- **Read is wide, spend is narrow.** Every member sees where the money went; a
  team wallet any member could empty is not a wallet.
- **A refusal happens before anything moves.** The vendor checkout once
  refused with "Nothing has been taken from your wallet" after taking it.
- **A wallet with no PIN cannot spend at all**, because a team wallet is
  reachable by everybody in the team.
"""
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction as db_transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import wallets
from .models import (Games, OrgMember, Organization, TeamMembers, Teams,
                     Transaction, UserWallet, Users)


def a_user(name, coins=0):
    user = Users.objects.create(username=name, email='%s@vent.test' % name,
                                is_active=True,
                                login_session_token=('w-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    UserWallet.objects.create(user_wallet_id=name[:10], user=user,
                              wallet_balance=coins,
                              pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class WalletBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner, self.owner_auth = a_user('w_owner', coins=100)
        self.member, self.member_auth = a_user('w_member', coins=10)
        self.stranger, self.stranger_auth = a_user('w_stranger')
        game = Games.objects.create(game_title='EA FC W')
        self.team = Teams.objects.create(
            team_name='Wallet United', game=game, description='x',
            team_creator=self.owner, team_owner=self.owner,
            penalty_points=0, number_of_members=2)
        TeamMembers.objects.create(team=self.team, user=self.member)
        self.org = Organization.objects.create(
            org_name='Wallet Org', org_creator=self.owner, org_owner=self.owner)
        OrgMember.objects.create(org=self.org, user=self.member, role='member')

        self.team_wallet = wallets.wallet_for_team(self.team)
        self.org_wallet = wallets.wallet_for_org(self.org)


class TransferTests(WalletBase):
    def test_money_moves_and_both_lines_are_written(self):
        self.team_wallet.wallet_balance = 50
        self.team_wallet.save()
        wallets.transfer(self.team_wallet,
                         UserWallet.objects.get(user=self.member), 20)

        self.team_wallet.refresh_from_db()
        self.assertEqual(self.team_wallet.wallet_balance, 30)
        self.assertEqual(
            UserWallet.objects.get(user=self.member).wallet_balance, 30)
        self.assertEqual(
            Transaction.objects.filter(team_wallet=self.team_wallet).count(), 1)
        self.assertEqual(
            Transaction.objects.filter(wallet__user=self.member,
                                       type='transfer').count(), 1)

    def test_the_statement_adds_up_to_the_balance(self):
        self.team_wallet.wallet_balance = 100
        self.team_wallet.save()
        for _ in range(3):
            wallets.transfer(self.team_wallet,
                             UserWallet.objects.get(user=self.member), 10)
        self.team_wallet.refresh_from_db()
        moved = sum(row['amount'] for row in wallets.statement(self.team_wallet))
        self.assertEqual(self.team_wallet.wallet_balance, 100 + moved)

    def test_nothing_moves_when_there_is_not_enough(self):
        self.team_wallet.wallet_balance = 5
        self.team_wallet.save()
        with self.assertRaises(wallets.WalletError) as caught:
            wallets.transfer(self.team_wallet,
                             UserWallet.objects.get(user=self.member), 50)
        self.assertEqual(caught.exception.code, 'INSUFFICIENT_BALANCE')
        self.team_wallet.refresh_from_db()
        self.assertEqual(self.team_wallet.wallet_balance, 5)
        self.assertEqual(Transaction.objects.filter(
            team_wallet=self.team_wallet).count(), 0)

    def test_a_wallet_cannot_send_to_itself(self):
        with self.assertRaises(wallets.WalletError) as caught:
            wallets.transfer(self.team_wallet, self.team_wallet, 1)
        self.assertEqual(caught.exception.code, 'SAME_WALLET')

    def test_nothing_or_less_is_refused(self):
        for bad in (0, -5, 'abc', None):
            with self.assertRaises(wallets.WalletError):
                wallets.transfer(self.team_wallet,
                                 UserWallet.objects.get(user=self.member), bad)

    def test_every_direction_works_because_there_is_only_one_function(self):
        """Nine directions written out is nine chances to forget a line."""
        user_wallet = UserWallet.objects.get(user=self.owner)
        self.team_wallet.wallet_balance = 100
        self.team_wallet.save()
        self.org_wallet.wallet_balance = 100
        self.org_wallet.save()

        for src, dst in ((user_wallet, self.team_wallet),
                         (self.team_wallet, self.org_wallet),
                         (self.org_wallet, user_wallet)):
            # Read FRESH. An earlier direction in this loop may have moved
            # the very wallet being measured, and a stale in-memory balance
            # is not what the database holds.
            dst.refresh_from_db()
            before = dst.wallet_balance
            wallets.transfer(src, dst, 5)
            dst.refresh_from_db()
            self.assertEqual(dst.wallet_balance, before + 5)


class PinTests(WalletBase):
    def test_a_wallet_with_no_pin_cannot_spend(self):
        """A team wallet is reachable by everybody in the team."""
        self.team_wallet.wallet_balance = 50
        self.team_wallet.save()
        with self.assertRaises(wallets.WalletError) as caught:
            wallets.transfer(self.team_wallet,
                             UserWallet.objects.get(user=self.member), 5,
                             pin='1234')
        self.assertEqual(caught.exception.code, 'PIN_REQUIRED')

    def test_the_wrong_pin_moves_nothing(self):
        self.team_wallet.wallet_balance = 50
        self.team_wallet.pin_hash = make_password('4321')
        self.team_wallet.save()
        with self.assertRaises(wallets.WalletError) as caught:
            wallets.transfer(self.team_wallet,
                             UserWallet.objects.get(user=self.member), 5,
                             pin='0000')
        self.assertEqual(caught.exception.code, 'INVALID_PIN')
        self.team_wallet.refresh_from_db()
        self.assertEqual(self.team_wallet.wallet_balance, 50)


class OneOwnerTests(WalletBase):
    def test_a_transaction_cannot_belong_to_two_wallets(self):
        with self.assertRaises(IntegrityError):
            with db_transaction.atomic():
                Transaction.objects.create(
                    wallet=UserWallet.objects.get(user=self.owner),
                    team_wallet=self.team_wallet,
                    type='transfer', amount=1, status='completed')

    def test_a_transaction_cannot_belong_to_nobody(self):
        with self.assertRaises(IntegrityError):
            with db_transaction.atomic():
                Transaction.objects.create(type='transfer', amount=1,
                                           status='completed')


class WhoMaySpendTests(WalletBase):
    def get(self, url, auth):
        return self.client.get(url, **auth)

    def test_the_team_owner_may_read_and_spend(self):
        res = self.get('/auth/team/%s/wallet/' % self.team.slug, self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertTrue(res.json()['data']['can_spend'])

    def test_a_member_may_read_but_not_spend(self):
        """A team whose members cannot see where the money went is worse than
        no wallet; a wallet any member can empty is not a wallet."""
        res = self.get('/auth/team/%s/wallet/' % self.team.slug, self.member_auth)
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertFalse(res.json()['data']['can_spend'])

    def test_a_stranger_sees_nothing(self):
        res = self.get('/auth/team/%s/wallet/' % self.team.slug,
                       self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_sees_nothing(self):
        res = self.client.get('/auth/team/%s/wallet/' % self.team.slug)
        self.assertEqual(res.status_code, 401)

    def test_a_member_who_may_not_spend_is_refused_when_they_try(self):
        """The interface hides the control; the API is what stops anybody."""
        res = self.client.post(
            '/auth/team/%s/wallet/' % self.team.slug,
            {'action': 'send', 'to_kind': 'user', 'to': self.stranger.username,
             'amount': 1}, format='json', **self.member_auth)
        self.assertEqual(res.status_code, 403)

    def test_an_org_manager_needs_the_teams_scope_to_spend(self):
        row = OrgMember.objects.get(org=self.org, user=self.member)
        row.role = OrgMember.ROLE_MANAGER
        row.scopes = []
        row.save()
        res = self.get('/auth/organization/%s/wallet/' % self.org.slug,
                       self.member_auth)
        self.assertFalse(res.json()['data']['can_spend'])

        row.scopes = [OrgMember.SCOPE_TEAMS]
        row.save()
        res = self.get('/auth/organization/%s/wallet/' % self.org.slug,
                       self.member_auth)
        self.assertTrue(res.json()['data']['can_spend'])


class SendingThroughTheEndpointTests(WalletBase):
    def setUp(self):
        super().setUp()
        self.team_wallet.wallet_balance = 60
        self.team_wallet.pin_hash = make_password('1234')
        self.team_wallet.save()

    def send(self, **body):
        return self.client.post('/auth/team/%s/wallet/' % self.team.slug,
                                dict(action='send', pin='1234', **body),
                                format='json', **self.owner_auth)

    def test_a_team_pays_a_member(self):
        res = self.send(to_kind='user', to=self.member.username, amount=25)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['balance'], 35)
        self.assertEqual(
            UserWallet.objects.get(user=self.member).wallet_balance, 35)

    def test_a_team_pays_its_organisation(self):
        res = self.send(to_kind='org', to=self.org.slug, amount=10)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.org_wallet.refresh_from_db()
        self.assertEqual(self.org_wallet.wallet_balance, 10)

    def test_who_it_goes_to_is_NAMED_not_guessed(self):
        """"vermillion" could be a username, a team or an organisation, and
        guessing wrong sends somebody's money to a stranger."""
        res = self.send(to=self.member.username, amount=5)
        self.assertEqual(res.status_code, 400)

    def test_a_recipient_that_does_not_exist_is_refused(self):
        res = self.send(to_kind='user', to='nobody_at_all', amount=5)
        self.assertEqual(res.status_code, 404)

    def test_the_statement_comes_back_with_the_balance(self):
        self.send(to_kind='user', to=self.member.username, amount=5)
        res = self.client.get('/auth/team/%s/wallet/' % self.team.slug,
                              **self.owner_auth)
        rows = res.json()['data']['transactions']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['amount'], -5)

    def test_a_pin_can_be_set_and_is_hashed(self):
        res = self.client.post('/auth/team/%s/wallet/' % self.team.slug,
                               {'action': 'set_pin', 'pin': '9876'},
                               format='json', **self.owner_auth)
        self.assertEqual(res.status_code, 200)
        self.team_wallet.refresh_from_db()
        self.assertNotIn('9876', self.team_wallet.pin_hash)
        self.assertTrue(res.json()['data']['has_pin'])

    def test_a_pin_that_is_not_four_to_six_digits_is_refused(self):
        for bad in ('12', '1234567', 'abcd', ''):
            res = self.client.post('/auth/team/%s/wallet/' % self.team.slug,
                                   {'action': 'set_pin', 'pin': bad},
                                   format='json', **self.owner_auth)
            self.assertEqual(res.status_code, 400, bad)
