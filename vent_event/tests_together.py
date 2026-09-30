"""Going together at an event (inbox 305, 305a to 305c, 29 September 2026).

Every rule in together.py, from each side: who takes part at all, what each age
may turn on, who sees attendance and who sees the area, who may ping whom, what
changes when the event ends, and the admin door that keeps it and logs it.
"""
from datetime import date, time, timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import AdminAction, Follow, Games, UserBlock, UserProfile, Users

from .models import DepartureRecord, Event, EventPing, EventPresence, Ticket, TicketTier

_n = [0]


def person(name, age=None, admin_role=None):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('t-%s' % name)[:16], login_session_created_at=timezone.now(),
        is_staff=admin_role is not None, admin_role=admin_role)
    if admin_role:
        user.login_session_2fa_at = timezone.now()
        user.save(update_fields=['login_session_2fa_at'])
    if age is not None:
        today = date.today()
        UserProfile.objects.create(user=user, date_of_birth=date(today.year - age, 1, 1)
                                   if (today.month, today.day) >= (1, 1) else None)
    return user


def auth(user):
    return {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def follow_both(a, b):
    Follow.objects.create(follower=a, kind='user', target_id=b.user_id)
    Follow.objects.create(follower=b, kind='user', target_id=a.user_id)


class TogetherBase(TestCase):
    def setUp(self):
        self.org = person('tg_org', 40)
        game = Games.objects.create(game_title='EA FC TG')
        soon = timezone.localtime(timezone.now()) + timedelta(hours=2)
        self.event = Event.objects.create(
            name='Together Probe', game=game, creator=self.org, event_type='physical',
            desc='probe', entry_fee=0, reg_start_date=timezone.now() - timedelta(days=1),
            reg_end_date=timezone.now() + timedelta(days=1), event_date=soon.date(),
            start_time=soon.time(), end_time=time(23, 59), location='Lagos')
        self.tier = TicketTier.objects.create(event=self.event, name='General', price=0, quantity=100)

    def ticket(self, user):
        _n[0] += 1
        return Ticket.objects.create(event=self.event, tier=self.tier, user=user,
                                     code='TG%08d' % _n[0], attendee_name=user.username)

    def url(self, tail=''):
        return '/event/%s/together/%s' % (self.event.slug or self.event.event_id, tail)

    def me(self, user, **settings):
        return self.client.post(self.url('me/'), settings, content_type='application/json', **auth(user))

    def state(self, user=None):
        return self.client.get(self.url(), **(auth(user) if user else {}))


class WhoTakesPartTests(TogetherBase):
    def test_signed_out_sees_the_count_only(self):
        a = person('tg_a', 25)
        self.ticket(a)
        self.me(a, attendance_visibility='event')
        body = self.state().json()['data']
        self.assertEqual((body['signed_in'], body['count']), (False, 1))
        self.assertNotIn('people', body)

    def test_no_ticket_no_birthday_under_13_and_an_18_plus_event(self):
        nobody = person('tg_noticket', 25)
        self.assertEqual(self.me(nobody, attendance_visibility='event').json()['code'], 'NO_TICKET')
        unknown = person('tg_nodob')
        self.ticket(unknown)
        self.assertEqual(self.me(unknown, attendance_visibility='event').json()['code'], 'NEEDS_BIRTHDAY')
        child = person('tg_child', 11)
        self.ticket(child)
        self.assertEqual(self.me(child, attendance_visibility='mutuals').json()['code'], 'TOO_YOUNG')
        teen = person('tg_teen', 17)
        self.ticket(teen)
        self.event.min_age = 18
        self.event.save(update_fields=['min_age'])
        self.assertEqual(self.me(teen, attendance_visibility='mutuals').json()['code'], 'ADULTS_ONLY_EVENT')

    def test_what_each_age_may_turn_on(self):
        young, teen, adult = person('tg_14', 14), person('tg_16', 16), person('tg_30', 30)
        for u in (young, teen, adult):
            self.ticket(u)
        self.assertEqual(self.me(young, attendance_visibility='event').json()['code'], 'TOGETHER_NOT_ALLOWED')
        self.assertEqual(self.me(young, attendance_visibility='mutuals').status_code, 200)
        self.assertEqual(self.me(young, departure_visibility='mutuals', departure_area='Ikeja')
                         .json()['code'], 'TOGETHER_NOT_ALLOWED')
        self.assertEqual(self.me(young, pings_open=True).json()['code'], 'TOGETHER_NOT_ALLOWED')
        self.assertEqual(self.me(teen, departure_visibility='approved', departure_area='Yaba')
                         .json()['code'], 'TOGETHER_NOT_ALLOWED')
        self.assertEqual(self.me(teen, departure_visibility='mutuals', departure_area='Yaba').status_code, 200)
        self.assertEqual(self.me(adult, attendance_visibility='event', departure_visibility='approved',
                                 departure_area='Lekki', pings_open=True).status_code, 200)

    def test_an_area_is_required_to_share_one(self):
        a = person('tg_area', 30)
        self.ticket(a)
        self.assertEqual(self.me(a, departure_visibility='mutuals', departure_area='').json()['code'],
                         'AREA_REQUIRED')


class WhoSeesWhatTests(TogetherBase):
    def setUp(self):
        super().setUp()
        self.ada, self.bayo, self.cara = person('tg_ada', 30), person('tg_bayo', 28), person('tg_cara', 26)
        self.teen = person('tg_teen2', 16)
        for u in (self.ada, self.bayo, self.cara, self.teen):
            self.ticket(u)
        self.outsider = person('tg_out', 30)   # no ticket

    def people(self, viewer):
        return {p['person']['username']: p for p in self.state(viewer).json()['data']['people']}

    def test_everyone_at_the_event_means_ticket_holders(self):
        self.me(self.ada, attendance_visibility='event')
        self.assertIn('tg_ada', self.people(self.bayo))
        self.assertEqual(self.state(self.outsider).json()['data']['reason'], 'NO_TICKET')

    def test_mutuals_means_mutuals(self):
        self.me(self.ada, attendance_visibility='mutuals')
        self.assertNotIn('tg_ada', self.people(self.bayo))
        follow_both(self.ada, self.bayo)
        self.assertIn('tg_ada', self.people(self.bayo))

    def test_a_block_hides_both_ways(self):
        self.me(self.ada, attendance_visibility='event')
        UserBlock.objects.create(blocker=self.bayo, blocked=self.ada)
        self.assertNotIn('tg_ada', self.people(self.bayo))

    def test_the_area_goes_to_mutuals_and_the_people_approved(self):
        self.me(self.ada, attendance_visibility='event', departure_visibility='approved',
                departure_area='Surulere')
        self.assertEqual(self.people(self.bayo)['tg_ada']['departure_area'], '')
        res = self.client.post(self.url('approve/'), {'username': 'tg_bayo'},
                               content_type='application/json', **auth(self.ada))
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(self.people(self.bayo)['tg_ada']['departure_area'], 'Surulere')
        self.assertEqual(self.people(self.cara)['tg_ada']['departure_area'], '')

    def test_an_adult_never_sees_a_minors_area(self):
        follow_both(self.teen, self.ada)
        self.me(self.teen, attendance_visibility='event', departure_visibility='mutuals',
                departure_area='Ikeja')
        self.assertEqual(self.people(self.ada)['tg_teen2']['departure_area'], '')

    def test_after_the_event_nobody_sees_the_area_and_it_cannot_change_but_it_is_kept(self):
        follow_both(self.ada, self.bayo)
        self.me(self.ada, attendance_visibility='event', departure_visibility='mutuals',
                departure_area='Ajah')
        self.me(self.ada, departure_area='Lekki Phase 1')
        self.assertEqual(DepartureRecord.objects.filter(user=self.ada).count(), 2)
        self.event.end_date = timezone.now() - timedelta(minutes=1)
        self.event.save(update_fields=['end_date'])
        self.assertEqual(self.people(self.bayo)['tg_ada']['departure_area'], '')
        self.assertEqual(self.state(self.ada).json()['data']['me']['departure_area'], '')
        self.assertEqual(self.me(self.ada, departure_area='Oops').json()['code'], 'EVENT_OVER')
        self.assertEqual(DepartureRecord.objects.filter(user=self.ada).count(), 2, 'kept for admins')


class PingTests(TogetherBase):
    def setUp(self):
        super().setUp()
        self.ada, self.bayo = person('tg_pa', 30), person('tg_pb', 29)
        self.t1, self.t2 = person('tg_pt1', 16), person('tg_pt2', 17)
        for u in (self.ada, self.bayo, self.t1, self.t2):
            self.ticket(u)
            self.me(u, attendance_visibility='event', pings_open=True)

    def ping(self, sender, username):
        return self.client.post(self.url('ping/'), {'username': username, 'message': 'coffee?'},
                                content_type='application/json', **auth(sender))

    def test_adults_ping_and_accepting_opens_a_conversation(self):
        res = self.ping(self.ada, 'tg_pb')
        self.assertEqual(res.status_code, 200, res.content[:200])
        ping_id = res.json()['data']['id']
        self.assertEqual(self.ping(self.ada, 'tg_pb').json()['code'], 'PING_ALREADY')
        self.assertEqual(self.ping(self.bayo, 'tg_pa').json()['code'], 'PING_THEIRS_WAITING')
        # Somebody else's ping answers as if it did not exist.
        wrong = self.client.post(self.url('ping/%d/answer/' % ping_id), {'accept': True},
                                 content_type='application/json', **auth(self.ada))
        self.assertEqual(wrong.status_code, 404)
        ok = self.client.post(self.url('ping/%d/answer/' % ping_id), {'accept': True},
                              content_type='application/json', **auth(self.bayo))
        self.assertEqual(ok.status_code, 200, ok.content[:200])
        self.assertTrue(ok.json()['data']['conversation'])
        self.assertEqual(self.client.post(self.url('ping/%d/answer/' % ping_id), {'accept': False},
                                          content_type='application/json', **auth(self.bayo))
                         .json()['code'], 'PING_ANSWERED')

    def test_never_between_an_adult_and_a_minor(self):
        follow_both(self.ada, self.t1)
        self.assertEqual(self.ping(self.ada, 'tg_pt1').json()['code'], 'PING_AGE')
        self.assertEqual(self.ping(self.t1, 'tg_pa').json()['code'], 'PING_AGE')

    def test_minors_only_between_mutuals(self):
        self.assertEqual(self.ping(self.t1, 'tg_pt2').json()['code'], 'PING_MUTUALS_ONLY')
        follow_both(self.t1, self.t2)
        self.assertEqual(self.ping(self.t1, 'tg_pt2').status_code, 200)

    def test_closed_pings_and_blocks(self):
        self.me(self.bayo, pings_open=False)
        self.assertEqual(self.ping(self.ada, 'tg_pb').json()['code'], 'PINGS_CLOSED')
        self.me(self.bayo, pings_open=True)
        UserBlock.objects.create(blocker=self.bayo, blocked=self.ada)
        self.assertEqual(self.ping(self.ada, 'tg_pb').json()['code'], 'PINGS_CLOSED')

    def test_ten_a_day(self):
        for i in range(10):
            u = person('tg_many%d' % i, 30)
            self.ticket(u)
            self.me(u, attendance_visibility='event', pings_open=True)
            self.assertEqual(self.ping(self.ada, u.username).status_code, 200)
        extra = person('tg_many_x', 30)
        self.ticket(extra)
        self.me(extra, attendance_visibility='event', pings_open=True)
        res = self.ping(self.ada, 'tg_many_x')
        self.assertEqual((res.status_code, res.json()['code']), (429, 'PING_LIMIT'))


class LocationHistoryTests(TogetherBase):
    def setUp(self):
        super().setUp()
        self.ada = person('tg_la', 30)
        self.ticket(self.ada)
        self.me(self.ada, attendance_visibility='event', departure_visibility='mutuals',
                departure_area='Ikeja GRA')
        self.url_admin = '/event/admin/location-history/'

    def test_only_the_named_permission_and_every_search_is_logged(self):
        general = person('tg_admin', 40, admin_role='admin')
        self.assertEqual(self.client.get(self.url_admin, {'person': 'tg_la', 'reason': 'report 12'},
                                         **auth(general)).status_code, 403)
        mod = person('tg_mod', 40, admin_role='mod_admin')
        self.assertEqual(self.client.get(self.url_admin, {'person': 'tg_la'}, **auth(mod)).json()['code'],
                         'REASON_REQUIRED')
        res = self.client.get(self.url_admin, {'person': 'tg_la', 'reason': 'report 12'}, **auth(mod))
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(res.json()['data']['results'][0]['area'], 'Ikeja GRA')
        log = AdminAction.objects.get(action_type='search_location_history')
        self.assertEqual((log.admin_id, log.reason, log.metadata['results']), (mod.user_id, 'report 12', 1))

    def test_a_stranger_and_a_member_cannot_reach_it(self):
        self.assertIn(self.client.get(self.url_admin, {'person': 'tg_la', 'reason': 'x' * 6})
                      .status_code, (401, 403))
        self.assertIn(self.client.get(self.url_admin, {'person': 'tg_la', 'reason': 'x' * 6},
                                      **auth(self.ada)).status_code, (401, 403))


class ExportTests(TogetherBase):
    def test_the_export_carries_every_departure_area(self):
        ada = person('tg_ex', 30)
        self.ticket(ada)
        self.me(ada, attendance_visibility='event', departure_visibility='mutuals', departure_area='Yaba')
        self.me(ada, departure_area='Ebute Metta')
        res = self.client.get('/setting/export/', **auth(ada))
        self.assertEqual(res.status_code, 200)
        import json
        body = json.loads(res.content)
        areas = [r['area'] for r in body['going_together']['departure_areas']]
        self.assertEqual(sorted(areas), ['Ebute Metta', 'Yaba'])


class AdultsOnlyEventTests(TogetherBase):
    def test_the_switch_is_kept_edited_and_shown(self):
        from .views import _min_age_from
        self.assertEqual([_min_age_from(v) for v in (18, '18', True, 'true', 0, '', None, 16)],
                         [18, 18, 18, 18, 0, 0, 0, 0])
        res = self.client.put('/event/edit-event/%s/' % self.event.event_id, {'min_age': 18},
                              content_type='application/json', **auth(self.org))
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        self.event.refresh_from_db()
        self.assertEqual(self.event.min_age, 18)
        page = self.client.get('/event/view-event/%s/' % self.event.slug).json()['data']
        self.assertEqual((page.get('event') or page).get('min_age'), 18)
