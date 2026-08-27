"""Turning a partner off, changing what it may read, and replacing its key.

Enabling and disabling already worked through `admin_review`. These are the two
things that did not: changing the grant without it looking like a re-approval,
and rotating a key from our side rather than waiting for somebody at the partner
to do it.

The interesting cases are all about what happens to keys that already exist,
because a key issued last month is the thing that carries yesterday's grant.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import AdminAction, Users

from .models import Partner, PartnerApiKey


def an_admin(role='super_admin'):
    token = 'adm-%s' % uuid.uuid4().hex[:10]
    user = Users.objects.create(
        username='adm_%s' % uuid.uuid4().hex[:6],
        email='adm_%s@vent.test' % uuid.uuid4().hex[:6],
        is_staff=True, admin_role=role, login_session_token=token,
    )
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save(update_fields=['login_session_created_at', 'login_session_2fa_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % token}


class PartnerControlTests(TestCase):
    def setUp(self):
        self.admin, self.auth = an_admin()
        self.owner = Users.objects.create(
            username='partner_owner', email='owner@partner.test')
        self.partner = Partner.objects.create(
            name='African Free Fire Community',
            owner=self.owner,
            status='approved',
            requested_scopes=['tournaments:read', 'teams:read', 'players:read'],
            approved_scopes=['tournaments:read', 'teams:read', 'players:read'],
        )
        self.key, self.secret = PartnerApiKey.issue(
            self.partner, name='Live key',
            scopes=['tournaments:read', 'teams:read'], created_by=self.owner)

    # ------------------------------------------------------------- scopes

    def test_an_admin_can_take_a_scope_away(self):
        res = self.client.post(
            '/partners/admin/%s/scopes/' % self.partner.pk,
            data={'scopes': ['tournaments:read']},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.approved_scopes, ['tournaments:read'])

    def test_live_keys_are_trimmed_to_match(self):
        """A key still claiming a scope the partner lost is a lie in every log."""
        self.client.post(
            '/partners/admin/%s/scopes/' % self.partner.pk,
            data={'scopes': ['tournaments:read']},
            content_type='application/json', **self.auth)
        self.key.refresh_from_db()
        self.assertEqual(self.key.scopes, ['tournaments:read'])

    def test_changing_scopes_is_not_recorded_as_a_review(self):
        """It writes its own audit line and leaves the approval history alone."""
        before = self.partner.reviewed_at
        self.client.post(
            '/partners/admin/%s/scopes/' % self.partner.pk,
            data={'scopes': ['teams:read'], 'reason': 'Asked to drop tournaments'},
            content_type='application/json', **self.auth)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.reviewed_at, before)
        self.assertTrue(AdminAction.objects.filter(
            action_type='set_partner_scopes', target_id=str(self.partner.pk)).exists())

    def test_an_unknown_scope_is_dropped_rather_than_stored(self):
        self.client.post(
            '/partners/admin/%s/scopes/' % self.partner.pk,
            data={'scopes': ['tournaments:read', 'wallet:read', 'nonsense']},
            content_type='application/json', **self.auth)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.approved_scopes, ['tournaments:read'])

    # ------------------------------------------------------------ rotation

    def test_rotating_kills_the_old_key_and_issues_a_new_one(self):
        res = self.client.post(
            '/partners/admin/%s/keys/%s/rotate/' % (self.partner.pk, self.key.pk),
            data={'reason': 'Leaked in a public repo'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content)

        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.revoked_at)

        body = res.json()['data']
        self.assertTrue(body['secret'])
        self.assertEqual(body['revoked']['key_id'], self.key.key_id)
        self.assertEqual(
            self.partner.api_keys.filter(revoked_at__isnull=True).count(), 1)

    def test_the_replacement_carries_the_same_scopes(self):
        self.client.post(
            '/partners/admin/%s/keys/%s/rotate/' % (self.partner.pk, self.key.pk),
            content_type='application/json', **self.auth)
        new = self.partner.api_keys.filter(revoked_at__isnull=True).first()
        self.assertEqual(new.scopes, ['tournaments:read', 'teams:read'])
        self.assertEqual(new.name, 'Live key')

    def test_the_replacement_drops_a_scope_the_partner_has_since_lost(self):
        self.partner.approved_scopes = ['tournaments:read']
        self.partner.save(update_fields=['approved_scopes'])
        self.client.post(
            '/partners/admin/%s/keys/%s/rotate/' % (self.partner.pk, self.key.pk),
            content_type='application/json', **self.auth)
        new = self.partner.api_keys.filter(revoked_at__isnull=True).first()
        self.assertEqual(new.scopes, ['tournaments:read'])

    def test_an_already_revoked_key_cannot_be_rotated(self):
        self.key.revoked_at = timezone.now()
        self.key.save(update_fields=['revoked_at'])
        res = self.client.post(
            '/partners/admin/%s/keys/%s/rotate/' % (self.partner.pk, self.key.pk),
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 404, res.content)

    def test_a_suspended_partner_cannot_be_given_a_fresh_key(self):
        """Suspension is meant to stop the traffic, not pause it."""
        self.partner.status = 'suspended'
        self.partner.save(update_fields=['status'])
        res = self.client.post(
            '/partners/admin/%s/keys/%s/rotate/' % (self.partner.pk, self.key.pk),
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_rotation_is_written_to_the_audit_log(self):
        self.client.post(
            '/partners/admin/%s/keys/%s/rotate/' % (self.partner.pk, self.key.pk),
            content_type='application/json', **self.auth)
        self.assertTrue(AdminAction.objects.filter(
            action_type='rotate_partner_key').exists())

    # ------------------------------------------------------------- refusal

    def test_a_signed_out_caller_is_refused(self):
        res = self.client.post(
            '/partners/admin/%s/scopes/' % self.partner.pk,
            data={'scopes': []}, content_type='application/json')
        self.assertIn(res.status_code, (400, 401), res.content)

    def test_suspending_still_revokes_every_key(self):
        """The control that already existed, guarded so it stays working."""
        res = self.client.post(
            '/partners/admin/%s/review/' % self.partner.pk,
            data={'decision': 'suspended', 'note': 'Abuse report'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(
            self.partner.api_keys.filter(revoked_at__isnull=True).count(), 0)


class IssueKeyTests(TestCase):
    """Rotation could only replace a key that already existed, and the live
    partner had none - nothing had ever issued one, because issuing was
    self-service and the partner had not done it. The console offered nothing to
    rotate and no way to get there."""

    def setUp(self):
        self.admin, self.auth = an_admin()
        owner = Users.objects.create(username='afc_owner', email='afc@partner.test')
        self.partner = Partner.objects.create(
            name='African Free Fire Community', owner=owner, status='approved',
            requested_scopes=['tournaments:read'], approved_scopes=['tournaments:read'],
        )

    def test_an_admin_can_issue_the_first_key(self):
        self.assertEqual(self.partner.api_keys.count(), 0)
        res = self.client.post('/partners/admin/%s/keys/' % self.partner.pk,
                               data={'name': 'Live key'},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(res.json()['data']['secret'])
        self.assertEqual(
            self.partner.api_keys.filter(revoked_at__isnull=True).count(), 1)

    def test_the_key_carries_only_what_the_partner_holds(self):
        res = self.client.post('/partners/admin/%s/keys/' % self.partner.pk,
                               data={'scopes': ['tournaments:read', 'teams:read']},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.json()['data']['key']['scopes'], ['tournaments:read'])

    def test_a_partner_with_no_scopes_gets_no_key(self):
        self.partner.approved_scopes = []
        self.partner.save(update_fields=['approved_scopes'])
        res = self.client.post('/partners/admin/%s/keys/' % self.partner.pk,
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'NO_SCOPES')

    def test_a_suspended_partner_gets_no_key(self):
        self.partner.status = 'suspended'
        self.partner.save(update_fields=['status'])
        res = self.client.post('/partners/admin/%s/keys/' % self.partner.pk,
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_issuing_is_written_to_the_audit_log(self):
        self.client.post('/partners/admin/%s/keys/' % self.partner.pk,
                         content_type='application/json', **self.auth)
        self.assertTrue(AdminAction.objects.filter(
            action_type='issue_partner_key').exists())

    def test_five_live_keys_is_still_the_limit(self):
        for i in range(5):
            self.client.post('/partners/admin/%s/keys/' % self.partner.pk,
                             data={'name': 'k%s' % i},
                             content_type='application/json', **self.auth)
        res = self.client.post('/partners/admin/%s/keys/' % self.partner.pk,
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'TOO_MANY_KEYS')
