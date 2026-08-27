"""Applying, reviewing, and the line between the two.

The property worth protecting: applying grants nothing. Access exists only where
an admin ticked a scope, and taking a partner away takes their keys with it.
"""
import json

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_partners.models import Partner, PartnerApiKey


def signed_in(username='applicant', **extra):
    user = Users.objects.create(
        username=username, email=f'{username}@vent.test',
        login_session_token=f'tok-{username}'[:16],
        login_session_created_at=timezone.now(), is_active=True, **extra,
    )
    # A staff user in these suites exists to call the admin console's
    # endpoints, and those resolve the console's own grant rather than the
    # website session. The two used to be one field, which is why this fixture
    # only ever minted a site token.
    if extra.get('is_staff'):
        user.login_session_token = f'adm-{username}'[:16]
        user.login_session_created_at = timezone.now()
        user.login_session_2fa_at = timezone.now()
        user.save(update_fields=['login_session_token', 'login_session_created_at', 'login_session_2fa_at'])
        return user, {'HTTP_AUTHORIZATION': f'Bearer {user.login_session_token}'}
    return user, {'HTTP_AUTHORIZATION': f'Bearer {user.login_session_token}'}


class ApplicationTests(TestCase):
    def setUp(self):
        self.user, self.auth = signed_in()

    def post(self, path, body, auth=None):
        return self.client.post(
            path, data=json.dumps(body), content_type='application/json',
            **(auth if auth is not None else self.auth),
        )

    def test_the_scope_catalogue_is_readable(self):
        res = self.client.get('/partners/scopes/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('tournaments:read', res.json()['data']['scopes'])

    def test_applying_grants_nothing(self):
        res = self.post('/partners/apply/', {
            'name': 'African Free Fire Community',
            'contact_email': 'partners@afc.test',
            'requested_scopes': ['tournaments:read', 'teams:read'],
        })
        self.assertEqual(res.status_code, 201)
        data = res.json()['data']
        self.assertEqual(data['status'], 'pending')
        self.assertEqual(data['approved_scopes'], [])
        self.assertEqual(data['requested_scopes'], ['tournaments:read', 'teams:read'])

    def test_an_application_needs_a_name_and_an_email(self):
        res = self.post('/partners/apply/', {'name': ''})
        self.assertEqual(res.status_code, 400)

    def test_signing_in_is_required_to_apply(self):
        res = self.client.post(
            '/partners/apply/', data=json.dumps({'name': 'X', 'contact_email': 'x@vent.test'}),
            content_type='application/json',
        )
        self.assertIn(res.status_code, (400, 401))

    def test_you_cannot_apply_twice(self):
        self.post('/partners/apply/', {'name': 'One', 'contact_email': 'a@vent.test'})
        again = self.post('/partners/apply/', {'name': 'Two', 'contact_email': 'b@vent.test'})
        self.assertEqual(again.status_code, 409)

    def test_invented_scopes_are_dropped(self):
        res = self.post('/partners/apply/', {
            'name': 'Scope Test', 'contact_email': 's@vent.test',
            'requested_scopes': ['tournaments:read', 'everything:always', '../../etc/passwd'],
        })
        self.assertEqual(res.json()['data']['requested_scopes'], ['tournaments:read'])

    def test_redirect_uris_must_be_https_or_localhost(self):
        res = self.post('/partners/apply/', {
            'name': 'Redirects', 'contact_email': 'r@vent.test', 'wants_sso': True,
            'redirect_uris': [
                'https://afc.test/callback',
                'http://afc.test/insecure',
                'https://afc.test/callback#fragment',
                'https://*.afc.test/callback',
                'http://localhost:3000/callback',
            ],
        })
        self.assertEqual(
            res.json()['data']['redirect_uris'],
            ['https://afc.test/callback', 'http://localhost:3000/callback'],
        )

    def test_a_partner_without_approval_cannot_issue_a_key(self):
        applied = self.post('/partners/apply/', {'name': 'NoKeys', 'contact_email': 'n@vent.test'})
        pid = applied.json()['data']['id']
        res = self.post(f'/partners/{pid}/keys/', {})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_APPROVED')


class ReviewTests(TestCase):
    def setUp(self):
        self.user, self.auth = signed_in('applicant')
        self.admin, self.admin_auth = signed_in('reviewer', is_staff=True, admin_role='super_admin')
        applied = self.client.post(
            '/partners/apply/',
            data=json.dumps({
                'name': 'AFC', 'contact_email': 'p@afc.test',
                'requested_scopes': ['tournaments:read', 'teams:read', 'players:read'],
            }),
            content_type='application/json', **self.auth,
        )
        self.partner_id = applied.json()['data']['id']

    def review(self, body, auth=None):
        return self.client.post(
            f'/partners/admin/{self.partner_id}/review/', data=json.dumps(body),
            content_type='application/json', **(auth if auth is not None else self.admin_auth),
        )

    def test_only_an_admin_may_review(self):
        """Refused, and the code says which of the two reasons it was.

        The console reads the ordinary session now, so a non-admin's session
        does resolve to a real person - and is then judged not an admin, which
        is a 403. It was a 401 while the console had a grant of its own and an
        ordinary session was not an admin credential at all.
        """
        res = self.review({'decision': 'approved'}, auth=self.auth)
        self.assertIn(res.status_code, (401, 403), res.content)

    def test_an_admin_grants_exactly_the_scopes_they_tick(self):
        res = self.review({'decision': 'approved', 'scopes': ['tournaments:read']})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['approved_scopes'], ['tournaments:read'])

    def test_approving_without_naming_scopes_grants_what_was_asked(self):
        res = self.review({'decision': 'approved'})
        self.assertEqual(
            res.json()['data']['approved_scopes'],
            ['tournaments:read', 'teams:read', 'players:read'],
        )

    def test_the_admin_queue_lists_and_counts(self):
        res = self.client.get('/partners/admin/list/', **self.admin_auth)
        self.assertEqual(res.status_code, 200)
        body = res.json()['data']
        self.assertEqual(body['counts']['pending'], 1)
        self.assertEqual(len(body['partners']), 1)

    def test_an_approved_partner_can_issue_a_key_shown_once(self):
        self.review({'decision': 'approved', 'scopes': ['tournaments:read']})
        res = self.client.post(
            f'/partners/{self.partner_id}/keys/',
            data=json.dumps({'name': 'Live key', 'scopes': ['tournaments:read']}),
            content_type='application/json', **self.auth,
        )
        self.assertEqual(res.status_code, 201)
        secret = res.json()['data']['secret']
        self.assertTrue(secret.startswith('vent_pk_'))

        # and it works
        api = self.client.get('/api/v1/tournaments/', HTTP_AUTHORIZATION=f'Bearer {secret}')
        self.assertEqual(api.status_code, 200)

        # and the secret is not readable afterwards
        mine = self.client.get('/partners/mine/', **self.auth).json()['data']
        stored = json.dumps(mine)
        self.assertNotIn(secret.split('.')[-1], stored)

    def test_suspending_a_partner_revokes_its_keys(self):
        self.review({'decision': 'approved', 'scopes': ['tournaments:read']})
        issued = self.client.post(
            f'/partners/{self.partner_id}/keys/', data=json.dumps({}),
            content_type='application/json', **self.auth,
        )
        secret = issued.json()['data']['secret']
        self.assertEqual(
            self.client.get('/api/v1/tournaments/', HTTP_AUTHORIZATION=f'Bearer {secret}').status_code,
            200,
        )

        self.review({'decision': 'suspended', 'note': 'Abuse'})
        self.assertEqual(
            self.client.get('/api/v1/tournaments/', HTTP_AUTHORIZATION=f'Bearer {secret}').status_code,
            401,
        )

    def test_a_partner_can_revoke_its_own_key(self):
        self.review({'decision': 'approved', 'scopes': ['tournaments:read']})
        issued = self.client.post(
            f'/partners/{self.partner_id}/keys/', data=json.dumps({}),
            content_type='application/json', **self.auth,
        ).json()['data']
        res = self.client.post(
            f"/partners/{self.partner_id}/keys/{issued['key']['id']}/revoke/",
            data=json.dumps({}), content_type='application/json', **self.auth,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            self.client.get(
                '/api/v1/tournaments/', HTTP_AUTHORIZATION=f"Bearer {issued['secret']}",
            ).status_code,
            401,
        )

    def test_somebody_else_cannot_touch_your_partner(self):
        self.review({'decision': 'approved', 'scopes': ['tournaments:read']})
        _, other_auth = signed_in('stranger')
        res = self.client.post(
            f'/partners/{self.partner_id}/keys/', data=json.dumps({}),
            content_type='application/json', **other_auth,
        )
        self.assertEqual(res.status_code, 403)

    def test_five_live_keys_is_the_limit(self):
        self.review({'decision': 'approved', 'scopes': ['tournaments:read']})
        for _ in range(5):
            self.client.post(
                f'/partners/{self.partner_id}/keys/', data=json.dumps({}),
                content_type='application/json', **self.auth,
            )
        sixth = self.client.post(
            f'/partners/{self.partner_id}/keys/', data=json.dumps({}),
            content_type='application/json', **self.auth,
        )
        self.assertEqual(sixth.status_code, 400)
        self.assertEqual(sixth.json()['code'], 'TOO_MANY_KEYS')
