"""An organisation addressed by its name, and pictures that survive the upload.

CEO, 29 August 2026: "creating an organization the logos and banners uploaded
did not work, clicking on any of the sub pages did not load anything, clicking
on manage the organization too, shows: Organization not found. including all
subpages."

Three faults, and the network log named all of them:

    /organization/walk-test-org/           200
    /organization/walk-test-org/members/   404
    /organization/walk-test-org/teams/     404
    /organization/linkable-teams/          404

1. When organisations learned slugs, `org_detail` was changed to take a
   `<str:org_id>` and every sub-resource was left as `<int:org_id>`. A slug
   never matches `int`, so Django answered 404 before the view ran: the header
   loaded and every panel under it failed.
2. `organization/linkable-teams/` was declared after `organization/<str:org_id>/`.
   Django matches in order and `<str:...>` takes one segment, so the literal
   route was unreachable.
3. `org_create` never read `logo` or `banner` out of the request.
"""
import io
import json
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from .models import Organization, Users


def a_user(name):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def a_png():
    """The smallest valid PNG, so ImageField's verification passes."""
    raw = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00'
           b'\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc'
           b'\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
    return raw


class OrgBySlugTests(TestCase):
    def setUp(self):
        self.owner, self.auth = a_user('orgowner')
        res = self.client.post(
            '/organization/create/',
            data=json.dumps({'name': 'Walk Test Org %s' % uuid.uuid4().hex[:4],
                             'tag': 'WTO'}),
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.org = Organization.objects.get()

    def test_the_organisation_has_a_slug(self):
        self.assertTrue(self.org.slug, 'no slug, so it has no address')

    # Every sub-resource the page actually loads. Named one by one rather than
    # looped, so a failure says which panel is broken.
    def test_members_resolve_by_slug(self):
        res = self.client.get('/organization/%s/members/' % self.org.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_teams_resolve_by_slug(self):
        res = self.client.get('/organization/%s/teams/' % self.org.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_requests_resolve_by_slug(self):
        res = self.client.get('/organization/%s/requests/' % self.org.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_tournaments_resolve_by_slug(self):
        res = self.client.get('/organization/%s/tournaments/' % self.org.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_events_resolve_by_slug(self):
        res = self.client.get('/organization/%s/events/' % self.org.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_activity_resolves_by_slug(self):
        res = self.client.get('/organization/%s/activity/' % self.org.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_a_numeric_id_still_works(self):
        """Links already shared have to keep working."""
        res = self.client.get('/organization/%s/members/' % self.org.org_id, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_linkable_teams_is_not_shadowed_by_the_catch_all(self):
        """`<str:org_id>` matches one segment, so a literal route declared
        after it is unreachable. This one answered 404 forever."""
        res = self.client.get('/organization/linkable-teams/', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_an_organisation_that_does_not_exist_still_says_so(self):
        res = self.client.get('/organization/no-such-org/members/', **self.auth)
        self.assertEqual(res.status_code, 404)


class OrgUploadTests(TestCase):
    def test_the_logo_and_banner_survive_creation(self):
        """They were read from nowhere: the form uploaded them and the
        endpoint built the row without them, so the organisation came out
        blank and nothing was reported as wrong."""
        owner, auth = a_user('uploader')
        res = self.client.post(
            '/organization/create/',
            data={
                'name': 'Picture Org %s' % uuid.uuid4().hex[:4],
                'logo': SimpleUploadedFile('logo.png', a_png(), content_type='image/png'),
                'banner': SimpleUploadedFile('banner.png', a_png(), content_type='image/png'),
            },
            **auth)
        self.assertEqual(res.status_code, 201, res.content[:300])

        org = Organization.objects.get()
        self.assertTrue(org.logo, 'the logo was dropped')
        self.assertTrue(org.banner, 'the banner was dropped')

    def test_creating_without_pictures_still_works(self):
        owner, auth = a_user('nopics')
        res = self.client.post(
            '/organization/create/',
            data=json.dumps({'name': 'Plain Org %s' % uuid.uuid4().hex[:4]}),
            content_type='application/json', **auth)
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertFalse(Organization.objects.get().logo)


class OrgMemberBadgeTests(TestCase):
    """The founder mark travels with the person, into the member table too.

    It was missing there because the org views built their own person dict by
    hand instead of going through the one builder every other surface uses.
    The row links to the profile and then shows a name with no badge, which is
    exactly the inconsistency the CEO reported on direct messages.
    """

    def test_a_member_row_carries_the_founder_mark(self):
        owner, auth = a_user('founder')
        if hasattr(owner, 'is_founder'):
            owner.is_founder = True
            owner.show_founder_badge = True
            owner.save(update_fields=['is_founder', 'show_founder_badge'])

        res = self.client.post(
            '/organization/create/',
            data=json.dumps({'name': 'Badge Org %s' % uuid.uuid4().hex[:4]}),
            content_type='application/json', **auth)
        self.assertEqual(res.status_code, 201, res.content[:300])
        org = Organization.objects.get()

        res = self.client.get('/organization/%s/members/' % org.slug, **auth)
        self.assertEqual(res.status_code, 200)
        member = res.json()['data']['members'][0]
        self.assertIn('user', member)
        self.assertIn('founder_badge', member['user'],
                      'the member row describes a person without saying whether '
                      'they wear the founder mark')
        self.assertTrue(member['user']['founder_badge'])
