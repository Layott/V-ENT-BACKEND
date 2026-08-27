"""What a visitor may administer, answered for the website session.

An admin reading a tournament page is signed in with a website session, not the
console one, so the page knows only `is_staff` - "some kind of staff" and
nothing about what they may do. Without this the inline admin controls would
have to appear for every staff account and fail on submit for most of them.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from .models import Users


def a_user(name, **extra):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tok-%s' % name)[:16],
        **extra,
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class MyAdminCapabilitiesTests(TestCase):
    url = '/auth/me/admin/'

    def test_a_signed_out_visitor_gets_an_answer_not_an_error(self):
        """Every page asks this. A page is not an error for being read."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()['data']['is_admin'])

    def test_an_ordinary_member_is_not_an_admin(self):
        _user, auth = a_user('player')
        response = self.client.get(self.url, **auth)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()['data']['is_admin'])
        self.assertEqual(response.json()['data']['permissions'], {})

    def test_a_moderator_gets_the_permissions_their_role_holds(self):
        _user, auth = a_user('moddy', is_staff=True, admin_role='mod_admin')
        data = self.client.get(self.url, **auth).json()['data']
        self.assertTrue(data['is_admin'])
        self.assertEqual(data['role'], 'mod_admin')
        self.assertTrue(data['permissions']['cancel_tournament'])
        self.assertTrue(data['permissions']['manage_events'])

    def test_a_moderator_does_not_hold_the_ones_they_do_not(self):
        """The point of asking: the page hides what this admin cannot do."""
        _user, auth = a_user('moddy2', is_staff=True, admin_role='mod_admin')
        perms = self.client.get(self.url, **auth).json()['data']['permissions']
        self.assertFalse(perms['manage_admins'])
        self.assertFalse(perms['approve_payouts'])

    def test_a_finance_admin_cannot_manage_events(self):
        _user, auth = a_user('fin', is_staff=True, admin_role='finance_admin')
        perms = self.client.get(self.url, **auth).json()['data']['permissions']
        self.assertFalse(perms['manage_events'])
        self.assertTrue(perms['approve_payouts'])

    def test_a_super_admin_holds_everything_asked_about(self):
        _user, auth = a_user('boss', is_staff=True, admin_role='super_admin')
        data = self.client.get(self.url, **auth).json()['data']
        self.assertTrue(data['permissions']['manage_admins'])
        self.assertTrue(data['permissions']['cancel_tournament'])
        self.assertTrue(data['permissions']['manage_events'])
