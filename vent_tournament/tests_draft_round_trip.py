"""A draft, saved and re-opened, comes back whole.

CEO, 29 August 2026: "each time I save as draft, it creates a new draft" and
"the registration start time end time, banner, logo, connected event, game
edition and some other things, don't get saved and I have to renter it again."

Two faults, and the first caused most of the second. The wizard always POSTed
to create, so a second save made a second tournament - and the new row had none
of the images the first one carried, which is why they looked like they had not
saved. Underneath that, the registration window had no columns at all: the
wizard has sent `reg_start_date_and_time` since it was written and the server
read it and dropped it.

These tests are the round trip: save it, read it back, and check the values are
the ones that went in. A field that is written but never returned fails here,
which is the shape of the bug that survived longest.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import GameSeries, Games, Users, UserWallet

from .models import Tournament


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('d-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(user_wallet_id='w%09d' % user.user_id, user=user,
                              wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class DraftRoundTripTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('dr_org')
        self.game = Games.objects.create(game_title='EA FC DR')
        self.series = GameSeries.objects.create(game=self.game, name='2026')
        now = timezone.now()
        self.opens = (now + timedelta(days=1)).replace(microsecond=0)
        self.closes = (now + timedelta(days=5)).replace(microsecond=0)
        self.payload = {
            'tournament_title': 'Draft Probe',
            'game': 'EA FC DR',
            'tournament_description': 'probe',
            'tournament_type': 'online',
            'start_date_and_time': (now + timedelta(days=7)).isoformat(),
            'end_date_and_time': (now + timedelta(days=8)).isoformat(),
            'reg_start_date_and_time': self.opens.isoformat(),
            'reg_end_date_and_time': self.closes.isoformat(),
            'series_id': self.series.series_id,
            'bracket_type': 'round_robin',
            'entry_type': 'Free',
            'tournament_visibility': 'public',
            'tournament_access': 'team',
            'team_size': 2,
            'is_draft': '1',
        }

    def create(self, **overrides):
        body = dict(self.payload, **overrides)
        return self.client.post('/tournament/create-tournament/', body,
                                **self.auth)

    def view(self, tournament_id, auth=None):
        # Authenticated: a draft is not public, and the wizard loads it as its
        # owner. An unauthenticated read is a 404 by design.
        return self.client.get('/tournament/view-tournament/%s/' % tournament_id,
                               **(auth or self.auth))

    # ------------------------------------------------- the registration window

    def test_the_registration_window_is_stored(self):
        res = self.create()
        self.assertIn(res.status_code, (200, 201), res.data)
        t = Tournament.objects.get(tournament_title='Draft Probe')
        self.assertIsNotNone(t.registration_opens_at)
        self.assertIsNotNone(t.registration_closes_at)

    def test_the_registration_window_comes_back(self):
        # Written but never returned is the same as lost, from the wizard's
        # side: re-opening the draft shows an empty field either way.
        self.create()
        t = Tournament.objects.get(tournament_title='Draft Probe')
        res = self.view(t.pk)
        self.assertEqual(res.status_code, 200, res.data)
        data = res.data['data']
        payload = data.get('tournament') or data
        self.assertTrue(payload.get('reg_start_date_and_time'))
        self.assertTrue(payload.get('reg_end_date_and_time'))

    def test_the_owner_can_open_their_own_draft(self):
        # The wizard loads the draft through this endpoint. If the owner cannot
        # read it, re-opening a draft shows an empty form, which looks exactly
        # like the fields not having saved.
        self.create()
        t = Tournament.objects.get(tournament_title='Draft Probe')
        self.assertEqual(self.view(t.pk).status_code, 200)

    def test_a_draft_is_not_public(self):
        self.create()
        t = Tournament.objects.get(tournament_title='Draft Probe')
        res = self.client.get('/tournament/view-tournament/%s/' % t.pk)
        self.assertEqual(res.status_code, 404)

    def test_a_tournament_with_no_window_is_allowed(self):
        res = self.create(reg_start_date_and_time='', reg_end_date_and_time='')
        self.assertIn(res.status_code, (200, 201), res.data)
        t = Tournament.objects.get(tournament_title='Draft Probe')
        self.assertIsNone(t.registration_opens_at)

    # ------------------------------------------------------------ the edition

    def test_the_edition_is_stored_and_returned(self):
        self.create()
        t = Tournament.objects.get(tournament_title='Draft Probe')
        self.assertEqual(t.tournament_series_id, self.series.series_id)
        res = self.view(t.pk)
        self.assertEqual(res.status_code, 200, res.data)
        data = res.data['data']
        payload = data.get('tournament') or data
        self.assertEqual(payload.get('series_id'), self.series.series_id)

    # ------------------------------------------- saving twice is one tournament

    def test_editing_a_draft_does_not_make_a_second_one(self):
        # The whole of the CEO's first report. The wizard now PUTs to edit when
        # it was opened from a draft; this is the server half of that.
        self.create()
        t = Tournament.objects.get(tournament_title='Draft Probe')
        res = self.client.put(
            '/tournament/edit-tournament/%s/' % t.pk,
            {'tournament_title': 'Draft Probe'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            Tournament.objects.filter(tournament_title='Draft Probe').count(), 1)

    def test_editing_saves_the_registration_window(self):
        self.create()
        t = Tournament.objects.get(tournament_title='Draft Probe')
        later = (timezone.now() + timedelta(days=9)).replace(microsecond=0)
        res = self.client.put(
            '/tournament/edit-tournament/%s/' % t.pk,
            {'reg_end_date_and_time': later.isoformat()},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.data)
        t.refresh_from_db()
        # `update_fields` is passed on the save, so a column set but not listed
        # would be dropped silently. This is what catches that.
        self.assertEqual(t.registration_closes_at.date(), later.date())

    def test_editing_saves_the_edition(self):
        self.create(series_id='')
        t = Tournament.objects.get(tournament_title='Draft Probe')
        self.assertIsNone(t.tournament_series_id)
        res = self.client.put(
            '/tournament/edit-tournament/%s/' % t.pk,
            {'series_id': self.series.series_id},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.data)
        t.refresh_from_db()
        self.assertEqual(t.tournament_series_id, self.series.series_id)

    def test_the_edition_can_be_cleared(self):
        self.create()
        t = Tournament.objects.get(tournament_title='Draft Probe')
        self.client.put('/tournament/edit-tournament/%s/' % t.pk,
                        {'series_id': ''}, content_type='application/json',
                        **self.auth)
        t.refresh_from_db()
        self.assertIsNone(t.tournament_series_id)

    def test_a_stranger_cannot_edit_the_draft(self):
        self.create()
        t = Tournament.objects.get(tournament_title='Draft Probe')
        _other, other_auth = a_user('dr_other')
        res = self.client.put(
            '/tournament/edit-tournament/%s/' % t.pk,
            {'tournament_title': 'Stolen'},
            content_type='application/json', **other_auth)
        self.assertEqual(res.status_code, 403)
