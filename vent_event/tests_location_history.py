"""The admin location-history search (inbox 305b, 305c, 596; 12 November 2026).

Forgiving search by person, event, area and day; a row that says what the door
saw; one person's whole history; every search and every history opened in
AdminAction with what it showed; "who has looked" scoped like the audit log;
and the doors closed to anybody without `view_location_history`.
"""
from datetime import time, timedelta

from django.utils import timezone

from vent_auth.models import AdminAction, Games

from .models import DepartureRecord, Event, Ticket
from .tests_together import TogetherBase, auth, person

URL = '/event/admin/location-history/'
REASON = 'report 4471'


class LocationHistoryBase(TogetherBase):
    def setUp(self):
        super().setUp()
        game = Games.objects.create(game_title='Tekken LH')
        when = timezone.localtime(timezone.now()) + timedelta(days=3)
        self.con = Event.objects.create(
            name='Lagos Anime Con', game=game, creator=self.org, event_type='physical',
            desc='probe', entry_fee=0, reg_start_date=timezone.now() - timedelta(days=1),
            reg_end_date=timezone.now() + timedelta(days=2), event_date=when.date(),
            start_time=when.time(), end_time=time(23, 59), location='Landmark, Lagos')
        self.ada = person('lh_ada', 30)
        self.ada.full_name, self.ada.email = 'Adaeze Okafor', 'ada.okafor@vent.test'
        self.ada.save(update_fields=['full_name', 'email'])
        self.bayo = person('lh_bayo', 28)
        self.bayo.full_name = 'Bayo Lawal'
        self.bayo.save(update_fields=['full_name'])
        self.mod = person('lh_mod', 40, admin_role='mod_admin')
        self.now = timezone.now()

    def record(self, user, area, event=None, days_ago=0):
        r = DepartureRecord.objects.create(event=event or self.event, user=user, area=area,
                                           visibility='mutuals')
        DepartureRecord.objects.filter(pk=r.pk).update(set_at=self.now - timedelta(days=days_ago))
        return r

    def search(self, who=None, **params):
        params.setdefault('reason', REASON)
        return self.client.get(URL, params, **auth(who or self.mod))

    def found(self, **params):
        res = self.search(**params)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']


class PersonSearchTests(LocationHistoryBase):
    def setUp(self):
        super().setUp()
        self.record(self.ada, 'Ikeja GRA')
        self.record(self.bayo, 'Yaba')

    def test_person_by_part_of_the_name_a_typo_an_email_and_the_username(self):
        for query in ('adaez', 'Adaeze Okafr', 'ada.okafor@vent.test', '@lh_ada', 'LH_ADA'):
            names = {r['person']['username'] for r in self.found(person=query)['results']}
            self.assertEqual(names, {'lh_ada'}, query)

    def test_person_nobody_matches_finds_nothing_and_says_so(self):
        data = self.found(person='zzqxv')
        self.assertEqual((data['results'], data['total'], data['people']), ([], 0, []))


class SearchTermsTests(LocationHistoryBase):
    def setUp(self):
        super().setUp()
        self.record(self.ada, 'Lekki Phase 1', days_ago=1)
        self.record(self.bayo, 'Lekki', event=self.con, days_ago=10)
        self.record(self.bayo, 'Yaba', days_ago=20)

    def test_search_event_by_name_or_by_its_address(self):
        for query in ('lagos anime', 'Lagos Anime Con', self.con.slug):
            areas = [r['area'] for r in self.found(event=query)['results']]
            self.assertEqual(areas, ['Lekki'], query)

    def test_search_area_by_its_words_best_match_first(self):
        areas = [r['area'] for r in self.found(area='lekki')['results']]
        self.assertEqual(areas, ['Lekki', 'Lekki Phase 1'])
        self.assertNotIn('Yaba', [r['area'] for r in self.found(area='lekki')['results']])

    def test_search_days_narrow_from_inclusive_to_exclusive(self):
        since = (self.now - timedelta(days=15)).isoformat()
        until = (self.now - timedelta(days=5)).isoformat()
        areas = [r['area'] for r in self.found(person='lh_bayo', **{'from': since, 'to': until})['results']]
        self.assertEqual(areas, ['Lekki'])

    def test_search_needs_a_term_a_reason_ordered_days_and_readable_days(self):
        self.assertEqual(self.search(**{'from': self.now.isoformat()}).json()['code'], 'SEARCH_TERM_REQUIRED')
        self.assertEqual(self.search(person='lh_ada', reason='abc').json()['code'], 'REASON_REQUIRED')
        later, earlier = self.now.isoformat(), (self.now - timedelta(days=1)).isoformat()
        self.assertEqual(self.search(person='lh_ada', **{'from': later, 'to': earlier}).json()['code'],
                         'DATES_OUT_OF_ORDER')
        self.assertEqual(self.search(person='lh_ada', **{'from': 'last tuesday'}).json()['code'],
                         'INVALID_INPUT')
        self.assertFalse(AdminAction.objects.filter(action_type='search_location_history').exists(),
                         'a refused search showed nothing, so nothing to record')

    def test_search_reaches_an_event_that_was_deleted(self):
        self.con.deleted_at = timezone.now()
        self.con.save(update_fields=['deleted_at'])
        rows = self.found(event=self.con.slug)['results']
        self.assertEqual(([r['area'] for r in rows], rows[0]['event']['deleted']), (['Lekki'], True))


class RowTests(LocationHistoryBase):
    def test_row_says_the_event_the_area_whether_it_is_the_latest_and_the_door(self):
        self.record(self.ada, 'Ikeja', days_ago=2)
        self.record(self.ada, 'Ikeja GRA', days_ago=1)
        self.record(self.bayo, 'Yaba')
        carl, dan = person('lh_carl', 30), person('lh_dan', 30)
        self.record(carl, 'Surulere')
        self.record(dan, 'Ajah')
        at = self.now - timedelta(hours=3)
        Ticket.objects.create(event=self.event, tier=self.tier, user=self.ada, code='LHIN0001',
                              status='checked_in', checked_in_at=at, checked_in_gate='Gate B')
        self.ticket(self.bayo)
        Ticket.objects.create(event=self.event, tier=self.tier, user=dan, code='LHRF0001', status='refunded')
        rows = {(r['person']['username'], r['area']): r for r in self.found(event=self.event.slug)['results']}
        newest = rows[('lh_ada', 'Ikeja GRA')]
        self.assertEqual(set(newest), {'id', 'person', 'event', 'area', 'visibility', 'set_at', 'latest', 'door'})
        self.assertEqual(newest['event']['name'], 'Together Probe')
        self.assertEqual((newest['latest'], rows[('lh_ada', 'Ikeja')]['latest']), (True, False))
        self.assertEqual((newest['door']['state'], newest['door']['gate']), ('in', 'Gate B'))
        self.assertIsNotNone(newest['door']['at'])
        self.assertEqual(rows[('lh_bayo', 'Yaba')]['door']['state'], 'ticket')
        self.assertEqual(rows[('lh_carl', 'Surulere')]['door']['state'], 'none')
        self.assertEqual(rows[('lh_dan', 'Ajah')]['door']['state'], 'none', 'a refund is not a ticket')

    def test_row_pages_fifty_at_a_time(self):
        for i in range(60):
            self.record(self.ada, 'Area %d' % i)
        first = self.found(person='lh_ada')
        self.assertEqual((len(first['results']), first['total'], first['has_more']), (50, 60, True))
        second = self.found(person='lh_ada', page=2)
        self.assertEqual((len(second['results']), second['has_more']), (10, False))
        self.assertFalse({r['id'] for r in first['results']} & {r['id'] for r in second['results']})


class PeopleTests(LocationHistoryBase):
    def test_people_a_person_search_matched_with_their_counts(self):
        twin = person('lh_adanna', 30)
        self.record(self.ada, 'Ikeja')
        self.record(self.ada, 'Ikeja GRA')
        self.record(twin, 'Yaba')
        self.record(self.bayo, 'Lekki')
        people = {p['person']['username']: p['records'] for p in self.found(person='lh_ada')['people']}
        self.assertEqual(people.get('lh_ada'), 2)
        self.assertEqual(people.get('lh_adanna'), 1)
        self.assertNotIn('lh_bayo', people)
        self.assertIsNone(self.found(area='lekki')['people'], 'no person asked for, no people list')


class HistoryTests(LocationHistoryBase):
    def test_history_of_one_person_every_event_and_it_is_logged_as_a_view(self):
        self.record(self.ada, 'Ikeja', days_ago=5)
        self.record(self.ada, 'Lekki', event=self.con, days_ago=1)
        self.record(self.bayo, 'Yaba')
        url = URL + 'person/lh_ada/'
        self.assertEqual(self.client.get(url, **auth(self.mod)).json()['code'], 'REASON_REQUIRED')
        res = self.client.get(url, {'reason': REASON}, **auth(self.mod))
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertEqual(([r['area'] for r in data['results']], data['events'], data['total']),
                         (['Lekki', 'Ikeja'], 2, 2))
        log = AdminAction.objects.get(action_type='view_location_history')
        self.assertEqual((log.admin_id, log.reason, log.target_model, log.target_id),
                         (self.mod.user_id, REASON, 'Users', 'lh_ada'))
        self.assertEqual(sorted(log.metadata['shown']), sorted(r['id'] for r in data['results']))

    def test_history_of_nobody_is_a_coded_404(self):
        res = self.client.get(URL + 'person/lh_nobody/', {'reason': REASON}, **auth(self.mod))
        self.assertEqual((res.status_code, res.json()['code']), (404, 'PERSON_NOT_FOUND'))


class LoggedTests(LocationHistoryBase):
    def test_logged_every_search_with_its_terms_and_what_it_showed(self):
        a = self.record(self.ada, 'Ikeja')
        self.found(person='adaeze', area='ikeja')
        self.found(person='zzqxv')
        hit, miss = AdminAction.objects.filter(action_type='search_location_history').order_by('id')
        self.assertEqual((hit.admin_id, hit.reason), (self.mod.user_id, REASON))
        self.assertEqual((hit.metadata['person'], hit.metadata['area'], hit.metadata['results'],
                          hit.metadata['shown']), ('adaeze', 'ikeja', 1, [a.id]))
        self.assertEqual((miss.metadata['results'], miss.metadata['shown']), (0, []))


class LookedTests(LocationHistoryBase):
    def test_looked_super_admin_sees_everybody_others_their_own(self):
        boss = person('lh_boss', 45, admin_role='super_admin')
        self.record(self.ada, 'Ikeja')
        self.found(person='lh_ada')
        self.client.get(URL + 'person/lh_ada/', {'reason': 'second look'}, **auth(boss))
        AdminAction.objects.create(admin=boss, action_type='ban_user', target_model='Users',
                                   target_id='9', reason='not a lookup')
        mine = self.client.get(URL + 'log/', **auth(self.mod)).json()['data']
        self.assertEqual([(r['admin']['username'], r['kind']) for r in mine['results']], [('lh_mod', 'search')])
        self.assertEqual((mine['results'][0]['terms'], mine['results'][0]['results'], mine['everyone']),
                         ({'person': 'lh_ada'}, 1, False))
        every = self.client.get(URL + 'log/', **auth(boss)).json()['data']
        self.assertEqual([(r['admin']['username'], r['kind']) for r in every['results']],
                         [('lh_boss', 'history'), ('lh_mod', 'search')])
        self.assertTrue(every['everyone'])


class RefuseTests(LocationHistoryBase):
    def test_refuse_signed_out_a_member_and_an_admin_role_without_the_permission(self):
        self.record(self.ada, 'Ikeja')
        general = person('lh_admin', 40, admin_role='admin')
        finance = person('lh_fin', 40, admin_role='finance_admin')
        doors = [(URL, {'person': 'lh_ada', 'reason': REASON}),
                 (URL + 'person/lh_ada/', {'reason': REASON}),
                 (URL + 'log/', {})]
        for url, params in doors:
            self.assertIn(self.client.get(url, params).status_code, (401, 403), url)
            for who in (self.ada, general, finance):
                self.assertIn(self.client.get(url, params, **auth(who)).status_code, (401, 403),
                              (url, who.username))
        self.assertFalse(AdminAction.objects.filter(
            action_type__in=('search_location_history', 'view_location_history')).exists())
