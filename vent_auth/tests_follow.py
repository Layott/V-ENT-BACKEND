"""Following a team or a person, with a count anybody can see.

CEO, 7 September 2026: "org owners should also be able to see their followers,
same for teams and users and info on like how many."

Organisations had all of it. Teams and people had none of it - no table, no
endpoint, no count, no list - so a team page could not say how many people
cared about it and a player could not see who followed them.

One `Follow` table for both kinds rather than `TeamFollower` and
`UserFollower`, because following is one concept. `Teams` was defined twice in
this codebase once and it took a migration to unpick; two follower tables would
be the same mistake with a head start.
"""
import uuid

from django.test import TestCase
from django.utils import timezone as tz
from rest_framework.test import APIClient

from .models import Follow, Games, Teams, Users, follower_count, is_following


def a_user(name='f'):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True)
    u.login_session_created_at = tz.now()
    u.save()
    return u


def a_team(owner):
    game, _ = Games.objects.get_or_create(game_title='EA FC 26')
    return Teams.objects.create(
        team_name='Squad %s' % uuid.uuid4().hex[:6], game=game,
        description='x', team_creator=owner, team_owner=owner,
        penalty_points=0, number_of_members=1)


class FollowATeamTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = a_user('owner')
        self.fan = a_user('fan')
        self.team = a_team(self.owner)
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.fan.login_session_token}

    def url(self):
        return '/auth/follow/team/%s/' % self.team.slug

    def test_following_a_team_makes_a_row_and_a_count(self):
        res = self.client.post(self.url(), {}, format='json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertIs(res.json()['data']['is_following'], True)
        self.assertEqual(res.json()['data']['follower_count'], 1)
        self.assertEqual(Follow.objects.filter(kind='team').count(), 1)

    def test_following_twice_is_still_one_follower(self):
        """A double tap on a slow connection must not read as two people."""
        self.client.post(self.url(), {}, format='json', **self.auth)
        res = self.client.post(self.url(), {}, format='json', **self.auth)
        self.assertEqual(res.json()['data']['follower_count'], 1)

    def test_unfollowing_removes_it(self):
        self.client.post(self.url(), {}, format='json', **self.auth)
        res = self.client.delete(self.url(), **self.auth)
        self.assertIs(res.json()['data']['is_following'], False)
        self.assertEqual(res.json()['data']['follower_count'], 0)

    def test_a_stranger_cannot_follow(self):
        res = self.client.post(self.url(), {}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_the_team_is_addressed_by_slug_not_by_a_key(self):
        """The slug rule: no primary key in an address a person can see."""
        segment = self.url().rsplit('/', 2)[1]
        self.assertEqual(segment, self.team.slug)
        # Not the bare key. A slug may CONTAIN digits, so the test is that the
        # segment is not the id itself rather than that no digit appears -
        # which is what the first version of this assertion got wrong.
        self.assertNotEqual(segment, str(self.team.team_id))


class FollowAPersonTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.star = a_user('star')
        self.fan = a_user('fan')
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.fan.login_session_token}

    def url(self, who=None):
        return '/auth/follow/user/%s/' % (who or self.star.username)

    def test_following_a_person_works(self):
        res = self.client.post(self.url(), {}, format='json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['follower_count'], 1)

    def test_you_cannot_follow_yourself(self):
        res = self.client.post(self.url(self.fan.username), {}, format='json',
                               **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'FOLLOW_SELF')

    def test_an_unknown_person_is_a_404_not_a_row(self):
        res = self.client.post(self.url('nobody_at_all'), {}, format='json',
                               **self.auth)
        self.assertEqual(res.status_code, 404)


class FollowerListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = a_user('owner')
        self.team = a_team(self.owner)
        self.fans = [a_user('fan%d' % i) for i in range(3)]
        for f in self.fans:
            Follow.objects.create(follower=f, kind='team',
                                  target_id=self.team.team_id)

    def test_anybody_can_see_the_count(self):
        """Public on purpose. A follower count is a fact about a team the same
        way its member count is, and hiding it makes the page look empty to
        everybody deciding whether to join."""
        res = self.client.get('/auth/follow/team/%s/followers/' % self.team.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['count'], 3)

    def test_followers_are_people_with_faces_and_badges(self):
        """Through the one person builder, like every other list of people."""
        res = self.client.get('/auth/follow/team/%s/followers/' % self.team.slug)
        row = res.json()['data']['followers'][0]
        self.assertIn('username', row)
        self.assertIn('avatar', row)
        self.assertIn('founder_badge', row)

    def test_a_signed_out_visitor_is_not_following(self):
        """Never None: a screen puts this straight into `is_following`, and
        None there is a third state nothing handles."""
        res = self.client.get('/auth/follow/team/%s/followers/' % self.team.slug)
        self.assertIs(res.json()['data']['is_following'], False)


class WhatIFollowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.me = a_user('me')
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.me.login_session_token}
        owner = a_user('owner')
        self.team = a_team(owner)
        self.star = a_user('star')
        Follow.objects.create(follower=self.me, kind='team',
                              target_id=self.team.team_id)
        Follow.objects.create(follower=self.me, kind='user',
                              target_id=self.star.user_id)

    def test_one_call_answers_the_whole_question(self):
        """Three endpoints would be three chances to show two thirds of it."""
        res = self.client.get('/auth/follow/mine/', **self.auth)
        data = res.json()['data']
        self.assertEqual(len(data['teams']), 1)
        self.assertEqual(len(data['users']), 1)
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['teams'][0]['slug'], self.team.slug)

    def test_a_stranger_gets_401(self):
        self.assertEqual(self.client.get('/auth/follow/mine/').status_code, 401)


class SharedHelperTests(TestCase):
    """`follower_count` and `is_following` answer for every kind.

    One function each, so a count is computed the same way everywhere. The day
    organisations move into `Follow`, these are the only two places that change.
    """

    def setUp(self):
        self.owner = a_user('owner')
        self.team = a_team(self.owner)
        self.fan = a_user('fan')

    def test_count_for_a_team(self):
        self.assertEqual(follower_count('team', self.team.team_id), 0)
        Follow.objects.create(follower=self.fan, kind='team',
                              target_id=self.team.team_id)
        self.assertEqual(follower_count('team', self.team.team_id), 1)

    def test_count_for_an_organisation_reads_its_own_table(self):
        from .models import Organization, OrgFollower
        org = Organization.objects.create(org_name='Shared %s' % uuid.uuid4().hex[:5],
                                          org_creator=self.owner, org_owner=self.owner)
        OrgFollower.objects.create(org=org, user=self.fan)
        self.assertEqual(follower_count('org', org.org_id), 1)

    def test_is_following_is_false_for_nobody(self):
        self.assertIs(is_following(None, 'team', self.team.team_id), False)


class OrgTypeTests(TestCase):
    """An organisation says what kind of thing it is.

    CEO: "not all orgs will be esports orgs or event orgs, can just be for
    teams. so lets manage accordingly."
    """

    def setUp(self):
        self.owner = a_user('orgowner')

    def make(self, org_type=None):
        from .models import Organization
        kwargs = {'org_name': 'Typed %s' % uuid.uuid4().hex[:5],
                  'org_creator': self.owner, 'org_owner': self.owner}
        if org_type:
            kwargs['org_type'] = org_type
        return Organization.objects.create(**kwargs)

    def test_the_default_is_mixed_rather_than_a_guess(self):
        """Everything created before this existed. Claiming to know what an
        organisation does is worse than saying it has not been said."""
        self.assertEqual(self.make().org_type, 'mixed')

    def test_a_team_org_is_not_asked_about_ticketing(self):
        caps = self.make('team').capabilities()
        self.assertIs(caps['teams'], True)
        self.assertIs(caps['ticketing'], False)
        self.assertIs(caps['events'], False)

    def test_an_event_organiser_gets_ticketing_and_vendors(self):
        caps = self.make('events').capabilities()
        self.assertIs(caps['ticketing'], True)
        self.assertIs(caps['vendors'], True)
        self.assertIs(caps['teams'], False)

    def test_an_unknown_type_falls_back_to_everything(self):
        """Data written by an older version must not hide working controls."""
        org = self.make()
        org.org_type = 'something_new'
        self.assertIs(org.capabilities()['events'], True)
