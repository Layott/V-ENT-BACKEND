"""Approved sites, end to end through the API (P8, level 2).

CEO, inbox 480: "we want people to be able to whitelist v-ent on their site or
put tournament registrations and brackets system under their own website from
v-ent." Design 14c: "The organiser adds a domain and proves it with a DNS TXT
record or a meta tag; V-ENT then sends frame-ancestors for that tournament
listing only approved domains, so nobody else can frame it. Views counted per
domain."

Who may do what, as every role: signed out (401 on every door but the two
public ones), a signed-in stranger (403, nothing changes), the organiser, a
co-manager of an event.
"""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event.models import Event, EventManager
from vent_tournament.models import Tournament

from .models import EmbedLock, EmbedView, Site


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('s-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def proven(owner, host, method='dns'):
    return Site.objects.create(owner=owner, host=host, method=method, verified_at=timezone.now())


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.organiser, self.auth = a_user('st_org')
        self.stranger, self.stranger_auth = a_user('st_other')
        game = Games.objects.create(game_title='EA FC ST')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Sites Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=6),
            bracket_type='single_elimination', is_draft=False)
        self.event = Event.objects.create(
            name='Sites Fest', creator=self.organiser, event_type='physical', desc='x', entry_fee=0,
            start_date=now + timedelta(days=5), end_date=now + timedelta(days=5, hours=8),
            reg_start_date=now - timedelta(days=1), reg_end_date=now + timedelta(days=4))

    def post(self, path, body=None, auth=None):
        return self.client.post(path, data=body or {}, content_type='application/json', **(auth or {}))

    def put(self, path, body, auth=None):
        return self.client.put(path, data=body, content_type='application/json', **(auth or {}))


class SignedOutTests(Base):
    def test_every_door_but_the_public_two_wants_an_account(self):
        slug = self.tournament.slug
        for method, path in (('get', '/sites/mine/'), ('post', '/sites/mine/'),
                             ('post', '/sites/mine/theirsite.com/check/'),
                             ('delete', '/sites/mine/theirsite.com/'),
                             ('get', '/sites/tournament/%s/' % slug),
                             ('put', '/sites/tournament/%s/locks/' % slug),
                             ('get', '/sites/event/%s/' % self.event.slug)):
            if method == 'get':
                res = self.client.get(path)
            else:
                res = getattr(self.client, method)(path, data='{}', content_type='application/json')
            self.assertEqual(res.status_code, 401, (method, path))

    def test_the_frame_policy_and_the_view_count_are_public(self):
        res = self.client.get('/sites/frame/tournament/%s/' % self.tournament.slug)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data'], {'ancestors': []})
        res = self.post('/sites/view/', {'kind': 'event', 'ref': self.event.slug, 'host': 'theirsite.com'})
        self.assertEqual(res.status_code, 200)


class MySitesTests(Base):
    def test_adding_a_site_hands_back_what_to_publish(self):
        res = self.post('/sites/mine/', {'host': 'https://TheirSite.com/tournaments'}, self.auth)
        self.assertEqual(res.status_code, 201, res.json())
        row = res.json()['data']
        token = Site.objects.get(owner=self.organiser).token
        self.assertEqual(row['host'], 'theirsite.com')
        self.assertFalse(row['verified'])
        self.assertEqual(row['dns'], {'name': '_vent-verify.theirsite.com', 'value': 'vent-verify=%s' % token})
        self.assertEqual(row['meta'], '<meta name="vent-verify" content="%s">' % token)
        # The same name again is the same site, not a second token.
        again = self.post('/sites/mine/', {'host': 'theirsite.com'}, self.auth)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(Site.objects.filter(owner=self.organiser).count(), 1)

    @override_settings(FRONTEND_URL='https://v-ent.co')
    def test_a_name_that_cannot_be_a_site_is_refused_with_a_code(self):
        for host, code in (('127.0.0.1', 'SITE_HOST_INVALID'), ('localhost', 'SITE_HOST_INVALID'),
                           ('app.v-ent.co', 'SITE_HOST_OURS')):
            res = self.post('/sites/mine/', {'host': host}, self.auth)
            self.assertEqual((res.status_code, res.json()['code']), (400, code), host)
        res = self.post('/sites/mine/', {}, self.auth)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'INVALID_INPUT'))
        self.assertFalse(Site.objects.exists())

    def test_twenty_sites_each_at_most(self):
        for n in range(20):
            Site.objects.create(owner=self.organiser, host='site%d.com' % n)
        res = self.post('/sites/mine/', {'host': 'one-more.com'}, self.auth)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'SITE_LIMIT'))

    def test_a_list_holds_your_own_sites_only(self):
        Site.objects.create(owner=self.stranger, host='theirs.com')
        Site.objects.create(owner=self.organiser, host='mine.com')
        res = self.client.get('/sites/mine/', **self.auth)
        self.assertEqual([s['host'] for s in res.json()['data']['sites']], ['mine.com'])

    def test_a_check_records_the_proof(self):
        Site.objects.create(owner=self.organiser, host='theirsite.com')
        with patch('vent_sites.verify.check', return_value=(True, 'dns')):
            res = self.post('/sites/mine/theirsite.com/check/', {}, self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.assertTrue(res.json()['data']['verified'])
        self.assertEqual(res.json()['data']['method'], 'dns')

    def test_a_failed_check_says_why_and_keeps_the_site(self):
        Site.objects.create(owner=self.organiser, host='theirsite.com')
        with patch('vent_sites.verify.check', return_value=(False, 'SITE_DNS_MISSING')):
            res = self.post('/sites/mine/theirsite.com/check/', {'method': 'dns'}, self.auth)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'SITE_DNS_MISSING'))
        self.assertEqual(res.json()['data']['last_code'], 'SITE_DNS_MISSING')
        self.assertIsNone(Site.objects.get().verified_at)

    def test_somebody_elses_site_is_not_there_for_you(self):
        Site.objects.create(owner=self.organiser, host='theirsite.com')
        with patch('vent_sites.verify.check', return_value=(True, 'dns')) as check:
            res = self.post('/sites/mine/theirsite.com/check/', {}, self.stranger_auth)
        self.assertEqual((res.status_code, res.json()['code']), (404, 'SITE_NOT_FOUND'))
        check.assert_not_called()
        res = self.client.delete('/sites/mine/theirsite.com/', **self.stranger_auth)
        self.assertEqual(res.status_code, 404)
        self.assertTrue(Site.objects.filter(owner=self.organiser).exists())

    @override_settings(AUTH_THROTTLE_ENABLED=True)
    def test_checking_is_rate_limited(self):
        Site.objects.create(owner=self.organiser, host='theirsite.com')
        with patch('vent_sites.verify.check', return_value=(False, 'SITE_DNS_MISSING')):
            for _ in range(10):
                self.assertEqual(self.post('/sites/mine/theirsite.com/check/', {}, self.auth).status_code, 400)
            res = self.post('/sites/mine/theirsite.com/check/', {}, self.auth)
        self.assertEqual((res.status_code, res.json()['code']), (429, 'TOO_MANY_ATTEMPTS'))

    def test_removing_a_site_releases_what_it_locked(self):
        site = proven(self.organiser, 'theirsite.com')
        EmbedLock.objects.create(site=site, tournament=self.tournament, added_by=self.organiser)
        res = self.client.delete('/sites/mine/theirsite.com/', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(EmbedLock.objects.exists())


class LockTests(Base):
    def locks_url(self, kind='tournament', ref=None):
        return '/sites/%s/%s/locks/' % (kind, ref or self.tournament.slug)

    def test_a_stranger_can_neither_read_nor_lock(self):
        site = proven(self.stranger, 'evil.com')
        res = self.client.get('/sites/tournament/%s/' % self.tournament.slug, **self.stranger_auth)
        self.assertEqual((res.status_code, res.json()['code']), (403, 'SITE_NOT_MANAGER'))
        res = self.put(self.locks_url(), {'hosts': [site.host]}, self.stranger_auth)
        self.assertEqual((res.status_code, res.json()['code']), (403, 'SITE_NOT_MANAGER'))
        self.assertFalse(EmbedLock.objects.exists())

    def test_a_lock_needs_a_proven_site_of_your_own(self):
        Site.objects.create(owner=self.organiser, host='unproven.com')
        proven(self.stranger, 'theirs.com')
        for hosts, code in ((['unproven.com'], 'SITE_NOT_VERIFIED'), (['theirs.com'], 'SITE_NOT_YOURS'),
                            (['nobody.com'], 'SITE_NOT_YOURS'), (['127.0.0.1'], 'SITE_HOST_INVALID'),
                            (['s%d.com' % n for n in range(11)], 'SITE_LOCKS_LIMIT')):
            res = self.put(self.locks_url(), {'hosts': hosts}, self.auth)
            self.assertEqual((res.status_code, res.json()['code']), (400, code), hosts)
        res = self.put(self.locks_url(), {'hosts': 'theirsite.com'}, self.auth)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'INVALID_INPUT'))
        self.assertFalse(EmbedLock.objects.exists())

    def test_a_locked_tournament_frames_only_on_its_sites(self):
        proven(self.organiser, 'theirsite.com', 'dns')
        proven(self.organiser, 'www.other.org', 'meta')
        res = self.put(self.locks_url(), {'hosts': ['theirsite.com', 'www.other.org']}, self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        expected = ["'self'", 'https://theirsite.com', 'https://*.theirsite.com', 'https://www.other.org']
        self.assertFalse(res.json()['data']['anywhere'])
        self.assertEqual(res.json()['data']['ancestors'], expected)
        frame = self.client.get('/sites/frame/tournament/%s/' % self.tournament.slug)
        self.assertEqual(frame.json()['data']['ancestors'], expected)
        # Unlocked again: anywhere, as before P8.
        self.put(self.locks_url(), {'hosts': []}, self.auth)
        frame = self.client.get('/sites/frame/tournament/%s/' % self.tournament.slug)
        self.assertEqual(frame.json()['data']['ancestors'], [])

    def test_keeps_others_lock(self):
        # A co-manager of the event names their own site beside the
        # organiser's; the organiser's lock stays because the list keeps it.
        manager, manager_auth = a_user('st_mgr')
        EventManager.objects.create(event=self.event, user=manager, role='manager')
        proven(self.organiser, 'organiser.com')
        proven(manager, 'manager.com')
        self.put(self.locks_url('event', self.event.slug), {'hosts': ['organiser.com']}, self.auth)
        res = self.put(self.locks_url('event', self.event.slug),
                       {'hosts': ['organiser.com', 'manager.com']}, manager_auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.assertEqual(sorted(l.site.host for l in EmbedLock.objects.filter(event=self.event)),
                         ['manager.com', 'organiser.com'])
        # Left out of the list on purpose, it goes.
        self.put(self.locks_url('event', self.event.slug), {'hosts': ['manager.com']}, manager_auth)
        self.assertEqual([l.site.host for l in EmbedLock.objects.filter(event=self.event)], ['manager.com'])

    def test_old_slug_answers(self):
        site = proven(self.organiser, 'theirsite.com', 'meta')
        EmbedLock.objects.create(site=site, tournament=self.tournament, added_by=self.organiser)
        old = self.tournament.slug
        self.tournament.tournament_title = 'Sites Cup Renamed'
        self.tournament.save()
        self.assertNotEqual(self.tournament.slug, old)
        frame = self.client.get('/sites/frame/tournament/%s/' % old)
        self.assertEqual(frame.status_code, 200)
        self.assertEqual(frame.json()['data']['ancestors'], ["'self'", 'https://theirsite.com'])

    def test_a_draft_or_an_unknown_record_has_no_policy(self):
        self.tournament.is_draft = True
        self.tournament.save()
        for path in ('/sites/frame/tournament/%s/' % self.tournament.slug, '/sites/frame/tournament/nope/',
                     '/sites/frame/team/%s/' % self.tournament.slug):
            res = self.client.get(path)
            self.assertEqual((res.status_code, res.json()['code']), (404, 'SITE_RECORD_NOT_FOUND'), path)


class ViewBeaconTests(Base):
    def count(self, host, kind='tournament', ref=None):
        return self.post('/sites/view/', {'kind': kind, 'ref': ref or self.tournament.slug, 'host': host})

    def test_views_are_counted_per_site_per_day(self):
        self.count('https://theirsite.com/cup')
        self.count('theirsite.com')
        self.count('other.org')
        row = EmbedView.objects.get(tournament=self.tournament, host='theirsite.com')
        self.assertEqual((row.views, row.day), (2, timezone.localdate()))
        res = self.client.get('/sites/tournament/%s/' % self.tournament.slug, **self.auth)
        data = res.json()['data']
        self.assertEqual(data['views'], [{'host': 'theirsite.com', 'views': 2}, {'host': 'other.org', 'views': 1}])
        self.assertEqual(data['views_total'], 3)

    def test_unknown_host(self):
        # A host page with no-referrer: the frame cannot see where it is.
        self.count('')
        self.count('not a host at all')
        self.assertEqual(EmbedView.objects.get(tournament=self.tournament, host='').views, 2)

    @override_settings(FRONTEND_URL='https://v-ent.co')
    def test_v_ent_itself_is_not_a_view(self):
        res = self.count('https://v-ent.co/tournaments/x/manage')
        self.assertEqual(res.json()['data'], {'counted': False})
        self.assertFalse(EmbedView.objects.exists())

    def test_an_unknown_record_is_not_counted(self):
        res = self.count('theirsite.com', ref='no-such-cup')
        self.assertEqual((res.status_code, res.json()['code']), (404, 'SITE_RECORD_NOT_FOUND'))

    @override_settings(AUTH_THROTTLE_ENABLED=True)
    def test_counting_is_rate_limited(self):
        for _ in range(60):
            self.count('theirsite.com')
        res = self.count('theirsite.com')
        self.assertEqual(res.status_code, 429)
        self.assertEqual(EmbedView.objects.get(host='theirsite.com').views, 60)
