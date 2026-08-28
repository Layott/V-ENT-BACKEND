"""The base tier grants itself, and the key sends itself.

The CEO, twice:

  "requesting for company or registeration data is too stressful cause we cant
   chcek everyone... let's set like a base minimum that anybody can access"

  "instead having to do these for each person that comes, it should have
   automatically sent once approved"

Everything in the base tier is already readable by anybody with a browser, so
asking for a registration number before handing it over protects nothing. What
stays reviewed is the part where the answer differs per partner: participants
and brackets are about identifiable people, and SSO hands over identity.
"""
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from .models import Partner, PartnerApiKey, REVIEWED_SCOPES, SELF_SERVE_SCOPES


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('t-%s' % name)[:16], **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class SelfServeTests(TestCase):
    def setUp(self):
        self.user, self.auth = a_user('builder')

    def apply(self, **extra):
        body = {'name': 'A Fan Site', 'contact_email': 'dev@fansite.test'}
        body.update(extra)
        return self.client.post('/partners/apply/', data=body,
                                content_type='application/json', **self.auth)

    def test_the_base_tier_needs_nothing_but_a_name_and_an_address(self):
        res = self.apply(requested_scopes=['tournaments:read', 'rankings:read'])
        self.assertEqual(res.status_code, 201, res.content)
        partner = Partner.objects.get(owner=self.user)
        self.assertEqual(partner.status, 'approved')
        self.assertIn('tournaments:read', partner.approved_scopes)

    def test_a_key_is_issued_and_emailed_without_anybody_pressing_anything(self):
        mail.outbox = []
        self.apply(requested_scopes=['tournaments:read'])
        partner = Partner.objects.get(owner=self.user)
        self.assertEqual(partner.api_keys.filter(revoked_at__isnull=True).count(), 1)
        self.assertTrue(any('API key' in m.subject for m in mail.outbox),
                        [m.subject for m in mail.outbox])

    def test_asking_for_more_keeps_the_application_open(self):
        """And the base tier works while a person looks at the rest."""
        res = self.apply(requested_scopes=[
            'tournaments:read', 'tournaments:brackets:read'])
        self.assertEqual(res.status_code, 201, res.content)
        partner = Partner.objects.get(owner=self.user)
        self.assertEqual(partner.status, 'pending')
        self.assertIn('tournaments:read', partner.approved_scopes)
        self.assertNotIn('tournaments:brackets:read', partner.approved_scopes)
        # And it can already read, while it waits.
        self.assertTrue(partner.api_keys.filter(revoked_at__isnull=True).exists())

    def test_sso_always_keeps_it_open(self):
        """Signing people in hands over identity; that is never automatic."""
        self.apply(requested_scopes=['tournaments:read'], wants_sso=True)
        partner = Partner.objects.get(owner=self.user)
        self.assertEqual(partner.status, 'pending')
        self.assertEqual(partner.sso_status, 'requested')

    def test_nothing_identifiable_is_in_the_base_tier(self):
        """The line: listings grant themselves, people do not."""
        self.assertIn('tournaments:participants:read', REVIEWED_SCOPES)
        self.assertIn('tournaments:brackets:read', REVIEWED_SCOPES)
        self.assertIn('players:stats:read', REVIEWED_SCOPES)
        self.assertNotIn('tournaments:participants:read', SELF_SERVE_SCOPES)

    def test_a_reviewed_scope_cannot_be_smuggled_in(self):
        self.apply(requested_scopes=['tournaments:participants:read'])
        partner = Partner.objects.get(owner=self.user)
        self.assertEqual(partner.approved_scopes, [])
        self.assertEqual(partner.status, 'pending')

    def test_the_key_carries_only_the_granted_scopes(self):
        self.apply(requested_scopes=['tournaments:read', 'tournaments:brackets:read'])
        key = Partner.objects.get(owner=self.user).api_keys.first()
        self.assertEqual(key.scopes, ['tournaments:read'])


class ApprovalSendsCredentialsTests(TestCase):
    def setUp(self):
        self.admin, self.auth = a_user('cred_admin', is_staff=True,
                                       admin_role='super_admin')
        owner, _ = a_user('cred_owner')
        self.partner = Partner.objects.create(
            name='Reviewed Partner', owner=owner, status='pending',
            contact_email='them@partner.test',
            requested_scopes=['tournaments:brackets:read'],
        )

    def test_approving_issues_a_key_and_emails_it(self):
        """"it should have automatically sent once approved"."""
        mail.outbox = []
        res = self.client.post(
            '/partners/admin/%s/review/' % self.partner.pk,
            data={'decision': 'approved', 'scopes': ['tournaments:brackets:read']},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(
            self.partner.api_keys.filter(revoked_at__isnull=True).count(), 1)
        self.assertTrue(any('API key' in m.subject for m in mail.outbox),
                        [m.subject for m in mail.outbox])

    def test_approving_a_partner_that_already_has_a_key_issues_no_second_one(self):
        PartnerApiKey.issue(self.partner, name='Existing',
                            scopes=['tournaments:read'], created_by=self.admin)
        self.client.post(
            '/partners/admin/%s/review/' % self.partner.pk,
            data={'decision': 'approved', 'scopes': ['tournaments:read']},
            content_type='application/json', **self.auth)
        self.assertEqual(
            self.partner.api_keys.filter(revoked_at__isnull=True).count(), 1)
