"""Continuing a draft must keep every change the organiser made.

CEO, 2 September 2026:

    "first off i set this event to 5 teams and 2 players per team and it still
    shows 0/32 slots ... Then the settings are completely different from what
    was set, things are different, even sponsors i removed all and it still
    shows the same, it seems if you continue a tournament from drafts there is
    this bug."

Correct on every count. Continuing a draft PUTs to `edit_tournament`, and the
wizard speaks a different vocabulary from the columns:

    the wizard sends            the model has
    max_number_of_participants  max_number_of_teams, player_size
    min_number_of_participants  min_number_of_teams
    sponsor_names/types/...     a many-to-many
    prize_data                  TournamentPrizeDistribution rows
    options                     a JSON column
    points_win/draw/loss        keys inside that column

`create_tournament` translates all of it. `edit_tournament` translated none of
it, so **eight** things the organiser changed after the first save were dropped
without a word: the slot count stayed at the default 32, and a removed sponsor
could never be removed.

The general shape is worth naming, because this is the second time it has
happened here: a wizard that POSTs to one endpoint and PUTs to another needs
BOTH to understand the same payload. A test that only exercises create will
pass for ever while the edit path quietly discards half the form.
"""
import json
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_tournament.models import (
    Sponsors, Tournament, TournamentPrizeDistribution)


class DraftEditKeepsEverythingTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='draftOwner', email='do@vent.test',
            login_session_token='draft-owner-tk'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}

        self.game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.draft = Tournament.objects.create(
            tournament_title='Rivalry Series S2',
            tournament_game=self.game,
            tournament_creator=self.owner,
            start_date_and_time=timezone.now() + timezone.timedelta(days=7),
            end_date_and_time=timezone.now() + timezone.timedelta(days=8),
            bracket_type='single_elimination',
            team_size=1,
            player_size=32,
            min_number_of_teams=8,
            max_number_of_teams=32,
            is_draft=True,
        )
        # A sponsor the organiser will remove.
        mtn = Sponsors.objects.create(name='MTN')
        self.draft.sponsors.add(mtn)

    def edit(self, **body):
        return self.client.put(
            '/tournament/edit-tournament/%d/' % self.draft.tournament_id,
            data=body, content_type='application/json', **self.auth)

    # ------------------------------------------------------- the slot count

    def test_the_slot_count_the_wizard_sends_is_applied(self):
        """"i set this event to 5 teams ... and it still shows 0/32 slots"."""
        res = self.edit(max_number_of_participants=5,
                        min_number_of_participants=2)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.max_number_of_teams, 5)
        self.assertEqual(self.draft.min_number_of_teams, 2)

    def test_the_slot_count_also_sets_player_size(self):
        """player_size is the number the public page draws as "0/32", and
        create sets it from the same field. Setting one and not the other is
        how the page went on saying 32 while the cap said 5."""
        self.edit(max_number_of_participants=5)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.player_size, 5)

    def test_players_per_team_is_kept(self):
        self.edit(team_size=2)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.team_size, 2)

    def test_a_slot_count_that_is_not_a_number_names_the_field(self):
        res = self.edit(max_number_of_participants='five')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['field'], 'max_number_of_participants')

    # ---------------------------------------------------------- the sponsors

    def test_removing_every_sponsor_removes_them(self):
        """"even sponsors i removed all and it still shows the same"."""
        res = self.edit(sponsor_names=json.dumps([]),
                        sponsor_types=json.dumps([]),
                        sponsor_usernames=json.dumps([]))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(self.draft.sponsors.count(), 0)

    def test_the_sponsor_list_sent_is_the_whole_answer(self):
        """The wizard sends every row each time, so what it sends replaces
        what is stored rather than being added to it."""
        self.edit(sponsor_names=json.dumps(['PAGA', 'Glo']),
                  sponsor_types=json.dumps(['', '']),
                  sponsor_usernames=json.dumps(['', '']))
        names = sorted(self.draft.sponsors.values_list('name', flat=True))
        self.assertEqual(names, ['Glo', 'PAGA'])

    def test_not_mentioning_sponsors_leaves_them_alone(self):
        """A caller editing only the title must not wipe the sponsors."""
        self.edit(tournament_title='Rivalry Series S2 Renamed')
        self.assertEqual(
            list(self.draft.sponsors.values_list('name', flat=True)), ['MTN'])

    def test_a_sponsor_can_be_linked_to_a_real_account(self):
        other = Users.objects.create(username='sponsorco',
                                     email='sc@vent.test', is_active=True)
        self.edit(sponsor_names=json.dumps(['Sponsor Co']),
                  sponsor_types=json.dumps(['user']),
                  sponsor_usernames=json.dumps([other.username]))
        sponsor = self.draft.sponsors.get()
        self.assertEqual(sponsor.sponsor_id_object, other.pk)
        self.assertEqual(sponsor.sponsor_type,
                         ContentType.objects.get_for_model(Users))

    def test_an_unknown_sponsor_username_stays_a_name_only_sponsor(self):
        """A typo should not lose the sponsor, only the link."""
        self.edit(sponsor_names=json.dumps(['Ghost Co']),
                  sponsor_types=json.dumps(['user']),
                  sponsor_usernames=json.dumps(['nobody-by-that-name']))
        sponsor = self.draft.sponsors.get()
        self.assertEqual(sponsor.name, 'Ghost Co')
        self.assertIsNone(sponsor.sponsor_id_object)

    # ------------------------------------------------------------ the prizes

    def test_prize_rows_are_replaced_by_what_the_wizard_sends(self):
        TournamentPrizeDistribution.objects.create(
            tournament=self.draft, position=1, prize=Decimal('100'))
        self.edit(prize_data=json.dumps([
            {'position': 1, 'prize': '5000', 'extras': 'Trophy'},
            {'position': 2, 'prize': '2000', 'extras': ''},
        ]))
        rows = list(self.draft.prize_distributions.order_by('position')
                    .values_list('position', 'prize'))
        self.assertEqual(rows, [(1, Decimal('5000')), (2, Decimal('2000'))])

    def test_not_mentioning_prizes_leaves_them_alone(self):
        TournamentPrizeDistribution.objects.create(
            tournament=self.draft, position=1, prize=Decimal('100'))
        self.edit(tournament_title='Still Here')
        self.assertEqual(self.draft.prize_distributions.count(), 1)

    # ----------------------------------------------------------- the options

    def test_options_sent_by_the_wizard_are_applied(self):
        self.edit(options=json.dumps({'restrict_country': 'Nigeria'}))
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.options.get('restrict_country'), 'Nigeria')

    def test_editing_one_option_does_not_wipe_the_rest(self):
        """The guard the existing suite already had, kept here because this
        endpoint now writes options from two places."""
        self.edit(options=json.dumps({'restrict_country': 'Nigeria'}))
        self.edit(options=json.dumps({'check_in_required': True}))
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.options.get('restrict_country'), 'Nigeria')

    # ------------------------------------------------- the whole round trip

    def test_a_full_wizard_payload_lands_intact(self):
        """The case the CEO actually walked: reopen a draft, change the size,
        the seats and the sponsors, save, and find all three applied."""
        res = self.edit(
            tournament_title='Rivalvry Series S2',
            max_number_of_participants=5,
            min_number_of_participants=2,
            team_size=2,
            sponsor_names=json.dumps([]),
            sponsor_types=json.dumps([]),
            sponsor_usernames=json.dumps([]),
        )
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.tournament_title, 'Rivalvry Series S2')
        self.assertEqual(self.draft.max_number_of_teams, 5)
        self.assertEqual(self.draft.player_size, 5)
        self.assertEqual(self.draft.team_size, 2)
        self.assertEqual(self.draft.sponsors.count(), 0)

    def test_editing_a_draft_never_creates_a_second_tournament(self):
        """Two rows appeared on production, `rivalvry-series-s2` and
        `rivalvry-series-s2-2`. Whatever else happens, a PUT edits."""
        before = Tournament.objects.count()
        self.edit(tournament_title='Renamed Once')
        self.edit(tournament_title='Renamed Twice')
        self.assertEqual(Tournament.objects.count(), before)
