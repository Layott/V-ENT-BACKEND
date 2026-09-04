"""One door, several days.

CEO, 4 September 2026, at the Rivalry Series: "maybe there should also be
different scanners for different days. so that people dont come and show day 2
tickets on day one and its work because tehre is just one scanner."

`TicketTier.day` has carried the date since tiers were written, and nothing had
ever read it at the door. A Saturday ticket opened Friday's gate, and the only
thing between a two day event and being walked through twice was a steward
reading the tier name off a phone screen.

Two rules, and the second is the one that keeps existing events working:

  * a tier WITH a day admits only on that day
  * a tier WITHOUT one admits on any day, because that is what a single day
    event and a full run pass both are
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from .models import Event, Ticket, TicketTier
from .views_tickets import _new_code


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('day-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class DayDoorTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('day_org')
        now = timezone.now()
        self.today = timezone.localdate()
        self.tomorrow = self.today + timedelta(days=1)

        self.event = Event.objects.create(
            name='Two Day Series', slug='two-day-series', creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0,
            start_date=now, end_date=now + timedelta(days=1))

        self.day_one = TicketTier.objects.create(
            event=self.event, name='Day 1', price=0, quantity=100,
            day=self.today, day_label='Day 1')
        self.day_two = TicketTier.objects.create(
            event=self.event, name='Day 2', price=0, quantity=100,
            day=self.tomorrow, day_label='Day 2')
        self.whole_run = TicketTier.objects.create(
            event=self.event, name='Weekend pass', price=0, quantity=100)

    def ticket(self, tier, name='Somebody'):
        return Ticket.objects.create(
            event=self.event, tier=tier, code=_new_code(),
            attendee_name=name, attendee_email='x@vent.test', status='valid')

    def scan(self, ticket, day=None):
        body = {'gate': 'Main'}
        if day is not None:
            body['day'] = day
        return self.client.post('/event/ticket/%s/check-in/' % ticket.code,
                                data=body, content_type='application/json',
                                **self.auth)

    # ------------------------------------------------------------- the rule

    def test_todays_ticket_opens_todays_door(self):
        res = self.scan(self.ticket(self.day_one))
        self.assertEqual(res.status_code, 200, res.content)

    def test_tomorrows_ticket_does_not_open_todays_door(self):
        """The whole reason this exists. Before it, this answered 200."""
        res = self.scan(self.ticket(self.day_two, 'Early bird'))
        self.assertEqual(res.status_code, 409)
        body = res.json()
        self.assertEqual(body['code'], 'WRONG_DAY')
        self.assertEqual(body['data']['ticket_day'], self.tomorrow.isoformat())
        self.assertEqual(body['data']['ticket_day_label'], 'Day 2')
        self.assertEqual(body['data']['scanning_day'], self.today.isoformat())

    def test_a_refused_ticket_is_not_marked_used(self):
        """A wrong day must not burn the ticket. They come back tomorrow."""
        ticket = self.ticket(self.day_two)
        self.scan(ticket)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'valid')
        self.assertIsNone(ticket.checked_in_at)

    def test_a_tier_with_no_day_admits_on_any_day(self):
        """Every event that exists today has tiers with no day on them. If this
        narrowed them the fix would close every door on the platform."""
        res = self.scan(self.ticket(self.whole_run))
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_door_can_be_pinned_to_another_day(self):
        """A steward testing Saturday's gate on Friday night, and the real case:
        two scanners running side by side at a two day event."""
        res = self.scan(self.ticket(self.day_two), day=self.tomorrow.isoformat())
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_pinned_door_refuses_the_other_day(self):
        res = self.scan(self.ticket(self.day_one), day=self.tomorrow.isoformat())
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'WRONG_DAY')

    def test_a_day_that_cannot_be_read_is_refused_rather_than_ignored(self):
        """Ignoring it would open every door, which is the failure that hurts."""
        res = self.scan(self.ticket(self.day_one), day='saturday')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_DAY')

    # --------------------------------------------------------- the door list

    def test_the_door_list_carries_each_ticket_s_day(self):
        """Without it a scanner with no network cannot apply the rule at all,
        and the offline half of this feature is the half that matters at a
        venue."""
        self.ticket(self.day_two, 'Tomorrow person')
        res = self.client.get('/event/two-day-series/attendees/', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        row = res.json()['data']['attendees'][0]
        self.assertEqual(row['tier_day'], self.tomorrow.isoformat())
        self.assertEqual(row['tier_day_label'], 'Day 2')

    def test_a_whole_run_ticket_says_so_on_the_door_list(self):
        self.ticket(self.whole_run, 'Weekend person')
        res = self.client.get('/event/two-day-series/attendees/', **self.auth)
        row = [r for r in res.json()['data']['attendees']
               if r['attendee_name'] == 'Weekend person'][0]
        self.assertIsNone(row['tier_day'])
