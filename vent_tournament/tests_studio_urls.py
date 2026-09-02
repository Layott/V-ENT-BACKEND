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
from vent_tournament.models import BroadcastElement, Tournament


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
        """Next serves `/studio/<token>/<kind>` without one. The API's own
        trailing-slash convention is what had put one there, which is the sort
        of detail that only matters when the two ends are different servers."""
        url = self.start()['urls']['scorebar']
        self.assertFalse(url.endswith('/'), url)
        self.assertRegex(url, r'^https://v-ent\.co/studio/[^/]+/scorebar$')

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
                         sorted(k for k, _label in BroadcastElement.KINDS))
