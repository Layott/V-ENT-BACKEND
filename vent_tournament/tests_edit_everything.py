"""An organiser can edit every part of a tournament they created.

CEO, 2 September 2026: "Users should be able to edit everry single thing about
a tournament they created, cause i did not even see where to edit tournament
name or banner or image, or game or bracket and all of thst stuff".

Two separate faults sat behind that. There was no edit screen at all, and the
endpoint it would have called could not change the **game**, so an organiser who
picked the wrong one in the wizard had to build the tournament again.

The test that matters most here is the last one: it walks the field list on the
model and fails when something is not reachable through the endpoint. A test
that only checks the fields I remembered would pass on the day and drift the
moment a column is added.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.client import MULTIPART_CONTENT, encode_multipart
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_tournament.models import Tournament


def a_png():
    """The smallest thing Django's ImageField will accept."""
    import base64
    raw = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8'
        'z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
    return SimpleUploadedFile('art.png', raw, content_type='image/png')


class EditEverythingTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='editOwner', email='eo@vent.test',
            login_session_token='edit-owner-tk'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer %s' % self.owner.login_session_token}

        self.stranger = Users.objects.create(
            username='editStranger', email='es@vent.test',
            login_session_token='edit-strngr-tk'[:16], is_active=True)
        self.stranger.login_session_created_at = timezone.now()
        self.stranger.save()
        self.stranger_auth = {
            'HTTP_AUTHORIZATION': 'Bearer %s' % self.stranger.login_session_token}

        # get_or_create, not create: a migration already seeds the catalogue,
        # and game_title is unique.
        self.free_fire, _ = Games.objects.get_or_create(game_title='Free Fire')
        self.eafc, _ = Games.objects.get_or_create(game_title='EA FC 26')

        self.t = Tournament.objects.create(
            tournament_title='Original Name',
            tournament_game=self.free_fire,
            tournament_creator=self.owner,
            start_date_and_time=timezone.now() + timezone.timedelta(days=7),
            end_date_and_time=timezone.now() + timezone.timedelta(days=8),
            is_draft=False,
        )

    def edit(self, auth=None, **body):
        return self.client.put(
            '/tournament/edit-tournament/%d/' % self.t.tournament_id,
            data=body, content_type='application/json',
            **(self.auth if auth is None else auth))

    # ------------------------------------------------------------ the basics

    def test_the_name_can_be_changed(self):
        res = self.edit(tournament_title='Renamed Cup')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_title, 'Renamed Cup')

    def test_renaming_moves_the_address_and_keeps_the_old_one(self):
        before = self.t.slug
        self.edit(tournament_title='Renamed Cup')
        self.t.refresh_from_db()
        self.assertNotEqual(self.t.slug, before)

    def test_the_bracket_can_be_changed(self):
        res = self.edit(bracket_type='double_elimination')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.t.refresh_from_db()
        self.assertIn('ouble', self.t.bracket_type)

    # -------------------------------------------------------------- the game

    def test_the_game_can_be_changed_by_name(self):
        """The one that was missing. Picking the wrong game in the wizard
        meant building the tournament again."""
        res = self.edit(tournament_game='EA FC 26')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_game, self.eafc)

    def test_the_game_can_be_changed_by_id(self):
        res = self.edit(tournament_game=self.eafc.game_id)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_game, self.eafc)

    def test_an_unknown_game_is_refused_by_name(self):
        res = self.edit(tournament_game='Some Game That Does Not Exist')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'GAME_NOT_FOUND')
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_game, self.free_fire)

    def test_changing_the_game_clears_a_series_from_the_old_one(self):
        """A series belongs to a game. Keeping one across a game change leaves
        a pairing that means nothing."""
        from vent_auth.models import GameSeries
        series = GameSeries.objects.create(game=self.free_fire, name='Season 1')
        self.t.tournament_series = series
        self.t.save(update_fields=['tournament_series'])

        self.edit(tournament_game='EA FC 26')
        self.t.refresh_from_db()
        self.assertIsNone(self.t.tournament_series)

    # ------------------------------------------------------------ the images

    def test_the_logo_and_banner_can_be_replaced(self):
        # Django's test client does not encode multipart on PUT by itself, so
        # it is encoded here. `format='multipart'` is a DRF APIClient idea and
        # arrives as application/octet-stream through this one.
        res = self.client.put(
            '/tournament/edit-tournament/%d/' % self.t.tournament_id,
            data=encode_multipart(
                'BoUnDaRyStRiNg',
                {'tournament_logo': a_png(), 'tournament_banner': a_png()}),
            content_type=MULTIPART_CONTENT, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.t.refresh_from_db()
        self.assertTrue(self.t.tournament_logo)
        self.assertTrue(self.t.tournament_banner)

    # ------------------------------------------------------------- refusals

    def test_a_stranger_cannot_edit_it(self):
        res = self.edit(auth=self.stranger_auth, tournament_title='Hijacked')
        self.assertEqual(res.status_code, 403)
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_title, 'Original Name')

    def test_a_signed_out_caller_cannot_edit_it(self):
        res = self.client.put(
            '/tournament/edit-tournament/%d/' % self.t.tournament_id,
            data={'tournament_title': 'Hijacked'}, content_type='application/json')
        self.assertIn(res.status_code, (401, 403))

    def test_ending_before_starting_is_refused(self):
        res = self.edit(
            start_date_and_time=(timezone.now() + timezone.timedelta(days=9)).isoformat(),
            end_date_and_time=(timezone.now() + timezone.timedelta(days=2)).isoformat())
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'END_BEFORE_START')

    def test_a_cap_below_the_floor_is_refused(self):
        res = self.edit(min_number_of_teams=16, max_number_of_teams=8)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'MIN_ABOVE_MAX')

    def test_a_number_field_sent_as_words_names_the_field(self):
        res = self.edit(max_number_of_teams='loads')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['field'], 'max_number_of_teams')

    # --------------------------------------------------- nothing left behind

    def test_every_field_an_organiser_set_can_be_changed_again(self):
        """The guard against drift. Anything the create wizard can set has to
        be reachable from the edit endpoint, or an organiser is stuck with
        whatever they chose first.

        Columns excluded on purpose are listed with the reason, so adding a
        column and forgetting the edit path fails here rather than in a
        support message six weeks later.
        """
        never_editable = {
            # identity and bookkeeping, not organiser-set content
            'tournament_id', 'slug', 'tournament_creator', 'interaction_count',
            'is_draft', 'created_at', 'updated_at',
            # set through their own endpoints, which have their own rules
            'tournament_organization', 'tournament_series', 'sponsors',
            # multipart rather than JSON, covered by their own tests above
            'tournament_logo', 'tournament_banner', 'rules_document',
            # Lifecycle. These are the RESULT of an action - cancelling,
            # completing, closing check-in - and writing them directly would
            # let a tournament claim to be cancelled with nothing refunded.
            'status', 'cancelled_at', 'cancelled_reason', 'completed_at',
            'check_in_closed_at',
            # The 22 organiser settings, which have their own endpoint and
            # their own per-key validation.
            'options',
        }
        editable_here = {
            'tournament_title', 'tournament_description', 'tournament_rules',
            'tournament_location', 'virtual_link', 'tournament_visibility',
            'tournament_type', 'bracket_type', 'tournament_access',
            'entry_fee', 'entry_fee_price', 'team_size', 'player_size',
            'min_number_of_teams', 'max_number_of_teams', 'prize_type',
            'game_mode', 'start_date_and_time', 'end_date_and_time',
            'facebook_link', 'twitter_link', 'instagram_link', 'youtube_link',
            'twitch_link', 'kick_link', 'tiktok_link', 'bigolive_link',
            'tournament_game', 'registration_opens_at', 'registration_closes_at',
            'prize_currency', 'prize_pool_total', 'prize_pool_total_vc',
            'approve_registrations', 'score_confirmation_mode',
        }

        columns = {f.name for f in Tournament._meta.get_fields()
                   if getattr(f, 'concrete', False) or f.many_to_many}
        unreachable = columns - editable_here - never_editable
        self.assertEqual(
            unreachable, set(),
            'These columns cannot be changed after creation and are not listed '
            'as deliberately fixed: %s' % sorted(unreachable))
