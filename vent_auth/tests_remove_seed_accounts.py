"""What `remove_seed_accounts` must refuse, and what it must write down first.

The command deletes accounts, and deleting an account here is not a small
delete: a wallet and its whole ledger go with it, a team the account owns goes
with it, and a ticket at a real event goes with it. So the interesting tests
are the refusals, not the happy path.

Each test below exists because the old version of the command would have failed
it:

  * it chose its own list from a set of email domains, so it could delete an
    account nobody named
  * it deleted in a bare loop with no transaction, so a PROTECT refusal landed
    halfway through with some accounts gone and no record of which
  * it wrote nothing down, so a mistake could not be put back
  * it had no idea the door accounts existed
"""
import json
import os
import tempfile
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from io import StringIO

from .models import Games, Teams, Transaction, Users, UserWallet


def _user(username, email=None, **kw):
    return Users.objects.create(
        username=username, email=email or ('%s@vent.test' % username),
        is_active=True, **kw)


def _run(*args, **kw):
    out = StringIO()
    call_command('remove_seed_accounts', *args, stdout=out, stderr=out, **kw)
    return out.getvalue()


class ItRefusesToChooseForYouTests(TestCase):
    """The CEO asked to decide. A command that picks its own targets cannot
    carry somebody else's decision, it can only carry its author's opinion."""

    def setUp(self):
        self.spare = _user('seed_spare', 'spare@seed.v-ent.co')

    def test_no_list_is_refused(self):
        with self.assertRaises(CommandError) as caught:
            _run()
        self.assertIn('No accounts named', str(caught.exception))
        self.assertTrue(Users.objects.filter(username='seed_spare').exists())

    def test_a_name_that_is_not_an_account_stops_everything(self):
        """A typo must not quietly delete the rest of the list. Somebody who
        mistypes one name of twenty has got the list wrong, and the right
        answer is to hand it back, not to delete nineteen."""
        real = _user('seed_real', 'real@seed.v-ent.co')
        with self.assertRaises(CommandError) as caught:
            _run('--user', 'seed_real', '--user', 'seed_typoo',
                 '--delete', '--yes-i-mean-it')
        self.assertIn('seed_typoo', str(caught.exception))
        self.assertTrue(Users.objects.filter(pk=real.pk).exists())

    def test_suggest_deletes_nothing_and_chooses_nobody(self):
        out = _run('--suggest')
        self.assertIn('not a list', out)
        self.assertTrue(Users.objects.filter(username='seed_spare').exists())


class TheDoorAccountsTests(TestCase):
    """rivalryops1 and rivalryops2 are what the ticket scanner signs in as.
    Removing one means a queue outside a real event and no way in."""

    def test_a_door_account_is_refused_by_name(self):
        _user('rivalryops1', 'ops1@v-ent.co')
        with self.assertRaises(CommandError) as caught:
            _run('--user', 'rivalryops1', '--delete', '--yes-i-mean-it')
        self.assertIn('live door', str(caught.exception))
        self.assertTrue(Users.objects.filter(username='rivalryops1').exists())

    def test_the_guard_can_be_overridden_deliberately(self):
        """Refusing for ever would be a rule nobody can follow the day the
        account really is retired. It takes a flag whose name says what it
        does."""
        _user('rivalryops2', 'ops2@v-ent.co')
        with tempfile.TemporaryDirectory() as tmp:
            _run('--user', 'rivalryops2', '--delete', '--yes-i-mean-it',
                 '--override-door-guard', '--export-dir', tmp)
        self.assertFalse(Users.objects.filter(username='rivalryops2').exists())


class ItSaysWhatWillHappenFirstTests(TestCase):
    def setUp(self):
        self.user = _user('seed_rich', 'rich@seed.v-ent.co')
        wallet = UserWallet.objects.create(
            user_wallet_id='w_rich', user=self.user, wallet_balance=4200)
        Transaction.objects.create(wallet=wallet, amount=100)

    def test_the_plan_names_the_money_and_deletes_nothing(self):
        out = _run('--user', 'seed_rich')
        self.assertIn('4200 VC', out)
        self.assertIn('1 ledger line', out)
        self.assertIn('Nothing was deleted', out)
        self.assertTrue(Users.objects.filter(pk=self.user.pk).exists())

    def test_delete_alone_is_not_enough(self):
        out = _run('--user', 'seed_rich', '--delete')
        self.assertIn('needs --yes-i-mean-it', out)
        self.assertTrue(Users.objects.filter(pk=self.user.pk).exists())

    def test_the_plan_warns_that_tournaments_are_orphaned_not_removed(self):
        """Tournament.tournament_creator is SET_NULL, so a tournament survives
        its organiser being deleted, with nobody on it. That is worse than a
        seeded organiser, and the plan has to say so before anybody agrees."""
        out = _run('--user', 'seed_rich')
        self.assertIn('SET_NULL', out)


class ItWritesTheRowsDownBeforeDestroyingThemTests(TestCase):
    def setUp(self):
        self.user = _user('seed_gone', 'gone@seed.v-ent.co')
        UserWallet.objects.create(
            user_wallet_id='w_gone', user=self.user, wallet_balance=7)

    def test_the_export_holds_every_row_and_the_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'run')
            out = _run('--user', 'seed_gone', '--delete', '--yes-i-mean-it',
                       '--export-dir', target)
            self.assertIn('Export written', out)

            with open(os.path.join(target, 'rows.json'), encoding='utf-8') as fh:
                rows = json.load(fh)
            models = {r['model'] for r in rows}
            self.assertIn('vent_auth.users', models)
            self.assertIn('vent_auth.userwallet', models)

            with open(os.path.join(target, 'manifest.json'), encoding='utf-8') as fh:
                manifest = json.load(fh)
            self.assertEqual(manifest['accounts'][0]['username'], 'seed_gone')
            self.assertEqual(manifest['accounts'][0]['wallet_balance_vc'], 7)

        self.assertFalse(Users.objects.filter(username='seed_gone').exists())

    def test_an_export_that_cannot_be_written_stops_the_delete(self):
        """A removal with no way back is not one this command performs. The
        export is written first precisely so this ordering is testable."""
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            a_file_not_a_directory = fh.name
        try:
            with self.assertRaises(CommandError) as caught:
                _run('--user', 'seed_gone', '--delete', '--yes-i-mean-it',
                     '--export-dir', os.path.join(a_file_not_a_directory, 'x'))
            self.assertIn('Nothing was deleted', str(caught.exception))
            self.assertTrue(Users.objects.filter(username='seed_gone').exists())
        finally:
            os.unlink(a_file_not_a_directory)


class AProtectedRowStopsTheWholeBatchTests(TestCase):
    """`MatchScore.submitted_by` is PROTECT. The old command deleted in a bare
    loop, so this refusal arrived partway through a batch."""

    def setUp(self):
        from vent_tournament.models import BracketMatch, MatchScore, Tournament

        self.scorer = _user('seed_scorer', 'scorer@seed.v-ent.co')
        self.bystander = _user('seed_bystander', 'bystander@seed.v-ent.co')
        now = timezone.now()
        tournament = Tournament.objects.create(
            tournament_title='Protect Fixture', tournament_creator=self.scorer,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2))
        match = BracketMatch.objects.create(
            tournament=tournament, round_number=1, match_number=1)
        MatchScore.objects.create(
            match=match, submitted_by=self.scorer, score_p1=1, score_p2=0)

    def test_the_plan_names_the_protected_rows(self):
        out = _run('--user', 'seed_scorer')
        self.assertIn('REFUSED', out)
        self.assertIn('protected', out)

    def test_nobody_in_the_batch_is_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError):
                _run('--user', 'seed_bystander', '--user', 'seed_scorer',
                     '--delete', '--yes-i-mean-it', '--export-dir', tmp)
        self.assertTrue(Users.objects.filter(username='seed_scorer').exists())
        self.assertTrue(Users.objects.filter(username='seed_bystander').exists())


class TheTeamGoesWithTheOwnerTests(TestCase):
    """`Teams.team_owner` is CASCADE. Somebody agreeing to remove an account
    is also agreeing to remove every team it owns, and has to be told."""

    def test_the_plan_lists_the_team_that_will_go(self):
        owner = _user('seed_captain', 'captain@seed.v-ent.co')
        game = Games.objects.create(game_title='Fixture FC')
        Teams.objects.create(
            team_name='Doomed Rangers', game=game, description='x',
            team_creator=owner, team_owner=owner,
            penalty_points=0, number_of_members=1)
        out = _run('--user', 'seed_captain')
        self.assertIn('vent_auth.Teams', out)
