# -*- coding: utf-8 -*-
"""Moving an overlay somebody uploaded, and leaving one alone.

CEO, 4 September 2026, inbox row 50: "should be able to change position even for
the overlays you upload". The Sits control reached V-ENT's own studio graphics
only. What I had told them, that an uploaded file is moved by editing its own
CSS, is true and is not an answer: the person holding the file at a venue is an
operator, not its designer.

The assertion that matters most in this file is the one about a file NOBODY has
moved. An overlay is somebody else's design, pasted into a machine at a venue
and left running for six hours, and the only safe default when there is nothing
to add is to add nothing. If `data-sits` ever appears on an untouched overlay,
every overlay in the world shifts on the next deploy.
"""
import json
import uuid
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users

from .models import Tournament, TournamentOverlay

MARKUP = b'<!doctype html><html><head></head><body><div>Score</div></body></html>'


def a_user(name):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        is_active=True, login_session_token=uuid.uuid4().hex[:16])
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


@override_settings(FRONTEND_URL='https://v-ent.co')
class UploadedOverlayPositionTests(TestCase):

    def setUp(self):
        self.owner, self.auth = a_user('ovowner')
        self.stranger, self.stranger_auth = a_user('ovstranger')
        self.game = Games.objects.create(game_title='EA FC 26 OV %s'
                                                    % uuid.uuid4().hex[:4])
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Overlay Position %s' % uuid.uuid4().hex[:4],
            tournament_game=self.game, tournament_creator=self.owner,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='individual',
            entry_fee='Free', is_draft=False, bracket_type='single_elimination')
        self.ref = self.tournament.slug or self.tournament.tournament_id
        self.overlay = TournamentOverlay.objects.create(
            tournament=self.tournament, name='Score bar',
            token=uuid.uuid4().hex[:40],
            file=SimpleUploadedFile('score.html', MARKUP,
                                    content_type='text/html'))

    def served(self):
        res = self.client.get('/overlay/%s/' % self.overlay.token)
        self.assertEqual(res.status_code, 200, res.content[:200])
        return res.content.decode('utf-8', 'ignore')

    def move(self, options, auth=None):
        return self.client.post(
            '/tournament/%s/overlays/%s/' % (self.ref, self.overlay.id),
            data=json.dumps({'options': options}),
            content_type='application/json', **(auth or self.auth))

    # ------------------------------------------------- the untouched default

    def test_an_overlay_nobody_moved_is_served_with_nothing_added(self):
        """The assertion this whole feature has to keep.

        Checked on the SERVED HTML rather than on the model, because the model
        being empty proves nothing about what the browser receives.
        """
        page = self.served()
        self.assertNotIn('data-sits', page)
        self.assertIn('Score', page)

    def test_and_setting_it_back_to_as_designed_removes_it_again(self):
        self.move({'position': 'top_right'})
        self.assertIn('data-sits', self.served())

        self.move({'position': 'as_designed'})
        self.assertNotIn('data-sits', self.served())

    # ------------------------------------------------------------- moving it

    def test_the_organiser_can_move_it(self):
        res = self.move({'position': 'bottom_centre', 'offset_y': -60})
        self.assertEqual(res.status_code, 200, res.content[:300])

        look = res.json()['data']['overlay']['presentation']
        self.assertEqual(look['position'], 'bottom_centre')
        self.assertEqual(look['offset_y'], -60)

        page = self.served()
        self.assertIn('data-sits', page)
        self.assertIn('bottom_centre', page)

    def test_the_list_says_where_each_one_sits(self):
        self.move({'position': 'top_left'})
        res = self.client.get('/tournament/%s/overlays/' % self.ref, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = res.json()['data']['overlays'][0]
        self.assertEqual(row['presentation']['position'], 'top_left')
        # And what a console may offer, so it keeps no list of its own.
        self.assertIn('positions', row['presentation_options'])

    # ------------------------------------------------------------- refusals

    def test_a_position_that_does_not_exist_is_refused(self):
        res = self.move({'position': 'somewhere_nice'})
        self.assertEqual(res.status_code, 400, res.content[:300])
        self.assertEqual(res.json()['code'], 'INVALID_PRESENTATION')
        self.overlay.refresh_from_db()
        self.assertEqual(self.overlay.options, {})

    def test_a_nudge_beyond_the_limit_is_refused(self):
        res = self.move({'position': 'top_left', 'offset_x': 99999})
        self.assertEqual(res.status_code, 400, res.content[:300])

    def test_somebody_else_cannot_move_it(self):
        res = self.move({'position': 'top_left'}, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content[:300])
        self.overlay.refresh_from_db()
        self.assertEqual(self.overlay.options, {})

    def test_deleting_still_works_on_the_same_address(self):
        """The POST joined an endpoint that already answered DELETE."""
        res = self.client.delete(
            '/tournament/%s/overlays/%s/' % (self.ref, self.overlay.id),
            **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertFalse(TournamentOverlay.objects.filter(
            pk=self.overlay.id).exists())
