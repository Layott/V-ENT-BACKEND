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


class RedirectAddressTests(TestCase):
    """Where a partner may send somebody after they sign in.

    Only the partner's own owner could edit these. AFC could not add their
    sign-in callback - a different path from the connect one - so BAD_REDIRECT
    was the live answer to every attempt to sign in with V-ENT, and the person
    able to unblock it had no control for it.
    """

    CONNECT = 'https://api.africanfreefirecommunity.com/auth/vent/callback/'
    SIGN_IN = 'https://api.africanfreefirecommunity.com/auth/vent/sso/callback/'

    def setUp(self):
        self.admin, self.auth = an_admin()
        self.owner = Users.objects.create(
            username='afc_owner', email='afc@partner.test')
        self.partner = Partner.objects.create(
            name='African Free Fire Community', owner=self.owner,
            status='approved', sso_status='approved',
            redirect_uris=[self.CONNECT],
        )

    def url(self):
        return '/partners/admin/%s/redirects/' % self.partner.pk

    def post(self, body, auth=None):
        return self.client.post(self.url(), data=body,
                                content_type='application/json',
                                **(auth if auth is not None else self.auth))

    def test_adding_one_keeps_the_one_already_there(self):
        """The whole point. A partner with a working connect callback must not
        lose it when a sign-in callback is added."""
        res = self.post({'add': self.SIGN_IN})
        self.assertEqual(res.status_code, 200, res.content)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.redirect_uris, [self.CONNECT, self.SIGN_IN])

    def test_the_new_address_is_then_accepted_by_the_consent_screen(self):
        """The refusal being fixed is the only thing that matters here."""
        self.partner.issue_sso_credentials()
        before = self.client.get('/partners/sso/authorize-info/', {
            'client_id': self.partner.sso_client_id,
            'redirect_uri': self.SIGN_IN, 'scope': 'identity'})
        self.assertEqual(before.json()['code'], 'BAD_REDIRECT')

        self.post({'add': self.SIGN_IN})

        after = self.client.get('/partners/sso/authorize-info/', {
            'client_id': self.partner.sso_client_id,
            'redirect_uri': self.SIGN_IN, 'scope': 'identity'})
        self.assertEqual(after.status_code, 200, after.content)

    def test_adding_the_same_one_twice_does_not_duplicate_it(self):
        self.post({'add': self.SIGN_IN})
        res = self.post({'add': self.SIGN_IN})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'NO_CHANGE')
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.redirect_uris.count(self.SIGN_IN), 1)

    def test_an_address_that_is_not_usable_is_refused_and_nothing_changes(self):
        res = self.post({'add': 'http://afc.test/callback'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'BAD_REDIRECT')
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.redirect_uris, [self.CONNECT])

    def test_a_wildcard_is_refused(self):
        """An open redirect is how a sign-in provider leaks an identity."""
        res = self.post({'add': 'https://*.afc.test/callback'})
        self.assertEqual(res.status_code, 400, res.content)

    def test_removing_one_leaves_the_rest(self):
        self.post({'add': self.SIGN_IN})
        self.post({'remove': self.CONNECT})
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.redirect_uris, [self.SIGN_IN])

    def test_sending_the_whole_list_replaces_it(self):
        res = self.post({'redirect_uris': [self.SIGN_IN]})
        self.assertEqual(res.status_code, 200, res.content)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.redirect_uris, [self.SIGN_IN])

    def test_every_change_is_written_to_the_audit_log(self):
        """"Who added that address" is the question that gets asked."""
        self.post({'add': self.SIGN_IN, 'reason': 'AFC sign-in integration'})
        entry = AdminAction.objects.get(action_type='set_partner_redirects')
        self.assertEqual(entry.admin_id, self.admin.user_id)
        self.assertEqual(entry.metadata['before'], [self.CONNECT])
        self.assertIn(self.SIGN_IN, entry.metadata['after'])
        self.assertIn('AFC', entry.reason)

    def test_a_stranger_cannot_add_an_address(self):
        stranger = Users.objects.create(
            username='not_an_admin', email='no@vent.test',
            login_session_token='strangertoken12')
        stranger.login_session_created_at = timezone.now()
        stranger.save()
        res = self.post({'add': self.SIGN_IN}, auth={
            'HTTP_AUTHORIZATION': 'Bearer strangertoken12'})
        self.assertIn(res.status_code, (401, 403), res.content)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.redirect_uris, [self.CONNECT])

    def test_ten_is_the_ceiling(self):
        res = self.post({'redirect_uris': [
            'https://afc.test/cb%s' % i for i in range(11)]})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'TOO_MANY')


class VerificationEndpointTests(TestCase):
    """Where we ask a partner to confirm one of their own usernames.

    Narrower than the other partner controls: this one stores somebody else's
    credential and points our server at an address it will call.
    """

    def setUp(self):
        self.admin, self.auth = an_admin()
        owner = Users.objects.create(username='ver_owner', email='v@partner.test')
        self.partner = Partner.objects.create(
            name='AFC', slug='afc-verify', owner=owner, status='approved')

    def url(self):
        return '/partners/admin/%s/verification/' % self.partner.pk

    def post(self, body, auth=None):
        return self.client.post(self.url(), data=body,
                                content_type='application/json',
                                **(auth if auth is not None else self.auth))

    def test_a_super_admin_sets_the_address_and_the_secret(self):
        res = self.post({'verification_url': 'https://afc.test/verify/',
                         'verification_secret': 'their-secret'})
        self.assertEqual(res.status_code, 200, res.content)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.verification_url, 'https://afc.test/verify/')
        self.assertEqual(self.partner.verification_secret, 'their-secret')

    def test_the_secret_is_never_read_back(self):
        """It is theirs. The console shows only that one is held."""
        self.post({'verification_secret': 'their-secret'})
        row = self.post({'verification_url': 'https://afc.test/verify/'}).json()['data']
        self.assertNotIn('verification_secret', row)
        self.assertTrue(row['has_verification_secret'])

    def test_plain_http_is_refused(self):
        """The call carries a credential and an identifier."""
        res = self.post({'verification_url': 'http://afc.test/verify/'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'BAD_URL')

    def test_clearing_the_address_turns_the_check_off_without_breaking_anything(self):
        self.post({'verification_url': 'https://afc.test/verify/'})
        res = self.post({'verification_url': ''})
        self.assertEqual(res.status_code, 200, res.content)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.verification_url, '')

    def test_a_lesser_admin_cannot_set_it(self):
        _lesser, lesser_auth = an_admin(role='support')
        res = self.post({'verification_url': 'https://evil.test/'}, auth=lesser_auth)
        self.assertEqual(res.status_code, 403, res.content)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.verification_url, '')

    def test_the_secret_never_reaches_the_audit_log(self):
        """Only that it changed."""
        self.post({'verification_secret': 'do-not-log-me',
                   'verification_url': 'https://afc.test/verify/'})
        entry = AdminAction.objects.get(action_type='set_partner_verification')
        self.assertNotIn('do-not-log-me', str(entry.metadata))
        self.assertTrue(entry.metadata['secret_changed'])
