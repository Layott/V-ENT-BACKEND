"""An organisation describes people the same way everything else does.

CEO, 7 September 2026, with a screenshot of their own organisation page:
"under an irganization oge my profilr pic and badge dudnt show".

The founders panel drew a grey circle with two letters in it and a plain name
beside it. Not a rendering bug: `serialize_org` sent

    'founders': [org.org_creator.username]

a list of STRINGS. There was no picture to draw and no badge to draw, because
a username is not a person. The same line sent `owner` as a bare username.

This is the fourth place with that cause - the team owner card, the org member
table, the community author, and now this - which is why `_person` exists and
why nothing may describe somebody by hand. The test is written against the
SHAPE rather than against the founders panel, so the next screen that reads
this payload gets the same guarantee without anybody remembering.
"""
import uuid

from django.test import TestCase
from django.utils import timezone as tz
from rest_framework.test import APIClient

from .models import Organization, UserProfile, Users


def a_user(name='org', founder=False, badge=True):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        full_name='Real Name',
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True)
    u.is_founder = founder
    u.show_founder_badge = badge
    u.login_session_created_at = tz.now()
    u.save()
    return u


class FoundersArePeopleTests(TestCase):
    """Every founder carries what a person carries."""

    def setUp(self):
        self.client = APIClient()
        self.creator = a_user('creator', founder=True)
        UserProfile.objects.create(user=self.creator,
                                   profile_picture='profile_pictures/face.png')
        self.org = Organization.objects.create(
            org_name='Cade Esports %s' % uuid.uuid4().hex[:4],
            org_creator=self.creator, org_owner=self.creator)

    def _detail(self):
        res = self.client.get('/organization/%s/' % self.org.slug)
        self.assertEqual(res.status_code, 200)
        return res.json()['data']['organization']

    def test_a_founder_is_an_object_and_not_a_string(self):
        """The whole fault in one assertion.

        A string cannot carry a face or a badge however the panel is written,
        so this is the line that would have caught it.
        """
        founders = self._detail()['founders']
        self.assertEqual(len(founders), 1)
        self.assertIsInstance(founders[0], dict,
                              'founders must be people, not usernames')

    def test_a_founder_carries_their_picture(self):
        self.assertTrue(self._detail()['founders'][0]['avatar'],
                        'the founder has an uploaded picture and it is not in '
                        'the payload, so the page cannot draw their face')

    def test_a_founder_carries_their_founder_mark(self):
        self.assertIs(self._detail()['founders'][0]['founder_badge'], True)

    def test_a_founder_who_hid_their_badge_does_not_wear_it_here(self):
        """Switching it off in settings switches it off everywhere."""
        self.creator.show_founder_badge = False
        self.creator.save()
        self.assertIs(self._detail()['founders'][0]['founder_badge'], False)

    def test_a_transferred_organisation_credits_both(self):
        """Ownership moving does not erase who started it."""
        new_owner = a_user('newowner')
        self.org.org_owner = new_owner
        self.org.save()
        names = [f['username'] for f in self._detail()['founders']]
        self.assertEqual(names, [self.creator.username, new_owner.username])

    def test_the_same_person_is_not_listed_twice(self):
        """Creator and owner are usually one person."""
        self.assertEqual(len(self._detail()['founders']), 1)


class OwnerIsAPersonTests(TestCase):
    """`owner` was a bare username, which is two faults in one field.

    It could not carry a face, and it is what made `org.owner.username` read
    `undefined` for a signed-out visitor - the comparison that offered every
    stranger a Manage button on every organisation.
    """

    def setUp(self):
        self.client = APIClient()
        self.owner = a_user('owner', founder=True)
        UserProfile.objects.create(user=self.owner,
                                   profile_picture='profile_pictures/face.png')
        self.org = Organization.objects.create(
            org_name='Owned %s' % uuid.uuid4().hex[:4],
            org_creator=self.owner, org_owner=self.owner)

    def test_owner_is_a_person(self):
        res = self.client.get('/organization/list/')
        rows = res.json()['data']['organizations']
        row = next(r for r in rows if r['slug'] == self.org.slug)
        self.assertIsInstance(row['owner'], dict)
        self.assertEqual(row['owner']['username'], self.owner.username)

    def test_the_plain_username_is_still_available(self):
        """`usernameOf()` reads either shape, but anything wanting the bare
        string should not have to unwrap an object to get it."""
        res = self.client.get('/organization/list/')
        rows = res.json()['data']['organizations']
        row = next(r for r in rows if r['slug'] == self.org.slug)
        self.assertEqual(row['owner_username'], self.owner.username)

    def test_a_signed_out_visitor_gets_a_real_username_not_undefined(self):
        """The shape that broke gating: both sides absent and equal.

        With no cookies the payload must still name the owner, so the front
        end compares a real username against nothing and gets false rather
        than comparing undefined against undefined and getting true.
        """
        res = self.client.get('/organization/list/')
        rows = res.json()['data']['organizations']
        row = next(r for r in rows if r['slug'] == self.org.slug)
        self.assertTrue(row['owner']['username'])
        self.assertIsNone(row['my_role'])
