"""Filling a tournament's sides and seats from a roster file.

The command exists because of how the CEO asked for it, two hours before a
broadcast: "You can't like code it in? If I give you all the info from here? Not
like hard coded but like just to fill in the slots?"

So the thing under test is: does a roster written the way somebody writes one in
a message end up as the right sides, in the right seat order, with nothing
invented.
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from .management.commands.rivalry_roster import parse_roster
from .models import (
    SquadMember, Tournament, TournamentRegistration, TournamentSquad,
)


class ParseTests(TestCase):
    """A roster is copied out of a message, not typed into a form."""

    def test_the_shape_somebody_actually_writes(self):
        rows = parse_roster(
            'Nigeria NGA\n'
            '  @tobi Tobi Adeyemi\n'
            '  @kunle Kunle Bakare\n'
            '\n'
            'Ghana GHA\n'
            '  @kwame Kwame Mensah\n'
            '  @yaw Yaw Boateng\n')
        self.assertEqual([r['name'] for r in rows], ['Nigeria', 'Ghana'])
        self.assertEqual([r['tag'] for r in rows], ['NGA', 'GHA'])
        self.assertEqual(rows[0]['players'][0],
                         {'handle': 'tobi', 'name': 'Tobi Adeyemi'})

    def test_numbered_lines_are_players_not_sides(self):
        """"1. @tobi" is how a person writes a seat order."""
        rows = parse_roster('Nigeria\n1. @tobi Tobi\n2. @kunle Kunle\n')
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['players']), 2)

    def test_a_name_with_no_handle_is_kept(self):
        rows = parse_roster('Nigeria NGA\n  Tobi Adeyemi\n')
        self.assertEqual(rows[0]['players'][0],
                         {'handle': '', 'name': 'Tobi Adeyemi'})

    def test_a_side_with_no_tag(self):
        rows = parse_roster('Ivory Coast\n  @kone Kone\n')
        self.assertEqual(rows[0]['name'], 'Ivory Coast')
        self.assertEqual(rows[0]['tag'], '')

    def test_json_is_accepted_too(self):
        rows = parse_roster(
            '{"squads": [{"name": "Kenya", "tag": "KEN",'
            ' "players": [{"username": "@otieno", "name": "Otieno"}]}]}')
        self.assertEqual(rows[0]['tag'], 'KEN')
        self.assertEqual(rows[0]['players'][0]['handle'], 'otieno')


class RosterTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.organiser = Users.objects.create(
            username='roster_org', email='org@vent.test', is_active=True)
        self.tournament = Tournament.objects.create(
            tournament_title='Rivalry Series S2', slug='rivalry-s2',
            tournament_creator=self.organiser, tournament_type='online',
            start_date_and_time=now, end_date_and_time=now)
        # The slug is derived from the title by `sync_slug()` in save(), so it
        # is read back rather than assumed. Asking for the slug we passed in is
        # how this test first failed.
        self.tournament.refresh_from_db()
        self.slug = self.tournament.slug
        for handle in ('tobi', 'kunle', 'kwame', 'yaw'):
            Users.objects.create(username=handle, email='%s@vent.test' % handle,
                                 full_name=handle.title(), is_active=True)

    ROSTER = ('Nigeria NGA\n'
              '  @tobi Tobi Adeyemi\n'
              '  @kunle Kunle Bakare\n'
              '\n'
              'Ghana GHA\n'
              '  @kwame Kwame Mensah\n'
              '  @yaw Yaw Boateng\n')

    def run_it(self, roster=None, **flags):
        path = self._file(roster if roster is not None else self.ROSTER)
        out = StringIO()
        call_command('rivalry_roster', tournament=self.slug, roster=path,
                     stdout=out, **flags)
        return out.getvalue()

    def _file(self, text):
        import tempfile
        handle = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False,
                                             encoding='utf-8')
        handle.write(text)
        handle.close()
        return handle.name

    # ---------------------------------------------------------------- basics

    def test_it_makes_the_sides_and_seats_and_enters_them(self):
        self.run_it()
        self.assertEqual(TournamentSquad.objects.count(), 2)
        nigeria = TournamentSquad.objects.get(name='Nigeria')
        self.assertEqual(nigeria.tag, 'NGA')
        self.assertEqual(nigeria.members.count(), 2)
        self.assertEqual(TournamentRegistration.objects.count(), 2)
        self.assertTrue(TournamentRegistration.objects
                        .filter(squad=nigeria, status='confirmed').exists())

    def test_the_first_player_listed_sits_seat_one(self):
        """Seat order is the order in the file, and seat 1 only ever plays
        seat 1. `_seat_players` reads members captain first, so seat 1 is
        recorded as the captain and nothing else in the codebase has to know
        about this file."""
        self.run_it()
        nigeria = TournamentSquad.objects.get(name='Nigeria')
        captains = list(nigeria.members.filter(is_captain=True)
                        .values_list('user__username', flat=True))
        self.assertEqual(captains, ['tobi'])

        from .services.bracket import _seat_players
        registration = TournamentRegistration.objects.get(squad=nigeria)
        seats = _seat_players(registration, 2)
        self.assertEqual([u.username for u in seats], ['tobi', 'kunle'])

    def test_running_it_twice_changes_nothing(self):
        """It will be run again this morning, after a name is corrected."""
        self.run_it()
        self.run_it()
        self.assertEqual(TournamentSquad.objects.count(), 2)
        self.assertEqual(SquadMember.objects.count(), 4)
        self.assertEqual(TournamentRegistration.objects.count(), 2)

    def test_a_corrected_seat_order_is_applied_on_the_second_run(self):
        self.run_it()
        self.run_it('Nigeria NGA\n  @kunle Kunle\n  @tobi Tobi\n')
        nigeria = TournamentSquad.objects.get(name='Nigeria')
        self.assertEqual(
            list(nigeria.members.filter(is_captain=True)
                 .values_list('user__username', flat=True)),
            ['kunle'])

    # ------------------------------------------------------- nothing invented

    def test_a_handle_with_no_account_stops_the_run(self):
        """And changes NOTHING. Half a roster applied is worse than none: the
        graphic then shows three nations and nobody notices the fourth."""
        with self.assertRaises(CommandError):
            self.run_it('Kenya KEN\n  @nobody Nobody At All\n')
        self.assertEqual(TournamentSquad.objects.count(), 0)

    def test_it_names_who_is_missing(self):
        out = StringIO()
        path = self._file('Kenya KEN\n  @nobody Nobody\n')
        with self.assertRaises(CommandError):
            call_command('rivalry_roster', tournament=self.slug,
                         roster=path, stdout=out)
        self.assertIn('@nobody', out.getvalue())

    def test_create_missing_makes_an_account_that_cannot_be_signed_into(self):
        self.run_it('Kenya KEN\n  @otieno Otieno Kamau\n', create_missing=True)
        user = Users.objects.get(username='otieno')
        self.assertEqual(user.full_name, 'Otieno Kamau')
        self.assertFalse(user.has_usable_password())

    def test_a_dry_run_writes_nothing(self):
        out = self.run_it(dry_run=True)
        self.assertIn('Dry run', out)
        self.assertEqual(TournamentSquad.objects.count(), 0)

    def test_an_unknown_tournament_is_refused(self):
        path = self._file(self.ROSTER)
        with self.assertRaises(CommandError):
            call_command('rivalry_roster', tournament='no-such-thing',
                         roster=path, stdout=StringIO())

    # --------------------------------------------------------- the fixtures

    def test_it_can_build_the_round_robin(self):
        """Through the service that already builds one. The command does no
        arithmetic of its own."""
        self.run_it(generate_fixtures=True)
        from .models import BracketMatch, TieFixture
        ties = BracketMatch.objects.filter(tournament=self.tournament)
        self.assertTrue(ties.exists())
        # Two sides, one tie, two seats each.
        self.assertEqual(TieFixture.objects.filter(tie__in=ties).count(),
                         ties.count() * 2)
