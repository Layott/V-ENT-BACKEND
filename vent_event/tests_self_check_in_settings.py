# -*- coding: utf-8 -*-
"""Turning self check-in on, which nobody could do until now.

`views_self_check_in.self_check_in` is 194 lines of careful work: a window, a
row lock so two taps cannot admit twice, and a second factor that handles guests
by the email on the ticket, which matters because 1336 of the 1422 tickets at
RIVALRY SERIES SEASON 2 were guest checkout.

All of it was unreachable. `Event.self_check_in` defaults to False, and the
settings view was `@api_view(['GET'])`, so there was no write endpoint and no
screen. That is inbox row 47 - "26 endpoints with no screen able to reach them" -
turning out to contain a whole feature rather than a list of dead ends.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import Event, EventManager, TicketTier, Ticket
from .tests_door_lookup import a_user, _auth


class SettingsFixture(TestCase):
    def setUp(self):
        self.organiser = a_user('sc_organiser')
        self.steward = a_user('sc_steward')
        self.stranger = a_user('sc_stranger')
        now = timezone.now()
        # `self_check_in_window` reads `starts_at`, which combines `event_date`
        # with `start_time` rather than reading `start_date`. Setting only the
        # latter gives a window of (None, None) and every self check-in answers
        # NO_EVENT_TIME, which is a fixture that would have quietly proved
        # nothing.
        #
        # The date comes from the START MOMENT, not from today. Taking
        # `local.date()` with a `start_time` three hours ahead puts the event
        # in the past whenever the suite runs after 21:00, because the time
        # wraps past midnight while the date does not. That is the same
        # rollover `ends_at` exists to handle, and a fixture that gets it wrong
        # fails only in the evening, which is the worst kind of flake.
        starts = timezone.localtime(now) + timedelta(hours=3)
        ends = starts + timedelta(hours=6)
        self.event = Event.objects.create(
            name='Self Check In Probe', creator=self.organiser,
            event_type='physical', desc='Admit yourself.', entry_fee=0,
            start_date=now + timedelta(hours=3),
            end_date=now + timedelta(hours=9),
            reg_start_date=now - timedelta(days=2),
            reg_end_date=now + timedelta(hours=2),
            event_date=starts.date(),
            start_time=starts.time(),
            end_time=ends.time(),
        )
        EventManager.objects.create(event=self.event, user=self.steward,
                                    role='door')
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)

    def settings_url(self, ref=None):
        return '/event/%s/self-check-in/settings/' % (ref or self.event.event_id)

    def post(self, body, user=None, ref=None):
        return self.client.post(self.settings_url(ref), data=body,
                                content_type='application/json',
                                **_auth(user or self.organiser))


class OrganiserWrites(SettingsFixture):

    def test_it_starts_off(self):
        """The state that made the whole feature unreachable."""
        res = self.client.get(self.settings_url())
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['data']['enabled'])

    def test_the_organiser_turns_it_on(self):
        res = self.post({'enabled': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['data']['enabled'])
        self.event.refresh_from_db()
        self.assertTrue(self.event.self_check_in)

    def test_the_organiser_turns_it_off_again(self):
        self.post({'enabled': True})
        res = self.post({'enabled': False})
        self.assertFalse(res.json()['data']['enabled'])
        self.event.refresh_from_db()
        self.assertFalse(self.event.self_check_in)

    def test_the_window_can_be_set(self):
        res = self.post({'enabled': True, 'opens_minutes_before': 45})
        self.assertEqual(res.json()['data']['opens_minutes_before'], 45)
        self.event.refresh_from_db()
        self.assertEqual(self.event.self_check_in_opens_minutes, 45)

    def test_the_window_comes_back_as_real_times(self):
        """A number of minutes is not something an organiser can picture."""
        data = self.post({'enabled': True, 'opens_minutes_before': 60}).json()['data']
        self.assertTrue(data['opens_at'])
        self.assertTrue(data['closes_at'])

    def test_a_silly_window_is_refused_by_code(self):
        res = self.post({'enabled': True, 'opens_minutes_before': 60 * 24 * 30})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_MINUTES')

    def test_a_negative_window_is_refused(self):
        res = self.post({'enabled': True, 'opens_minutes_before': -10})
        self.assertEqual(res.status_code, 400)

    def test_words_where_a_number_belongs_are_refused_not_crashed(self):
        res = self.post({'opens_minutes_before': 'soon'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_MINUTES')

    def test_turning_it_on_actually_lets_somebody_in(self):
        """The proof that the switch reaches the feature behind it.

        Setting a boolean is worth nothing on its own. This walks the whole
        way: the organiser turns it on, and a guest holding a code and the
        email it was sent to admits themselves.
        """
        ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-SELF0001', price_vc=0,
            attendee_name='A Guest', attendee_email='guest@vent.test')

        refused = self.client.post(
            '/event/ticket/VT-SELF0001/self-check-in/',
            data={'email': 'guest@vent.test'}, content_type='application/json')
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()['code'], 'SELF_CHECK_IN_OFF')

        # The window has to be open as well as the switch on: the event starts
        # in three hours, so it opens four hours before.
        self.post({'enabled': True, 'opens_minutes_before': 60 * 4})

        allowed = self.client.post(
            '/event/ticket/VT-SELF0001/self-check-in/',
            data={'email': 'guest@vent.test'}, content_type='application/json')
        self.assertEqual(allowed.status_code, 200, allowed.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'checked_in')
        self.assertEqual(ticket.checked_in_gate, 'self')


class StrangerRefused(SettingsFixture):

    def test_a_stranger_cannot_turn_it_on(self):
        res = self.post({'enabled': True}, user=self.stranger)
        self.assertEqual(res.status_code, 403)
        self.event.refresh_from_db()
        self.assertFalse(self.event.self_check_in)

    def test_door_staff_cannot_turn_it_on(self):
        """Admitting people is not deciding that people admit themselves.

        A steward works one gate. This setting removes the gate, which is the
        organiser's call and nobody else's.
        """
        res = self.post({'enabled': True}, user=self.steward)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_cannot_turn_it_on(self):
        res = self.client.post(self.settings_url(), data={'enabled': True},
                               content_type='application/json')
        self.assertEqual(res.status_code, 401)
        self.event.refresh_from_db()
        self.assertFalse(self.event.self_check_in)

    def test_reading_it_stays_open_to_everybody(self):
        """The event page asks this before deciding whether to show a control.

        It carries nothing private - whether a door lets people admit
        themselves is visible to anybody standing at it - and gating the read
        would mean a signed-out visitor could not be told what to expect.
        """
        self.assertEqual(self.client.get(self.settings_url()).status_code, 200)


class SelfIsDistinguishable(SettingsFixture):

    def setUp(self):
        super().setUp()
        self.event.self_check_in = True
        self.event.self_check_in_opens_minutes = 60 * 4
        self.event.save(update_fields=['self_check_in',
                                       'self_check_in_opens_minutes'])

    def test_the_attendee_list_says_who_admitted_themselves(self):
        at_door = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-SELF0010', price_vc=0,
            attendee_name='Door Person', attendee_email='door@vent.test')
        at_door.status = 'checked_in'
        at_door.checked_in_at = timezone.now()
        at_door.checked_in_gate = 'Main'
        at_door.save(update_fields=['status', 'checked_in_at',
                                    'checked_in_gate'])

        Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-SELF0011', price_vc=0,
            attendee_name='Self Person', attendee_email='self@vent.test')
        self.client.post('/event/ticket/VT-SELF0011/self-check-in/',
                         data={'email': 'self@vent.test'},
                         content_type='application/json')

        res = self.client.get('/event/%d/attendees/' % self.event.event_id,
                              **_auth(self.organiser))
        rows = {r['code']: r for r in res.json()['data']['attendees']}
        self.assertFalse(rows['VT-SELF0010']['self_check_in'])
        self.assertTrue(rows['VT-SELF0011']['self_check_in'])

    def test_the_summary_counts_them_apart(self):
        Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-SELF0020', price_vc=0,
            attendee_name='Self Person', attendee_email='self2@vent.test')
        self.client.post('/event/ticket/VT-SELF0020/self-check-in/',
                         data={'email': 'self2@vent.test'},
                         content_type='application/json')

        data = self.client.get('/event/%d/door-summary/' % self.event.event_id,
                               **_auth(self.organiser)).json()['data']
        self.assertEqual(data['self_admitted'], 1)
        self.assertEqual(data['at_the_door'], 0)


class BySlug(SettingsFixture):

    def test_reading_by_slug(self):
        res = self.client.get(self.settings_url(self.event.slug))
        self.assertEqual(res.status_code, 200)

    def test_writing_by_slug(self):
        res = self.post({'enabled': True}, ref=self.event.slug)
        self.assertEqual(res.status_code, 200, res.content)
        self.event.refresh_from_db()
        self.assertTrue(self.event.self_check_in)
