"""An attendee admitting themselves.

The part worth pinning is not the happy path, it is everything standing in for
a steward: the switch being off by default, the window, and the fact that
holding the code is not on its own enough.
"""
from datetime import date, time, timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from .models import Event, Ticket, TicketTier


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('s-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class SelfCheckInBase(TestCase):
    def setUp(self):
        self.organiser, self.org_auth = a_user('sci_org')
        self.game = Games.objects.create(game_title='EA FC SCI')
        # Starting in an hour, so the default two hour window is open now.
        soon = timezone.localtime(timezone.now()) + timedelta(hours=1)
        self.event = Event.objects.create(
            name='Self Check Probe', game=self.game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now() - timedelta(days=2),
            reg_end_date=timezone.now() + timedelta(days=2),
            event_date=soon.date(), start_time=soon.time(),
            end_time=time(23, 59), location='Lagos',
            self_check_in=True)
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)
        self.ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, code='SCI0000001',
            attendee_name='Ada', attendee_email='ada@example.com')

    def state(self, code=None):
        return self.client.get('/event/ticket/%s/self-check-in/'
                               % (code or self.ticket.code))

    def arrive(self, email='ada@example.com', code=None, auth=None):
        return self.client.post(
            '/event/ticket/%s/self-check-in/' % (code or self.ticket.code),
            {'email': email} if email is not None else {},
            content_type='application/json', **(auth or {}))


class SelfCheckInTests(SelfCheckInBase):
    def test_a_guest_can_admit_themselves(self):
        res = self.arrive()
        self.assertEqual(res.status_code, 200, res.data)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'checked_in')

    def test_it_is_recorded_as_a_self_check_in(self):
        # The organiser has to be able to tell the two apart. It is the
        # difference between a number they can act on and one they cannot.
        self.arrive()
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.checked_in_gate, 'self')
        self.assertIsNone(self.ticket.checked_in_by)

    def test_the_owner_signed_in_needs_no_email(self):
        owner, auth = a_user('sci_owner')
        self.ticket.user = owner
        self.ticket.attendee_email = ''
        self.ticket.save()
        res = self.arrive(email=None, auth=auth)
        self.assertEqual(res.status_code, 200, res.data)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.checked_in_by_id, owner.user_id)

    def test_the_code_alone_is_not_enough(self):
        # A code is a thing people screenshot into group chats.
        res = self.arrive(email='someone@else.test')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'EMAIL_MISMATCH')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'valid')

    def test_the_email_is_matched_case_insensitively(self):
        res = self.arrive(email='  ADA@Example.COM ')
        self.assertEqual(res.status_code, 200, res.data)

    def test_a_ticket_with_no_email_goes_to_the_door(self):
        self.ticket.attendee_email = ''
        self.ticket.save()
        res = self.arrive(email='')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'NO_EMAIL_ON_TICKET')

    def test_it_only_works_once(self):
        self.assertEqual(self.arrive().status_code, 200)
        again = self.arrive()
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.data['code'], 'ALREADY_CHECKED_IN')

    def test_an_unknown_code_is_a_404(self):
        self.assertEqual(self.arrive(code='NOTATICKET').status_code, 404)

    def test_a_refunded_ticket_admits_nobody(self):
        self.ticket.status = 'refunded'
        self.ticket.save()
        res = self.arrive()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'TICKET_NOT_VALID')


class SelfCheckInWindowTests(SelfCheckInBase):
    def test_it_is_off_unless_the_organiser_turns_it_on(self):
        # Somebody who can admit themselves can do it from home.
        self.event.self_check_in = False
        self.event.save()
        res = self.arrive()
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'SELF_CHECK_IN_OFF')

    def test_a_new_event_has_it_off(self):
        fresh = Event.objects.create(
            name='Default Probe', game=self.game, creator=self.organiser,
            event_type='physical', desc='d', entry_fee=0,
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            event_date=date.today(), start_time=time(18, 0), end_time=time(21, 0))
        self.assertFalse(fresh.self_check_in)

    def test_too_early_is_refused_with_the_time_it_opens(self):
        far = timezone.localtime(timezone.now()) + timedelta(days=3)
        self.event.event_date = far.date()
        self.event.start_time = far.time()
        self.event.save()
        res = self.arrive()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'TOO_EARLY')
        self.assertTrue(res.data['data']['opens_at'])

    def test_after_the_event_ends_it_is_closed(self):
        past = timezone.localtime(timezone.now()) - timedelta(days=2)
        self.event.event_date = past.date()
        self.event.start_time = time(9, 0)
        self.event.end_time = time(12, 0)
        self.event.save()
        res = self.arrive()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'TOO_LATE')

    def test_arriving_late_on_the_day_still_counts(self):
        # It closes at the END of the event, not the start. Somebody arriving
        # late is still somebody who came.
        #
        # The date is taken from when the event STARTED, not from now. Run this
        # at 00:55 and `now - 2h` is 22:55 yesterday: filing that under today's
        # date describes an event running 22:55 tonight to 02:55 tomorrow, which
        # has not begun, and the check-in is correctly refused as too early. The
        # code was right and the test was reading the clock badly - which is the
        # kind of failure that teaches people to ignore a suite, because it only
        # appears between midnight and two in the morning.
        now = timezone.localtime(timezone.now())
        started = now - timedelta(hours=2)
        self.event.event_date = started.date()
        self.event.start_time = started.time()
        self.event.end_time = (now + timedelta(hours=2)).time()
        self.event.save()
        self.assertEqual(self.arrive().status_code, 200)

    def test_an_event_running_past_midnight_has_a_window(self):
        # 21:00 to 02:00 ends the next day. Compared numerically it would end
        # five hours before it began, and the window would never be open.
        now = timezone.localtime(timezone.now())
        self.event.event_date = now.date()
        self.event.start_time = time(21, 0)
        self.event.end_time = time(2, 0)
        self.event.save()
        opens, closes = self.event.self_check_in_window()
        self.assertGreater(closes, opens)
        self.assertEqual(closes.date(), now.date() + timedelta(days=1))

    def test_the_state_endpoint_says_why_before_anybody_types(self):
        self.event.self_check_in = False
        self.event.save()
        res = self.state()
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['data']['may_check_in'])
        self.assertEqual(res.data['data']['reason'], 'SELF_CHECK_IN_OFF')

    def test_the_state_endpoint_says_yes_when_it_is_open(self):
        res = self.state()
        self.assertTrue(res.data['data']['may_check_in'])
        self.assertEqual(res.data['data']['reason'], '')

    def test_the_settings_endpoint_reports_the_window(self):
        res = self.client.get('/event/%s/self-check-in/settings/'
                              % self.event.event_id)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['data']['enabled'])
        self.assertEqual(res.data['data']['opens_minutes_before'], 120)

    def test_the_organiser_turns_it_on_through_edit(self):
        self.event.self_check_in = False
        self.event.save()
        res = self.client.put(
            '/event/edit-event/%s/' % self.event.event_id,
            {'self_check_in': True, 'self_check_in_opens_minutes': 45},
            content_type='application/json', **self.org_auth)
        self.assertEqual(res.status_code, 200, res.data)
        self.event.refresh_from_db()
        self.assertTrue(self.event.self_check_in)
        self.assertEqual(self.event.self_check_in_opens_minutes, 45)

    def test_the_organiser_can_turn_it_back_off(self):
        # `False` is a value the organiser is expressing, not an absent field.
        res = self.client.put(
            '/event/edit-event/%s/' % self.event.event_id,
            {'self_check_in': False},
            content_type='application/json', **self.org_auth)
        self.assertEqual(res.status_code, 200, res.data)
        self.event.refresh_from_db()
        self.assertFalse(self.event.self_check_in)
