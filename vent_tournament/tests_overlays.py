"""Uploading an HTML overlay and getting a URL for OBS.

CEO, 29 August 2026: "if users can upload html files, we should be able to get
links to paste inside obs or vmix or any streaming software of choice", for
"any html file".

The honest edge is tested here rather than glossed over: an unmarked file is
accepted and reported as undriveable, because that is what it is. An overlay
that renders and never changes is only discovered on air, and a warning at
upload is the entire difference.
"""

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Teams, Users
from vent_tournament import overlay_binding
from vent_tournament.models import (Tournament, TournamentOverlay,
                                    TournamentRegistration)


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, full_name=name.title(),
        login_session_token=('tk-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user


MARKED = """<!doctype html><html><head><title>Scoreboard</title></head><body>
  <h1 data-vent="tournament.title"></h1>
  <img data-vent-src="team.logo" alt="">
  <span data-vent="team.name"></span>
  <span data-vent="team.won">0</span>
  <table><tbody data-vent-repeat="standings">
    <tr><td data-vent="place"></td><td data-vent="name"></td><td data-vent="won"></td></tr>
  </tbody></table>
</body></html>"""

SCRIPTED = """<!doctype html><html><head><script>
  window.build = function () { document.title = window.VENT.tournament.title; };
</script></head><body><div id="team"></div></body></html>"""

PLAIN = """<!doctype html><html><head><title>Pretty</title></head>
<body><div>ALIEN X</div><div>12 points</div></body></html>"""


class OverlayBindingTests(TestCase):
    """What a file turns out to be, decided once at upload."""

    def test_a_marked_file_is_marked(self):
        binding, fields, warnings = overlay_binding.inspect(MARKED)
        self.assertEqual(binding, overlay_binding.MARKED)
        self.assertIn('team.name', fields)
        self.assertIn('standings', fields)
        self.assertEqual(warnings, [])

    def test_a_scripted_file_is_scripted(self):
        binding, fields, _ = overlay_binding.inspect(SCRIPTED)
        self.assertEqual(binding, overlay_binding.SCRIPTED)

    def test_an_unmarked_file_is_reported_as_undriveable(self):
        binding, fields, warnings = overlay_binding.inspect(PLAIN)
        self.assertEqual(binding, overlay_binding.NONE)
        self.assertEqual(fields, [])
        self.assertTrue(warnings, 'an undriveable file was accepted silently')
        self.assertIn('never change', warnings[0])

    def test_a_name_the_runtime_cannot_fill_is_reported(self):
        _, fields, _ = overlay_binding.inspect(
            '<div data-vent="team.favourite_colour"></div>')
        self.assertEqual(overlay_binding.unknown_fields(fields),
                         ['team.favourite_colour'])

    def test_a_file_that_makes_its_own_network_calls_is_flagged(self):
        _, _, warnings = overlay_binding.inspect(
            '<div data-vent="team.name"></div><script>fetch("/x")</script>')
        self.assertTrue(any('network' in w for w in warnings))


class OverlayUploadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.game = Games.objects.get_or_create(game_title='Overlay Probe')[0]
        self.organiser = a_user('ov_org')
        self.stranger = a_user('ov_stranger')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Overlay Probe Cup', tournament_creator=self.organiser,
            tournament_game=self.game, tournament_type='online',
            tournament_access='team', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            start_date_and_time=now + timezone.timedelta(days=2),
            end_date_and_time=now + timezone.timedelta(days=3),
            is_draft=False)

    def _as(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)

    def _key(self):
        return self.tournament.slug or self.tournament.tournament_id

    def _upload(self, markup=MARKED, name='scoreboard.html'):
        self._as(self.organiser)
        return self.client.post(
            '/tournament/%s/overlays/' % self._key(),
            {'file': SimpleUploadedFile(name, markup.encode('utf-8'),
                                        content_type='text/html')},
            format='multipart')

    # ------------------------------------------------------------ the URL

    def test_uploading_gives_back_a_url_to_paste(self):
        res = self._upload()
        self.assertEqual(res.status_code, 201, res.data)
        url = res.data['data']['overlay']['url']
        self.assertIn('/overlay/', url)
        self.assertTrue(url.startswith('http'), url)

    def test_that_url_serves_the_file_with_the_runtime_in_front_of_it(self):
        res = self._upload()
        url = res.data['data']['overlay']['url']
        path = url.split('testserver', 1)[-1]

        self.client.credentials()          # OBS has no session at all
        page = self.client.get(path)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode('utf-8')

        # The uploader's own markup, untouched.
        self.assertIn('data-vent="team.name"', body)
        # And the runtime, ahead of it.
        self.assertIn('vent-overlay-runtime', body)
        self.assertLess(body.index('vent-overlay-runtime'),
                        body.index('data-vent="team.name"'),
                        'the runtime arrives after the overlay reads it')
        self.assertIn('overlay-feed', body)

    def test_the_page_is_not_cached(self):
        url = self._upload().data['data']['overlay']['url']
        self.client.credentials()
        page = self.client.get(url.split('testserver', 1)[-1])
        self.assertIn('no-store', page['Cache-Control'])

    def test_a_dead_link_says_so_rather_than_erroring(self):
        self.client.credentials()
        page = self.client.get('/overlay/not-a-real-token/')
        self.assertEqual(page.status_code, 404)
        self.assertIn('not valid any more', page.content.decode('utf-8'))

    def test_rotating_changes_the_url_and_kills_the_old_one(self):
        first = self._upload().data['data']['overlay']
        self._as(self.organiser)
        rotated = self.client.post(
            '/tournament/%s/overlays/%d/rotate/' % (self._key(), first['id']))
        self.assertEqual(rotated.status_code, 200, rotated.data)
        self.assertNotEqual(rotated.data['data']['overlay']['url'], first['url'])

        self.client.credentials()
        dead = self.client.get(first['url'].split('testserver', 1)[-1])
        self.assertEqual(dead.status_code, 404)

    # ------------------------------------------------------- what was uploaded

    def test_an_unmarked_file_uploads_with_a_warning_rather_than_silently(self):
        res = self._upload(PLAIN, 'pretty.html')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['data']['overlay']['binding'], 'none')
        self.assertTrue(res.data['data']['warnings'])

    def test_a_scripted_file_is_recognised(self):
        res = self._upload(SCRIPTED, 'champion.html')
        self.assertEqual(res.data['data']['overlay']['binding'], 'scripted')

    def test_the_fields_it_binds_are_reported_back(self):
        res = self._upload()
        fields = res.data['data']['overlay']['bound_fields']
        self.assertIn('team.name', fields)
        self.assertIn('tournament.title', fields)

    def test_something_that_is_not_html_is_refused(self):
        self._as(self.organiser)
        res = self.client.post(
            '/tournament/%s/overlays/' % self._key(),
            {'file': SimpleUploadedFile('overlay.png', b'\x89PNG',
                                        content_type='image/png')},
            format='multipart')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(TournamentOverlay.objects.count(), 0)

    # ------------------------------------------------------------- who may

    def test_only_the_organiser_can_upload(self):
        self._as(self.stranger)
        res = self.client.post(
            '/tournament/%s/overlays/' % self._key(),
            {'file': SimpleUploadedFile('x.html', MARKED.encode('utf-8'),
                                        content_type='text/html')},
            format='multipart')
        self.assertEqual(res.status_code, 403, res.data)
        self.assertEqual(TournamentOverlay.objects.count(), 0)

    def test_signed_out_cannot_upload(self):
        self.client.credentials()
        res = self.client.post(
            '/tournament/%s/overlays/' % self._key(),
            {'file': SimpleUploadedFile('x.html', MARKED.encode('utf-8'),
                                        content_type='text/html')},
            format='multipart')
        self.assertEqual(res.status_code, 401, res.data)

    def test_only_the_organiser_sees_the_list(self):
        self._upload()
        self._as(self.stranger)
        res = self.client.get('/tournament/%s/overlays/' % self._key())
        self.assertEqual(res.status_code, 403, res.data)

    def test_an_overlay_can_be_removed(self):
        overlay = self._upload().data['data']['overlay']
        self._as(self.organiser)
        res = self.client.delete(
            '/tournament/%s/overlays/%d/' % (self._key(), overlay['id']))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(TournamentOverlay.objects.count(), 0)


class OverlayFeedTests(TestCase):
    """The data the runtime fetches."""

    def setUp(self):
        self.client = APIClient()
        self.game = Games.objects.get_or_create(game_title='Feed Probe')[0]
        self.organiser = a_user('feed_org')
        self.captain = a_user('feed_captain')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Feed Probe Cup', tournament_creator=self.organiser,
            tournament_game=self.game, tournament_type='online',
            tournament_access='team', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            start_date_and_time=now + timezone.timedelta(days=2),
            end_date_and_time=now + timezone.timedelta(days=3),
            is_draft=False)
        self.team = Teams.objects.create(
            team_name='Alien X', game=self.game, team_creator=self.captain,
            team_owner=self.captain, description='x', penalty_points=0,
            number_of_members=1)
        TournamentRegistration.objects.create(
            tournament=self.tournament, team=self.team, status='confirmed')

    def _key(self):
        return self.tournament.slug or self.tournament.tournament_id

    def test_the_feed_is_public_because_obs_cannot_sign_in(self):
        self.client.credentials()
        res = self.client.get('/tournament/%s/overlay-feed/' % self._key())
        self.assertEqual(res.status_code, 200, res.data)

    def test_it_carries_the_teams_in_the_shape_an_overlay_reads(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self._key())
        team = res.data['data']['teams'][0]
        for key in ['tag', 'name', 'logo', 'players', 'place', 'won', 'lost']:
            self.assertIn(key, team)

    def test_it_carries_the_tournament(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self._key())
        self.assertEqual(res.data['data']['tournament']['title'],
                         'Feed Probe Cup')

    def test_it_carries_a_version_so_an_overlay_can_skip_a_redraw(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self._key())
        self.assertTrue(res.data['data']['version'])

    def test_a_logo_is_an_absolute_url_because_obs_is_not_on_our_page(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self._key())
        logo = res.data['data']['teams'][0]['logo']
        if logo:
            self.assertTrue(logo.startswith('http'), logo)

    def test_an_unknown_tournament_is_not_found(self):
        res = self.client.get('/tournament/no-such-thing/overlay-feed/')
        self.assertEqual(res.status_code, 404)
