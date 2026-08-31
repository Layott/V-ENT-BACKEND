"""Somebody else's profile, the gallery release, and where a person is.

CEO, 31 August 2026:

  "I tried the view someone's profile, it opened and then I clicked on activity
  and it took me to my own profile. same for the other sub tabs"

  "under image gallery, should be able to upload images, and there should be
  another type of upload for those who want to upload their Esports pictures,
  let them know that the Esports images will be used publicly and inside events
  or tournaments. that they grant use of it to organizers for those events."

  "also the IP gets the wrong location, it says ilorin for me, but I am in Lagos
  currently"

The half of the first report that lived in the API: a public profile carried no
teams, no tournaments and no pictures, so the page had nothing of theirs to draw
and filled its panels from the reader's own endpoints. The tab addresses were a
frontend fault; this is the other half, and it is the one that made the page
show the wrong person's data even when it stayed put.

For the gallery the rule under test is that a licence which is not recorded is
not a licence: an esports upload without consent is refused, and consent is
written onto the row with the version of the wording it was given against.
"""
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from vent_tournament.models import Tournament, TournamentRegistration

from .models import Games, Teams, UserGallery, Users

PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08'
    b'\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00'
    b'\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def a_user(name):
    tag = uuid.uuid4().hex[:5]
    user = Users.objects.create(
        username='%s_%s' % (name, tag),
        email='%s_%s@vent.test' % (name, tag),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
        is_active=True,
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def an_image(name='shot.png'):
    return SimpleUploadedFile(name, PNG, content_type='image/png')


class PublicProfileTests(TestCase):
    def setUp(self):
        self.them, self.their_auth = a_user('them')
        self.me, self.my_auth = a_user('me')
        self.game, _ = Games.objects.get_or_create(game_title='Free Fire')

        self.their_team = Teams.objects.create(
            team_name='Their Squad %s' % uuid.uuid4().hex[:4], game=self.game,
            team_creator=self.them, team_owner=self.them,
            description='', penalty_points=0, number_of_members=1)
        self.my_team = Teams.objects.create(
            team_name='My Squad %s' % uuid.uuid4().hex[:4], game=self.game,
            team_creator=self.me, team_owner=self.me,
            description='', penalty_points=0, number_of_members=1)

        self.their_tournament = Tournament.objects.create(
            tournament_title='Their Cup', tournament_game=self.game,
            tournament_creator=self.them, is_draft=False,
            start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
        )
        Tournament.objects.create(
            tournament_title='My Cup', tournament_game=self.game,
            tournament_creator=self.me, is_draft=False,
            start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
        )

    def _profile(self, user, auth=None):
        res = self.client.get('/user/%s/profile/' % user.username, **(auth or {}))
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['data']

    def test_a_profile_carries_that_persons_teams_and_not_the_readers(self):
        data = self._profile(self.them, self.my_auth)
        names = [t['name'] for t in data['teams']]
        self.assertIn(self.their_team.team_name, names)
        self.assertNotIn(self.my_team.team_name, names)

    def test_a_team_owner_who_is_not_in_the_member_table_still_has_the_team(self):
        """Owning one and being a row in TeamMembers are separate facts, and
        reading only the member table reported "no teams" for somebody running
        five of them."""
        data = self._profile(self.them)
        self.assertEqual(len(data['teams']), 1)
        self.assertTrue(data['teams'][0]['is_owner'])

    def test_a_profile_carries_that_persons_tournaments_and_not_the_readers(self):
        data = self._profile(self.them, self.my_auth)
        titles = [t['name'] for t in data['tournaments']]
        self.assertIn('Their Cup', titles)
        self.assertNotIn('My Cup', titles)

    def test_playing_and_running_are_both_reported(self):
        other = Tournament.objects.create(
            tournament_title='Somebody Else Cup', tournament_game=self.game,
            tournament_creator=self.me, is_draft=False,
            start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
        )
        TournamentRegistration.objects.create(tournament=other, user=self.them)
        by_name = {t['name']: t['role'] for t in self._profile(self.them)['tournaments']}
        self.assertEqual(by_name['Their Cup'], 'organizer')
        self.assertEqual(by_name['Somebody Else Cup'], 'player')

    def test_a_draft_tournament_is_not_something_that_happened(self):
        Tournament.objects.create(
            tournament_title='Unpublished', tournament_game=self.game,
            tournament_creator=self.them, is_draft=True,
            start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
        )
        titles = [t['name'] for t in self._profile(self.them)['tournaments']]
        self.assertNotIn('Unpublished', titles)

    def test_the_address_is_the_username_and_an_old_numeric_link_still_works(self):
        self.assertEqual(
            self.client.get('/user/%s/profile/' % self.them.username).status_code, 200)
        self.assertEqual(
            self.client.get('/user/%d/profile/' % self.them.user_id).status_code, 200)


class GalleryReleaseTests(TestCase):
    def setUp(self):
        self.me, self.my_auth = a_user('shooter')
        self.them, self.their_auth = a_user('stranger')

    def _upload(self, auth, kind='personal', consent=None, name='a.png'):
        body = {'images': an_image(name), 'kind': kind}
        if consent is not None:
            body['consent'] = consent
        return self.client.post('/gallery/upload/', data=body, **auth)

    def test_a_personal_picture_uploads_and_is_not_released(self):
        res = self._upload(self.my_auth)
        self.assertEqual(res.status_code, 201, res.content)
        row = UserGallery.objects.get()
        self.assertEqual(row.kind, 'personal')
        self.assertIsNone(row.released_at)
        self.assertFalse(row.is_released)

    def test_an_esports_picture_without_consent_is_refused(self):
        res = self._upload(self.my_auth, kind='esports')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'CONSENT_REQUIRED')
        self.assertFalse(UserGallery.objects.exists())

    def test_consent_is_recorded_on_the_row_with_the_wording_version(self):
        res = self._upload(self.my_auth, kind='esports', consent='true')
        self.assertEqual(res.status_code, 201, res.content)
        row = UserGallery.objects.get()
        self.assertTrue(row.is_released)
        self.assertIsNotNone(row.released_at)
        self.assertEqual(row.release_terms_version, UserGallery.RELEASE_TERMS_VERSION)

    def test_the_wording_is_served_in_all_three_languages(self):
        res = self.client.get('/gallery/release-terms/')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        for language in ('en', 'fr', 'pt'):
            self.assertTrue(data[language].strip(), language)
        self.assertEqual(data['version'], UserGallery.RELEASE_TERMS_VERSION)

    def test_a_release_can_be_taken_back_without_deleting_the_picture(self):
        self._upload(self.my_auth, kind='esports', consent='true')
        row = UserGallery.objects.get()
        res = self.client.post('/gallery/withdraw-release/',
                               data={'image_id': row.id}, **self.my_auth)
        self.assertEqual(res.status_code, 200, res.content)
        row.refresh_from_db()
        self.assertFalse(row.is_released)
        self.assertEqual(row.kind, 'personal')
        self.assertTrue(UserGallery.objects.filter(id=row.id).exists())

    def test_a_released_picture_is_visible_to_a_stranger_and_a_personal_one_follows_the_profile(self):
        self._upload(self.my_auth, kind='esports', consent='true', name='e.png')
        self._upload(self.my_auth, kind='personal', name='p.png')

        res = self.client.get('/user/%s/gallery/' % self.me.username)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(len(data['esports']), 1)
        self.assertEqual(len(data['personal']), 1)   # this profile is public

    def test_nobody_can_upload_into_somebody_elses_gallery(self):
        res = self._upload(self.their_auth)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(UserGallery.objects.get().user_id, self.them.user_id)

    def test_the_limits_are_per_kind(self):
        """Somebody building an esports portfolio should not have to delete a
        holiday photograph to add a tournament one."""
        from .views_gallery_release import MAX_PERSONAL

        for i in range(MAX_PERSONAL):
            self.assertEqual(
                self._upload(self.my_auth, name='p%d.png' % i).status_code, 201)
        self.assertEqual(self._upload(self.my_auth, name='over.png').status_code, 400)
        # The esports slot is untouched by a full personal one.
        self.assertEqual(
            self._upload(self.my_auth, kind='esports', consent='true',
                         name='e.png').status_code, 201)


class LocationGuessTests(TestCase):
    """An IP places somebody in a country reliably and in a city barely at all.

    Nigerian mobile data routes through a handful of gateways, so a Lagos
    sign-in resolves to Ilorin - not occasionally, but for most of a network's
    subscribers.
    """

    def setUp(self):
        self.user, self.auth = a_user('traveller')

    def test_the_city_is_never_written_from_an_address(self):
        from unittest import mock

        from . import geo

        request = mock.Mock()
        request.META = {'REMOTE_ADDR': '102.89.0.1'}
        with mock.patch.object(geo, 'client_ip', return_value='102.89.0.1'), \
             mock.patch.object(geo, '_is_public', return_value=True), \
             mock.patch.object(geo, 'locate', return_value=('Nigeria', 'Ilorin')):
            geo.refresh_daily_location(self.user, request)

        self.user.refresh_from_db()
        self.assertEqual(self.user.country, 'Nigeria')
        self.assertFalse((self.user.state or '').strip(),
                         'a guessed city must never be written to the profile')
        self.assertTrue(self.user.country_is_guess)

    def test_a_country_the_person_chose_is_never_overwritten(self):
        from unittest import mock

        from . import geo

        self.user.country = 'Ghana'
        self.user.save(update_fields=['country'])

        request = mock.Mock()
        request.META = {'REMOTE_ADDR': '102.89.0.1'}
        with mock.patch.object(geo, 'client_ip', return_value='102.89.0.1'), \
             mock.patch.object(geo, '_is_public', return_value=True), \
             mock.patch.object(geo, 'locate', return_value=('Nigeria', 'Ilorin')):
            geo.refresh_daily_location(self.user, request)

        self.user.refresh_from_db()
        self.assertEqual(self.user.country, 'Ghana')
        self.assertFalse(self.user.country_is_guess)

    def test_setting_your_own_country_stops_it_being_a_guess(self):
        self.user.country = 'Nigeria'
        self.user.country_is_guess = True
        self.user.save(update_fields=['country', 'country_is_guess'])

        res = self.client.post(
            '/user/%s/update/' % self.user.user_id,
            data={'country': 'Nigeria', 'state': 'Lagos'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.user.refresh_from_db()
        self.assertFalse(self.user.country_is_guess)
        self.assertEqual(self.user.state, 'Lagos')
