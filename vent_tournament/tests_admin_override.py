"""An admin can edit a tournament they do not own, and it is recorded.

"should be able to do what the owner of the event can do and even overwrite them"

An admin could cancel a tournament and disqualify an entrant but could not fix a
typo in its title or correct a wrong start time. If the organiser was
unreachable, the tournament stayed wrong.

The override goes through the organiser's own edit path rather than a parallel
admin endpoint, so these tests also guard against the two drifting: whatever an
owner may edit, an admin may edit.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import AdminAction, Games, Users

from .models import Tournament


def a_user(name, **extra):
    user = Users.objects.create(
        username=f'{name}_{uuid.uuid4().hex[:5]}',
        email=f'{name}_{uuid.uuid4().hex[:5]}@vent.test',
        login_session_token=f'tok-{name}'[:16],
        **extra,
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': f'Bearer {user.login_session_token}'}


class AdminOverrideTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        self.admin, self.admin_auth = a_user('moddy', is_staff=True, admin_role='mod_admin')
        self.finance, self.finance_auth = a_user('fin', is_staff=True, admin_role='finance_admin')
        self.stranger, self.stranger_auth = a_user('nosy')

        game, _ = Games.objects.get_or_create(game_title='EA FC 25')
        now = timezone.now()
        self.t = Tournament.objects.create(
            tournament_title='Typo in teh Title',
            tournament_creator=self.owner, tournament_game=game,
            tournament_type='online', tournament_access='individual',
            tournament_visibility='public', entry_fee='Free', entry_fee_price=0,
            prize_type='no_prize', bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3),
            is_draft=False, status='registration_open',
        )
        self.url = f'/tournament/edit-tournament/{self.t.pk}/'

    def _edit(self, auth, **fields):
        return self.client.put(self.url, fields, content_type='application/json', **auth)

    def test_the_owner_can_still_edit(self):
        res = self._edit(self.owner_auth, tournament_title='Fixed By Owner')

        self.assertEqual(res.status_code, 200)
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_title, 'Fixed By Owner')
        self.assertFalse(res.json()['data']['edited_as_admin'])

    def test_an_admin_can_overrule_the_owner(self):
        res = self._edit(self.admin_auth, tournament_title='Corrected By Admin')

        self.assertEqual(res.status_code, 200, res.content[:200])
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_title, 'Corrected By Admin')
        self.assertTrue(res.json()['data']['edited_as_admin'])

    def test_an_admin_edit_is_written_to_the_audit_log(self):
        """An organiser who finds their start time changed can find out who did it."""
        self._edit(self.admin_auth, tournament_title='Changed',
                   reason='Organiser unreachable before the deadline')

        entry = AdminAction.objects.filter(action_type='edit_tournament').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.admin_id, self.admin.pk)
        self.assertEqual(entry.target_id, str(self.t.pk))
        self.assertIn('tournament_title', entry.metadata['updated_fields'])
        self.assertEqual(entry.metadata['owner_id'], self.owner.pk)
        self.assertIn('unreachable', entry.reason)

    def test_the_owner_editing_their_own_is_not_an_admin_action(self):
        self._edit(self.owner_auth, tournament_title='Mine')

        self.assertFalse(AdminAction.objects.filter(action_type='edit_tournament').exists())

    def test_an_ordinary_user_still_cannot_touch_it(self):
        res = self._edit(self.stranger_auth, tournament_title='Hijacked')

        self.assertEqual(res.status_code, 403)
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_title, 'Typo in teh Title')

    def test_an_admin_without_the_tournament_permission_cannot(self):
        """A finance admin runs payouts, not competitions.

        The override is tied to whoever may already cancel a tournament, so
        there is one answer to "who may touch a tournament they do not own".
        """
        res = self._edit(self.finance_auth, tournament_title='Wrong Desk')

        self.assertEqual(res.status_code, 403)

    def test_an_admin_can_change_the_same_fields_an_owner_can(self):
        """Whatever the owner may edit, the admin may edit - one path, one list."""
        res = self._edit(self.admin_auth,
                         tournament_title='Renamed',
                         tournament_description='Rewritten by an admin',
                         tournament_visibility='private')

        self.assertEqual(res.status_code, 200)
        self.t.refresh_from_db()
        self.assertEqual(self.t.tournament_visibility, 'private')
        self.assertEqual(self.t.tournament_description, 'Rewritten by an admin')

def console_auth(user):
    """Headers for an admin signed into the console but NOT into the site.

    The console session is a different token with a different clock. Blanking
    the site token is the whole point of the test: it reproduces the real case,
    an admin who never signed in to the website at all.
    """
    user.admin_session_token = 'adm-%s' % user.pk
    user.admin_session_created_at = timezone.now()
    user.login_session_token = None
    user.save(update_fields=[
        'admin_session_token', 'admin_session_created_at', 'login_session_token'])
    return {'HTTP_AUTHORIZATION': 'Bearer %s' % user.admin_session_token}


class AdminConsoleSessionTests(TestCase):
    """An admin acting from the console, with no website session at all."""

    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        self.admin, _ = a_user('moddy', is_staff=True, admin_role='mod_admin')
        self.plain, _ = a_user('plain')

        game, _ = Games.objects.get_or_create(game_title='EA FC 25')
        now = timezone.now()
        self.draft = Tournament.objects.create(
            tournament_title='Half Written Draft',
            tournament_creator=self.owner, tournament_game=game,
            tournament_type='online', tournament_access='individual',
            tournament_visibility='public', entry_fee='Free', entry_fee_price=0,
            prize_type='no_prize', bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=3),
            end_date_and_time=now + timedelta(days=4),
            is_draft=True,
        )

    def test_console_token_may_edit_without_a_site_session(self):
        """The exact failure: console session, no site session, edit refused."""
        res = self.client.put(
            '/tournament/edit-tournament/%s/' % self.draft.tournament_id,
            data={'tournament_title': 'Fixed By An Admin'},
            content_type='application/json',
            **console_auth(self.admin))
        self.assertEqual(res.status_code, 200, res.content)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.tournament_title, 'Fixed By An Admin')

    def test_console_token_may_read_a_draft_it_does_not_own(self):
        """Reading has to work too, or the edit form opens blank."""
        res = self.client.get(
            '/tournament/view-tournament/%s/' % self.draft.tournament_id,
            **console_auth(self.admin))
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_draft_stays_hidden_from_everybody_else(self):
        """The console door must not become a hole for ordinary accounts."""
        res = self.client.get(
            '/tournament/view-tournament/%s/' % self.draft.tournament_id,
            HTTP_AUTHORIZATION='Bearer %s' % self.plain.login_session_token)
        self.assertEqual(res.status_code, 404, res.content)

    def test_a_draft_stays_hidden_from_an_anonymous_visitor(self):
        res = self.client.get(
            '/tournament/view-tournament/%s/' % self.draft.tournament_id)
        self.assertEqual(res.status_code, 404, res.content)

    def test_an_expired_console_session_is_refused(self):
        headers = console_auth(self.admin)
        self.admin.admin_session_created_at = timezone.now() - timedelta(days=90)
        self.admin.save(update_fields=['admin_session_created_at'])
        res = self.client.put(
            '/tournament/edit-tournament/%s/' % self.draft.tournament_id,
            data={'tournament_title': 'Should Not Land'},
            content_type='application/json', **headers)
        self.assertEqual(res.status_code, 401, res.content)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.tournament_title, 'Half Written Draft')

    def test_the_two_sessions_stay_independent(self):
        """PR #39's mistake, guarded: one token must never be the other."""
        headers = console_auth(self.admin)
        self.admin.refresh_from_db()
        self.assertIsNotNone(self.admin.admin_session_token)
        self.assertNotEqual(
            self.admin.admin_session_token, self.admin.login_session_token)
        self.assertEqual(headers['HTTP_AUTHORIZATION'],
                         'Bearer %s' % self.admin.admin_session_token)
