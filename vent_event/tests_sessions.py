"""The programme.

The Schedule tab used to be a function that invented a two-day programme from
the event's start date, so every event showed the same "Cosplay parade". These
are the tests for what replaces it.

A session carries its own capacity, which is the reason to have sessions rather
than a list of times: a convention holding 900 has a panel room holding 80.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from .models import Event, EventSession


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('s-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class SessionTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('sess_org')
        self.stranger, self.stranger_auth = a_user('sess_stranger')
        now = timezone.now()
        self.start = (now + timedelta(days=7)).replace(
            hour=10, minute=0, second=0, microsecond=0)
        self.event = Event.objects.create(
            name='Session Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=self.start,
            end_date=self.start + timedelta(days=2),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=6))

    def url(self, suffix=''):
        return '/event/%s/sessions/%s' % (self.event.event_id, suffix)

    def add(self, **body):
        body.setdefault('title', 'Doors open')
        body.setdefault('starts_at', self.start.isoformat())
        return self.client.post(self.url('manage/'), data=body,
                                content_type='application/json', **self.auth)

    # ------------------------------------------------------------- creating

    def test_an_organiser_adds_a_session(self):
        res = self.add(title='Cosplay parade', stage='Centre Stage',
                       capacity=80)
        self.assertEqual(res.status_code, 201, res.content)
        session = res.json()['data']['session']
        self.assertEqual(session['title'], 'Cosplay parade')
        self.assertEqual(session['stage'], 'Centre Stage')
        self.assertEqual(session['capacity'], 80)

    def test_a_session_carries_its_own_capacity(self):
        """The reason to have sessions at all: a convention holding 900 has a
        panel room holding 80."""
        self.event.capacity = 900
        self.event.save(update_fields=['capacity'])
        res = self.add(title='Anime industry panel', capacity=80)
        self.assertEqual(res.json()['data']['session']['capacity'], 80)
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 900)

    def test_a_session_needs_a_title(self):
        self.assertEqual(self.add(title='  ').status_code, 400)

    def test_a_session_needs_a_start(self):
        res = self.client.post(self.url('manage/'), data={'title': 'Nowhen'},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'starts_at')

    def test_ending_before_starting_is_refused(self):
        res = self.add(ends_at=(self.start - timedelta(hours=1)).isoformat())
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'END_BEFORE_START')

    def test_a_capacity_that_is_not_a_number_is_refused(self):
        self.assertEqual(self.add(capacity='eighty').status_code, 400)

    def test_a_stranger_cannot_add_to_the_programme(self):
        res = self.client.post(self.url('manage/'),
                               data={'title': 'Mine', 'starts_at': self.start.isoformat()},
                               content_type='application/json',
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)

    # -------------------------------------------------------------- reading

    def test_the_programme_is_public(self):
        """It is the first thing somebody deciding whether to come wants to
        see, and the most shareable page an event has."""
        self.add(title='Doors open')
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['data']['published'])

    def test_an_event_with_no_programme_says_so_rather_than_inventing_one(self):
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertFalse(data['published'])
        self.assertEqual(data['days'], [])
        self.assertEqual(data['count'], 0)

    def test_sessions_are_grouped_into_days_in_order(self):
        self.add(title='Doors open', starts_at=self.start.isoformat())
        self.add(title='Cosplay parade',
                 starts_at=(self.start + timedelta(hours=2)).isoformat())
        self.add(title='Panel',
                 starts_at=(self.start + timedelta(days=1)).isoformat())

        days = self.client.get(self.url()).json()['data']['days']
        self.assertEqual(len(days), 2)
        self.assertEqual([d['label'] for d in days], ['Day 1', 'Day 2'])
        self.assertEqual(len(days[0]['sessions']), 2)
        self.assertEqual(days[0]['sessions'][0]['title'], 'Doors open')

    def test_a_set_after_midnight_belongs_to_the_night_before(self):
        """1am on Saturday is Friday's programme to everybody who was there."""
        # Doors at 10am, and the after-party at 1am the following calendar day.
        # Those are one night out, and a schedule that splits them into Day 1
        # and Day 2 is wrong to everybody who was there.
        self.add(title='Doors open', starts_at=self.start.isoformat())
        self.add(title='After-party',
                 starts_at=(self.start + timedelta(days=1)).replace(hour=1).isoformat())
        days = self.client.get(self.url()).json()['data']['days']
        self.assertEqual(len(days), 1)
        self.assertEqual([s['title'] for s in days[0]['sessions']],
                         ['Doors open', 'After-party'])

    def test_an_unpublished_session_is_not_public(self):
        self.add(title='Secret set', is_published=False)
        self.assertEqual(self.client.get(self.url()).json()['data']['count'], 0)
        # The organiser still sees it.
        mine = self.client.get(self.url('manage/'), **self.auth)
        self.assertEqual(len(mine.json()['data']['sessions']), 1)

    # -------------------------------------------------------------- editing

    def test_correcting_a_session(self):
        session_id = self.add().json()['data']['session']['id']
        res = self.client.patch(
            self.url('%s/' % session_id),
            data={'title': 'Doors open, actually', 'stage': 'Main Hall'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        session = EventSession.objects.get(pk=session_id)
        self.assertEqual(session.title, 'Doors open, actually')
        self.assertEqual(session.stage, 'Main Hall')

    def test_a_patch_that_changes_nothing_says_so(self):
        session_id = self.add().json()['data']['session']['id']
        res = self.client.patch(self.url('%s/' % session_id), data={},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'NO_FIELDS_TO_UPDATE')

    def test_removing_a_session(self):
        session_id = self.add().json()['data']['session']['id']
        res = self.client.delete(self.url('%s/' % session_id), **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(EventSession.objects.count(), 0)

    def test_a_stranger_cannot_remove_one(self):
        session_id = self.add().json()['data']['session']['id']
        res = self.client.delete(self.url('%s/' % session_id),
                                 **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(EventSession.objects.count(), 1)
