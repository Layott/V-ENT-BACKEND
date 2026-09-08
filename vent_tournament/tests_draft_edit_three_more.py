"""Three more fields the edit endpoint dropped, found by the round-trip checker.

CEO, 7 September 2026: "please all the checkers and make sure they are not
pointing to anything and fix them if they are."

`tools/check-wizard-roundtrip.py` had been reporting 19 broken links for weeks.
Reading all 19 by hand, ten were the checker failing to follow a helper call
and four were the same field under two names. Three were real, and all three
are the same fault the CEO reported on 2 September as "0/32 slots": the wizard
POSTs to `create_tournament` and PUTs to `edit_tournament`, create understands
the field and edit does not, so continuing a draft silently discards it.

    hide_location    switched on while continuing a draft, and the venue
                     stayed public
    winner_prize     corrected while continuing a draft, and the old figure
                     stayed. It is not a column: create writes it as the
                     position-1 prize row, so edit has to rewrite that row
    organization     the DRAFT MAPPER had no key for it, and the wizard
                     appends `organization` on every submit with `|| ''`
                     behind it. So re-opening any draft posted an empty
                     organisation and CLEARED whichever one was chosen. Worse
                     than dropping a value: it destroyed one

The first two are tested here. The third is a frontend mapper and is covered
by the round-trip checker itself, which is what found it.
"""
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Organization, Users
from vent_tournament.models import Tournament, TournamentPrizeDistribution


class EditKeepsHideLocationAndPrizeTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='draft3', email='d3@vent.test',
            login_session_token='draft3-token'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}
        self.game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.draft = Tournament.objects.create(
            tournament_title='Lagos Invitational',
            tournament_game=self.game,
            tournament_creator=self.owner,
            start_date_and_time=timezone.now() + timezone.timedelta(days=7),
            end_date_and_time=timezone.now() + timezone.timedelta(days=8),
            bracket_type='single_elimination',
            team_size=1,
            tournament_location='12 Awolowo Road, Ikoyi',
            is_draft=True,
        )

    def edit(self, **body):
        return self.client.put(
            '/tournament/edit-tournament/%d/' % self.draft.tournament_id,
            data=body, content_type='application/json', **self.auth)

    # ------------------------------------------------------- hide_location

    def test_hiding_the_location_actually_hides_it(self):
        res = self.edit(hide_location=True)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.draft.refresh_from_db()
        self.assertIsNone(self.draft.tournament_location)

    def test_unhiding_restores_the_location_the_organiser_typed(self):
        """Switching it back off is not a no-op: the wizard sends the venue in
        the same request, and that value is the organiser's newer answer."""
        self.edit(hide_location=True)
        res = self.edit(hide_location=False,
                        tournament_location='National Stadium, Surulere')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.tournament_location,
                         'National Stadium, Surulere')

    def test_leaving_it_alone_changes_nothing(self):
        """A request that does not mention it must not touch the venue."""
        res = self.edit(tournament_title='Lagos Invitational 2')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.tournament_location, '12 Awolowo Road, Ikoyi')

    # -------------------------------------------------------- winner_prize

    def test_the_winner_prize_is_written(self):
        res = self.edit(winner_prize=250, prize_currency='VC')
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = self.draft.prize_distributions.get(position=1)
        self.assertEqual(row.prize, 250)

    def test_correcting_it_rewrites_the_row_rather_than_adding_one(self):
        """Two position-1 rows would double the advertised prize pool."""
        self.edit(winner_prize=250, prize_currency='VC')
        self.edit(winner_prize=400, prize_currency='VC')
        rows = self.draft.prize_distributions.filter(position=1)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().prize, 400)

    def test_the_older_field_name_works_too(self):
        """create accepts `total_prize` as well, so edit has to."""
        res = self.edit(total_prize=99, prize_currency='VC')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(self.draft.prize_distributions.get(position=1).prize, 99)

    def test_something_that_is_not_a_number_is_refused_by_name(self):
        res = self.edit(winner_prize='lots')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'PRIZE_NOT_A_NUMBER')

    def test_not_sending_it_leaves_the_prize_alone(self):
        TournamentPrizeDistribution.objects.create(
            tournament=self.draft, position=1, prize=500, currency='VC')
        self.edit(tournament_title='Renamed')
        self.assertEqual(self.draft.prize_distributions.get(position=1).prize, 500)


class EditKeepsTheOrganisationTests(TestCase):
    """The organisation must survive a save that names it, and a save that
    does not.

    The destructive half was on the frontend, but the endpoint's behaviour is
    what makes it destructive, so it is worth pinning: an ABSENT key means
    unchanged, and an empty one means the organiser's own name.
    """

    def setUp(self):
        self.owner = Users.objects.create(
            username='orgdraft', email='od@vent.test',
            login_session_token='orgdraft-token'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}
        self.game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.org = Organization.objects.create(
            org_name='Cade Esports Draft', org_creator=self.owner,
            org_owner=self.owner)
        self.draft = Tournament.objects.create(
            tournament_title='Org Draft',
            tournament_game=self.game,
            tournament_creator=self.owner,
            tournament_organization=self.org,
            start_date_and_time=timezone.now() + timezone.timedelta(days=7),
            end_date_and_time=timezone.now() + timezone.timedelta(days=8),
            bracket_type='single_elimination',
            team_size=1,
            is_draft=True,
        )

    def edit(self, **body):
        return self.client.put(
            '/tournament/edit-tournament/%d/' % self.draft.tournament_id,
            data=body, content_type='application/json', **self.auth)

    def test_a_save_that_does_not_mention_it_keeps_it(self):
        res = self.edit(tournament_title='Org Draft 2')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.tournament_organization_id, self.org.org_id)

    def test_sending_it_back_keeps_it(self):
        """What the fixed draft mapper now does on every continue."""
        res = self.edit(organization=self.org.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.tournament_organization_id, self.org.org_id)

    def test_sending_an_empty_one_clears_it(self):
        """Deliberate, and the reason the missing mapper key was destructive:
        an organiser has to be able to take a tournament out of an
        organisation, so empty cannot mean "unchanged"."""
        res = self.edit(organization='')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.draft.refresh_from_db()
        self.assertIsNone(self.draft.tournament_organization_id)
