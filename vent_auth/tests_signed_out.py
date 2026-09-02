"""What a signed-out visitor may reach, and what the API refuses them.

CEO, 2 September 2026: "the flow for a non signed in user is bad, they should
not be able to create or join or open anything that requires an account and when
they try it should ask them to create one, they can even see the manage button
for organizations which shouldnt be."

The interface fix lives in `lib/gating.js` and is enforced by
`scripts/check-signed-out.mjs`. This is the other half: the API is what actually
stops anybody, and the controls are the courtesy of not inviting somebody to
fill in a form that cannot submit.

The standing rule stays: **content is public, the action is gated.** A stranger
reads an organisation, a club, a tournament and an event. They cannot join,
manage, post or follow.

## The bug that produced this file

`isMine` on the organisations list read:

    org?.owner?.username === session?.user?.username

Signed out, `org.owner` is a string so `.username` is `undefined`, and the
session side is `undefined`. `undefined === undefined` is true, so every
organisation looked owned and every card offered Manage to a stranger.

That was one of fourteen instances of the same shape across five files, on
organisations, teams and club messages.
"""
from django.test import TestCase

from .models import Organization, Users


class SignedOutReadsTests(TestCase):
    """Public content stays public. This is the half that must NOT regress."""

    def setUp(self):
        self.owner = Users.objects.create(
            username='so_owner', email='so_owner@vent.test', is_active=True)
        self.org = Organization.objects.create(
            org_name='Signed Out Test Org', org_creator=self.owner,
            org_owner=self.owner)

    def test_the_organisation_list_is_readable(self):
        res = self.client.get('/organization/list/')
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_an_organisation_page_is_readable(self):
        ref = self.org.slug or self.org.org_id
        res = self.client.get('/organization/%s/' % ref)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_the_list_tells_a_stranger_they_own_nothing(self):
        """`my_role` must be absent or null, never something truthy.

        The interface reads this to decide whether to offer Manage. If the API
        ever answered with a role for an anonymous caller, every guard on every
        page would be wrong at once.
        """
        res = self.client.get('/organization/list/')
        rows = res.json()['data']['organizations']
        for row in rows:
            self.assertIn(row.get('my_role'), (None, '', 'none'), row.get('name'))


class SignedOutWritesTests(TestCase):
    """Every action refuses an anonymous caller. Not 500, not 200: refused."""

    def setUp(self):
        self.owner = Users.objects.create(
            username='so_owner2', email='so_owner2@vent.test', is_active=True)
        self.org = Organization.objects.create(
            org_name='Signed Out Test Org 2', org_creator=self.owner,
            org_owner=self.owner)
        self.ref = self.org.slug or self.org.org_id

    def refused(self, res):
        """401 or 403. A 500 is a bug and a 200 is a hole."""
        self.assertIn(res.status_code, (401, 403),
                      'expected a refusal, got %s: %s'
                      % (res.status_code, res.content[:200]))

    def test_following_needs_an_account(self):
        self.refused(self.client.post(
            '/organization/%s/follow/' % self.ref, data={},
            content_type='application/json'))

    def test_managing_needs_an_account(self):
        self.refused(self.client.post(
            '/organization/%s/update/' % self.ref, data={'bio': 'hi'},
            content_type='application/json'))

    def test_capabilities_reports_no_permissions_to_a_stranger(self):
        """This one answers 200 on purpose, and that is right.

        `capabilities` is what the interface asks before drawing a control, so
        answering it for everybody lets one code path serve members and
        strangers alike. What matters is not the status code but that every
        permission comes back false: a stranger who is told they may edit is a
        stranger who gets offered the button.
        """
        res = self.client.get('/organization/%s/capabilities/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content[:200])
        me = res.json()['data']['me']
        self.assertFalse(me['is_member'])
        self.assertIsNone(me['role'])
        for key, value in me.items():
            if key.startswith('can_'):
                self.assertFalse(value, key)

    def test_inviting_needs_an_account(self):
        self.refused(self.client.post(
            '/organization/%s/invite/' % self.ref, data={'username': 'x'},
            content_type='application/json'))

    def test_changing_a_role_needs_an_account(self):
        self.refused(self.client.post(
            '/organization/%s/role/' % self.ref,
            data={'username': 'x', 'role': 'admin'},
            content_type='application/json'))

    def test_my_invitations_needs_an_account(self):
        self.refused(self.client.get('/organization/invites/mine/'))

    def test_creating_a_club_needs_an_account(self):
        self.refused(self.client.post(
            '/club/create/', data={'name': 'Anon Club'},
            content_type='application/json'))
