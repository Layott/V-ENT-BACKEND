"""Following an organisation, and what following actually does.

CEO, 2 September 2026: "Users shuould be able to follow an organization, in which
that particular orgs events, tournaments and anything about that org should show
constantly."

Following already existed and did nothing visible. `OrgFollower` rows were
written and never read back for anything a follower could see, which makes a
follow a counter rather than a subscription. The person who pressed it has no way
to tell the difference, which is the worst kind of feature: it looks finished.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_event.models import Event
from vent_tournament.models import Tournament

from .models import Games, OrgFollower, Organization, Users


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('f-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class OrgFollowTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('ofOwner')
        self.fan, self.fan_auth = a_user('ofFan')
        self.org = Organization.objects.create(
            org_name='Vermillion Encore', org_creator=self.owner,
            org_owner=self.owner)
        self.ref = self.org.slug or self.org.org_id

        now = timezone.now()
        game = Games.objects.create(game_title='EA FC 26')
        self.upcoming = Event.objects.create(
            name='Rivalry Series', creator=self.owner, event_type='physical',
            desc='x', entry_fee=0, organization=self.org,
            start_date=now + timedelta(days=3),
            end_date=now + timedelta(days=5),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=2))
        self.past = Event.objects.create(
            name='Last Season', creator=self.owner, event_type='physical',
            desc='x', entry_fee=0, organization=self.org,
            start_date=now - timedelta(days=40),
            end_date=now - timedelta(days=39),
            reg_start_date=now - timedelta(days=60),
            reg_end_date=now - timedelta(days=41))
        self.tournament = Tournament.objects.create(
            tournament_title='Encore Open', tournament_game=game,
            tournament_creator=self.owner, tournament_organization=self.org,
            start_date_and_time=now + timedelta(days=4),
            end_date_and_time=now + timedelta(days=6),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='individual',
            entry_fee='Free', is_draft=False)

    # ------------------------------------------------------------- follow

    def test_following_and_unfollowing(self):
        url = '/organization/%s/follow/' % self.ref
        res = self.client.post(url, data={}, content_type='application/json',
                               **self.fan_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(res.json()['data']['is_following'])
        self.assertEqual(res.json()['data']['follower_count'], 1)

        res = self.client.post(url, data={}, content_type='application/json',
                               **self.fan_auth)
        self.assertFalse(res.json()['data']['is_following'])
        self.assertEqual(res.json()['data']['follower_count'], 0)

    def test_following_needs_an_account(self):
        res = self.client.post('/organization/%s/follow/' % self.ref,
                               data={}, content_type='application/json')
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(OrgFollower.objects.count(), 0)

    def test_the_list_of_who_i_follow(self):
        self.client.post('/organization/%s/follow/' % self.ref, data={},
                         content_type='application/json', **self.fan_auth)
        res = self.client.get('/organization/following/', **self.fan_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        names = [o['name'] for o in res.json()['data']['organizations']]
        self.assertIn('Vermillion Encore', names)

    def test_that_route_is_not_swallowed_by_the_detail_route(self):
        """`/organization/following/` must not be read as an org called
        "following". Route order decides this and nothing else would catch it."""
        res = self.client.get('/organization/following/', **self.fan_auth)
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertIn('organizations', res.json()['data'])

    # --------------------------------------------------------------- feed

    def follow_then_feed(self):
        self.client.post('/organization/%s/follow/' % self.ref, data={},
                         content_type='application/json', **self.fan_auth)
        return self.client.get('/organization/following/feed/', **self.fan_auth)

    def test_the_feed_carries_events_and_tournaments_together(self):
        """A follower asks "what is coming up from these people", not "show me
        the events table". Two lists would be merged and re-sorted by every
        screen, and they would drift."""
        res = self.follow_then_feed()
        self.assertEqual(res.status_code, 200, res.content[:300])
        items = res.json()['data']['items']
        kinds = {i['kind'] for i in items}
        self.assertEqual(kinds, {'event', 'tournament'})
        titles = [i['title'] for i in items]
        self.assertIn('Rivalry Series', titles)
        self.assertIn('Encore Open', titles)

    def test_upcoming_comes_before_past(self):
        res = self.follow_then_feed()
        titles = [i['title'] for i in res.json()['data']['items']]
        self.assertLess(titles.index('Rivalry Series'), titles.index('Last Season'))

    def test_past_is_still_shown(self):
        """An organisation with nothing upcoming is not one with nothing to
        show, and an empty feed is what makes somebody unfollow."""
        titles = [i['title'] for i in self.follow_then_feed().json()['data']['items']]
        self.assertIn('Last Season', titles)

    def test_every_item_carries_a_link_and_its_organisation(self):
        for item in self.follow_then_feed().json()['data']['items']:
            self.assertTrue(item['url'].startswith('/'), item)
            self.assertEqual(item['organization']['name'], 'Vermillion Encore')

    def test_nothing_from_organisations_i_do_not_follow(self):
        other_owner, _ = a_user('ofOther')
        other = Organization.objects.create(
            org_name='Somebody Else', org_creator=other_owner,
            org_owner=other_owner)
        now = timezone.now()
        Event.objects.create(
            name='Not Mine', creator=other_owner, event_type='physical',
            desc='x', entry_fee=0, organization=other,
            start_date=now + timedelta(days=2), end_date=now + timedelta(days=3),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=1))

        titles = [i['title'] for i in self.follow_then_feed().json()['data']['items']]
        self.assertNotIn('Not Mine', titles)

    def test_the_feed_needs_an_account(self):
        res = self.client.get('/organization/following/feed/')
        self.assertIn(res.status_code, (401, 403))

    def test_following_nothing_says_so_rather_than_erroring(self):
        res = self.client.get('/organization/following/feed/', **self.fan_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['items'], [])
