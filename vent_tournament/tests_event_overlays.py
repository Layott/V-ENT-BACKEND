"""An event can have stream overlays too.

CEO: organisers "upload designs as html using the prompt they copy from the
site ... users should be able to upload html files they want to turn to stream
elements and basically link data from a particular tournament OR EVENT on the
site to that stream element ... pick from existing stream element templates for
tournaments and events."

`TournamentOverlay.tournament` was a required foreign key, so an organiser
running an event had nowhere to upload a design and no URL to paste into OBS.
An event has a programme, a door count, ticket sales and sponsors, all of which
somebody wants on a screen behind a stage.

Same shape of gap as short links, and `tools/check-parity.py` has a row for
this pair for exactly that reason.
"""
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_event.models import Event
from vent_tournament.models import TournamentOverlay


def an_overlay(markup=None):
    body = markup or (
        '<html><body>'
        '<div data-vent="event.name">Some event</div>'
        '<div data-vent="event.now_on">Something</div>'
        '</body></html>')
    return SimpleUploadedFile('overlay.html', body.encode('utf-8'),
                              content_type='text/html')


class EventOverlayTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='ovOwner', email='oo@vent.test',
            login_session_token='ov-owner-token'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}

        self.stranger = Users.objects.create(
            username='ovStranger', email='os@vent.test',
            login_session_token='ov-stranger-tk'[:16], is_active=True)
        self.stranger.login_session_created_at = timezone.now()
        self.stranger.save()
        self.stranger_auth = {
            'HTTP_AUTHORIZATION': 'Bearer %s' % self.stranger.login_session_token}

        self.event = Event.objects.create(
            name='Lagos Anime Con', creator=self.owner, event_type='physical',
            desc='x', entry_fee=Decimal('0'),
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=timezone.now().date(),
            start_time=timezone.now().time(), end_time=timezone.now().time())

    def url(self, suffix=''):
        return '/event/%s/overlays/%s' % (self.event.slug, suffix)

    def upload(self, auth=None, markup=None, name='Now and next'):
        return self.client.post(
            self.url(), data={'file': an_overlay(markup), 'name': name},
            **(self.auth if auth is None else auth))

    # -------------------------------------------------------- the upload

    def test_an_organiser_can_upload_a_design_for_an_event(self):
        res = self.upload()
        self.assertEqual(res.status_code, 201, res.content[:400])
        overlay = TournamentOverlay.objects.get()
        self.assertEqual(overlay.event, self.event)
        self.assertIsNone(overlay.tournament)

    def test_the_answer_carries_a_url_to_paste_into_obs(self):
        """The whole reason the feature exists."""
        res = self.upload()
        url = res.json()['data']['overlay']['url']
        self.assertIn('/overlay/', url)
        self.assertTrue(url.startswith('http'))

    def test_the_fields_it_binds_to_are_read_out_of_the_file(self):
        res = self.upload()
        fields = res.json()['data']['overlay']['bound_fields']
        self.assertIn('event.name', fields)
        self.assertIn('event.now_on', fields)

    def test_a_name_the_runtime_cannot_fill_is_warned_about_at_upload(self):
        """Told at upload rather than discovered on air, which is the only
        moment it is cheap to fix."""
        res = self.upload(markup='<html><body>'
                                 '<div data-vent="team.points_for">0</div>'
                                 '</body></html>')
        warnings = ' '.join(res.json()['data']['warnings'])
        self.assertIn('team.points_for', warnings)

    def test_a_file_that_binds_to_nothing_is_warned_about(self):
        """It will show exactly what was drawn and never update, which looks
        like a broken overlay rather than a static one."""
        res = self.upload(markup='<html><body><h1>Hello</h1></body></html>')
        warnings = ' '.join(res.json()['data']['warnings'])
        self.assertIn('data-vent', warnings)

    def test_only_html_is_accepted(self):
        bad = SimpleUploadedFile('overlay.png', b'\x89PNG', content_type='image/png')
        res = self.client.post(self.url(), data={'file': bad}, **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'NOT_HTML')

    # ------------------------------------------- the prompt and templates

    def test_the_designer_prompt_is_on_the_page_and_names_the_fields(self):
        """A designer given "make me an overlay" produces something beautiful
        that binds to nothing, and the fault only appears on air."""
        res = self.client.get(self.url(), **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        prompt = res.json()['data']['prompt']
        self.assertIn('data-vent', prompt)
        self.assertIn('event.name', prompt)
        self.assertIn('event.now_on', prompt)
        # The things an overlay must do to work in OBS at all.
        self.assertIn('transparent', prompt)
        self.assertIn('1920x1080', prompt)

    def test_the_prompt_is_about_events_not_tournaments(self):
        prompt = self.client.get(self.url(), **self.auth).json()['data']['prompt']
        self.assertNotIn('team.', prompt)

    def test_templates_are_offered_to_start_from(self):
        res = self.client.get(self.url(), **self.auth)
        templates = res.json()['data']['templates']
        self.assertTrue(templates)
        keys = {t['key'] for t in templates}
        self.assertIn('now_next', keys)
        for row in templates:
            self.assertTrue(row['name'])
            self.assertTrue(row['detail'])

    def test_the_fields_list_is_sent_so_the_page_keeps_no_copy(self):
        fields = self.client.get(self.url(), **self.auth).json()['data']['fields']
        self.assertIn('event.now_on', fields)
        self.assertIn('sponsors', fields)
        # And the descriptions, so the page does not have to invent them.
        help_rows = self.client.get(
            self.url(), **self.auth).json()['data']['field_help']
        self.assertTrue(all(r['name'] and r['detail'] for r in help_rows))

    # ------------------------------------------------------- who may do it

    def test_a_stranger_cannot_upload_to_somebody_elses_event(self):
        res = self.upload(auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(TournamentOverlay.objects.count(), 0)

    def test_a_signed_out_caller_cannot_upload(self):
        res = self.client.post(self.url(), data={'file': an_overlay()})
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(TournamentOverlay.objects.count(), 0)

    def test_a_stranger_cannot_list_them(self):
        self.upload()
        res = self.client.get(self.url(), **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    # ------------------------------------------------- the address is a key

    def test_rotating_issues_a_new_address(self):
        """OBS opens a browser source with no session and no header, so the
        URL is the credential. If it leaks the only remedy is a new one."""
        overlay_id = self.upload().json()['data']['overlay']['id']
        before = TournamentOverlay.objects.get(pk=overlay_id).token

        res = self.client.post(self.url('%d/rotate/' % overlay_id), **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        after = TournamentOverlay.objects.get(pk=overlay_id).token
        self.assertNotEqual(before, after)

    def test_a_stranger_cannot_rotate_it(self):
        overlay_id = self.upload().json()['data']['overlay']['id']
        res = self.client.post(self.url('%d/rotate/' % overlay_id),
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_removing_one(self):
        overlay_id = self.upload().json()['data']['overlay']['id']
        res = self.client.delete(self.url('%d/' % overlay_id), **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(TournamentOverlay.objects.count(), 0)

    def test_the_overlay_serves_from_its_token(self):
        token = TournamentOverlay.objects.get(
            pk=self.upload().json()['data']['overlay']['id']).token
        res = self.client.get('/overlay/%s/' % token)
        self.assertEqual(res.status_code, 200, res.content[:200])

    # ------------------------------------------------------------- the feed

    def test_the_feed_the_overlay_refreshes_from_actually_answers(self):
        """An endpoint nothing calls in a test is an endpoint nobody has run.

        This one would have raised NameError the first time an event overlay
        refreshed itself - four seconds into being on air - because it used a
        helper that exists in a sibling module and not in its own.
        """
        res = self.client.get('/event/%s/overlay-feed/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertEqual(data['event']['name'], 'Lagos Anime Con')
        for key in ('venue', 'now_on', 'next_on', 'room', 'attending',
                    'tickets_sold'):
            self.assertIn(key, data['event'], key)
        for key in ('programme', 'sponsors', 'version'):
            self.assertIn(key, data, key)

    def test_the_feed_is_public_because_a_browser_source_cannot_sign_in(self):
        res = self.client.get('/event/%s/overlay-feed/' % self.event.slug)
        self.assertEqual(res.status_code, 200)
