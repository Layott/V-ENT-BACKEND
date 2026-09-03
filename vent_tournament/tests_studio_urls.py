"""The URLs the studio hands an organiser have to be pages that exist.

Walking gate 12 signed in, pressing Copy URL put this on the clipboard:

    https://api.v-ent.co/studio/<token>/scorebar/

That is the API host. The element pages are FRONTEND routes served by Next at
`v-ent.co`; the only thing under `/studio/<token>/` on the API is `/feed/`. So
every URL an organiser copied 404d, and pasted into OBS it gives a blank
browser source.

The one thing this feature exists to produce was unusable, and nothing could
report it: the endpoint answered 200 with a perfectly well-formed URL to a page
that does not exist. `request.build_absolute_uri` builds against whatever host
made the request, and the host making it is always the API, because it is the
frontend calling.

The feed is different and must stay on the API, because it IS an API route.
Two bases, not one.
"""
from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory

from vent_tournament import views_overlays
from vent_tournament.models import (
    BroadcastElement, Tournament, TournamentOverlay)


@override_settings(FRONTEND_URL='https://v-ent.co')
class StudioUrlTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='studioOwner', email='so@vent.test',
            login_session_token='studio-owner-t'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}
        game = Games.objects.create(game_title='Studio FC')
        self.tournament = Tournament.objects.create(
            tournament_title='Studio Cup', tournament_game=game,
            tournament_creator=self.owner,
            start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=False)

    def start(self):
        res = self.client.post(
            '/tournament/%s/studio/sessions/' % self.tournament.slug,
            data={'name': 'Walk'}, content_type='application/json', **self.auth)
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        return res.json()['data']['session']

    def test_every_element_url_points_at_the_frontend(self):
        session = self.start()
        self.assertTrue(session['urls'])
        for kind, url in session['urls'].items():
            self.assertTrue(url.startswith('https://v-ent.co/studio/'),
                            '%s -> %s' % (kind, url))
            self.assertNotIn('api.v-ent.co', url, kind)

    def test_the_url_carries_no_trailing_slash(self):
        """Next serves these without one. The API's own trailing-slash
        convention is what had put one there, which is the sort of detail that
        only matters when the two ends are different servers.

        The address gained the owner's name on 3 September, so it now reads
        `/studio/<slug>/<graphic>/<token>` and the token is last. The old
        shape is still published as `legacy_urls` and still resolves."""
        session = self.start()
        url = session['urls']['scorebar']
        self.assertFalse(url.endswith('/'), url)
        self.assertRegex(url, r'^https://v-ent\.co/studio/[^/]+/scorebar/[^/]+$')
        legacy = session['legacy_urls']['scorebar']
        self.assertFalse(legacy.endswith('/'), legacy)
        self.assertRegex(legacy, r'^https://v-ent\.co/studio/[^/]+/scorebar$')

    def test_the_feed_stays_on_the_api(self):
        """It IS an API route. Pointing it at the frontend would break the
        thing the element pages poll."""
        session = self.start()
        self.assertIn('/studio/', session['feed'])
        self.assertTrue(session['feed'].endswith('/feed/'))
        # The API host, whatever `build_absolute_uri` resolved it to in this
        # test client, and NOT the configured frontend.
        self.assertFalse(session['feed'].startswith('https://v-ent.co/'),
                         session['feed'])

    def test_there_is_one_url_per_shipped_element(self):
        session = self.start()
        self.assertEqual(sorted(session['urls']),
                         sorted(k for k, _label in BroadcastElement.TOURNAMENT_KINDS))


@override_settings(FRONTEND_URL='https://v-ent.co')
class RetiredSessionTests(TestCase):
    """"URLs retired" has to be true, and has to clear the screen doing it.

    The console tells an operator the URLs "stop working when you end it". The
    feed kept serving the whole payload for ever, so the sentence was a shade
    stronger than the code.

    The fix is not a 404. The runtime keeps its last good frame on anything
    that is not a success - deliberately, so a dropped connection does not
    blank a graphic mid-match - so refusing would FREEZE whatever was on screen
    at the exact moment the operator wanted it gone.
    """

    def setUp(self):
        self.owner = Users.objects.create(
            username='retireOwner', email='ro@vent.test',
            login_session_token='retire-owner-t'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}
        game = Games.objects.create(game_title='Retire FC')
        self.tournament = Tournament.objects.create(
            tournament_title='Retire Cup', tournament_game=game,
            tournament_creator=self.owner,
            start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=False)

        res = self.client.post(
            '/tournament/%s/studio/sessions/' % self.tournament.slug,
            data={'name': 'Show'}, content_type='application/json', **self.auth)
        self.session = res.json()['data']['session']
        self.token = self.session['token']

    def put_on_air(self):
        return self.client.post(
            '/tournament/%s/studio/sessions/%s/element/scorebar/'
            % (self.tournament.slug, self.session['id']),
            data={'active': True, 'payload': {'caption': 'Leg 2'}},
            content_type='application/json', **self.auth)

    def end(self):
        return self.client.post(
            '/tournament/%s/studio/sessions/%s/'
            % (self.tournament.slug, self.session['id']),
            data={'end': True}, content_type='application/json', **self.auth)

    def feed(self):
        return self.client.get('/studio/%s/feed/' % self.token)

    def test_a_live_session_serves_normally(self):
        self.put_on_air()
        body = self.feed().json()['data']
        self.assertTrue(body['elements']['scorebar']['active'])
        self.assertFalse(body.get('retired', False))

    def test_an_ended_session_says_it_is_retired(self):
        self.put_on_air()
        self.end()
        body = self.feed().json()['data']
        self.assertTrue(body['retired'])

    def test_an_ended_session_clears_the_screen_rather_than_freezing_it(self):
        """Everything comes back inactive, so the graphic that was on air
        renders one more time as nothing and goes. A 404 here would leave it
        frozen on screen after the show."""
        self.put_on_air()
        self.end()
        res = self.feed()
        self.assertEqual(res.status_code, 200)
        elements = res.json()['data']['elements']
        self.assertEqual([k for k, v in elements.items() if v['active']], [])

    def test_the_retired_version_differs_so_the_page_redraws_once(self):
        """The runtime only redraws when `version` moves. If a retired feed
        reused the live version the screen would never clear."""
        self.put_on_air()
        before = self.feed().json()['data']['version']
        self.end()
        self.assertNotEqual(before, self.feed().json()['data']['version'])

    def test_a_token_that_never_existed_is_refused(self):
        res = self.client.get('/studio/not-a-real-token/feed/')
        self.assertEqual(res.status_code, 404)

    def test_starting_a_new_broadcast_retires_the_old_link(self):
        """Starting a second one ends the first, so its URLs go quiet without
        the operator having to remember."""
        self.put_on_air()
        self.client.post(
            '/tournament/%s/studio/sessions/' % self.tournament.slug,
            data={'name': 'Second show'}, content_type='application/json',
            **self.auth)
        self.assertTrue(self.feed().json()['data']['retired'])


class UploadedOverlayAddressTests(TestCase):
    """An uploaded overlay's URL says what it is.

    CEO, 3 September 2026: "can the urls for the overlays posses the names of
    the overlays, depending on the project or event or tournament the studio is
    working with, so slugs for the urls also."

    The studio's own graphics got named addresses. The files people upload did
    not, which is the wrong half to miss: an organiser has one folder of HTML
    and eight tabs of identical token URLs, and telling them apart IS the
    problem.
    """

    def setUp(self):
        self.owner = Users.objects.create(
            username='ovAddr', email='ov@vent.test', is_active=True,
            login_session_token='ovaddrtoken1234')
        game = Games.objects.create(game_title='EA FC ADDR')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Address Cup', tournament_game=game,
            tournament_creator=self.owner,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False, bracket_type='round_robin')
        self.overlay = TournamentOverlay.objects.create(
            tournament=self.tournament, name='Score bar.html',
            file=SimpleUploadedFile('score-bar.html',
                                    b'<div data-vent="team.name"></div>'),
            binding='marked', bound_fields=['team.name'])

    def url_of(self):
        request = APIRequestFactory().get('/')
        return views_overlays.serialize(self.overlay, request)['url']

    def test_the_address_carries_the_tournament_and_the_overlay_name(self):
        url = self.url_of()
        self.assertIn('/overlay/%s/score-bar/%s/'
                      % (self.tournament.slug, self.overlay.token), url)
        # The extension is an artefact of how the file arrived, not a name.
        self.assertNotIn('.html', url)

    def test_the_named_address_opens_the_overlay(self):
        res = self.client.get('/overlay/%s/score-bar/%s/'
                              % (self.tournament.slug, self.overlay.token))
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'data-vent="team.name"', res.content)

    def test_the_bare_address_still_opens_it_for_ever(self):
        """Already pasted into a scene collection that may not open for months."""
        res = self.client.get('/overlay/%s/' % self.overlay.token)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'data-vent="team.name"', res.content)

    def test_a_stale_name_still_opens_it(self):
        """A rename must not 404 somebody mid-broadcast. The token decides."""
        res = self.client.get('/overlay/last-months-name/whatever-it-was/%s/'
                              % self.overlay.token)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'data-vent="team.name"', res.content)

    def test_a_wrong_token_is_refused_however_right_the_names_look(self):
        res = self.client.get('/overlay/%s/score-bar/notatoken/'
                              % self.tournament.slug)
        self.assertEqual(res.status_code, 404)

    def test_the_address_follows_a_rename(self):
        self.overlay.name = 'Lower third.html'
        self.overlay.save()
        self.assertIn('/lower-third/', self.url_of())

    def test_a_name_that_slugifies_to_nothing_still_makes_an_address(self):
        self.overlay.name = '...html'
        self.overlay.save()
        url = self.url_of()
        self.assertIn('/overlay/', url)
        self.assertIn(self.overlay.token, url)
        self.assertNotIn('//overlay', url.replace('https://', ''))
