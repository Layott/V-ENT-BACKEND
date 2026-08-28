"""The door.

Two faults that show up at a busy entrance and nowhere else.

"Already checked in at 19:42" does not say WHERE or WHO, so a steward reading it
has to escalate: they cannot tell a duplicate from the same person returning
from a smoke break from their colleague scanning the same phone a second
earlier.

And only the event creator could check anybody in, although `EventManager` has
had a `door` role since it was written. An organiser cannot stand at every gate.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from .models import Event, EventManager, Ticket, TicketTier


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('d-%s' % name)[:16], is_active=True, **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class DoorTests(TestCase):
    def setUp(self):
        self.organiser, self.organiser_auth = a_user('door_organiser')
        self.steward, self.steward_auth = a_user('door_steward')
        self.stranger, self.stranger_auth = a_user('door_stranger')
        self.holder, _ = a_user('door_holder')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Door Probe', creator=self.organiser, event_type='physical',
            desc='A door.', entry_fee=0,
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=5),
            reg_start_date=now - timedelta(days=2),
            reg_end_date=now + timedelta(hours=4),
        )
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)
        self.ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, user=self.holder,
            code='VT-DOOR0001', price_vc=0, attendee_name='Chidi Okeke')

    def check_in(self, auth=None, gate=None, code=None):
        body = {'gate': gate} if gate else {}
        return self.client.post(
            '/event/ticket/%s/check-in/' % (code or self.ticket.code),
            data=body, content_type='application/json',
            **(auth if auth is not None else self.organiser_auth))

    # ------------------------------------------------------------- who may

    def test_the_organiser_checks_somebody_in(self):
        res = self.check_in(gate='Main Gate')
        self.assertEqual(res.status_code, 200, res.content)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'checked_in')
        self.assertEqual(self.ticket.checked_in_gate, 'Main Gate')

    def test_door_staff_can_check_in(self):
        """EventManager has had a `door` role since it was written and this path
        never consulted it, so in practice one person scanned."""
        EventManager.objects.create(
            event=self.event, user=self.steward, role='door')
        res = self.check_in(auth=self.steward_auth, gate='Gate B')
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_manager_can_check_in(self):
        EventManager.objects.create(
            event=self.event, user=self.steward, role='manager')
        self.assertEqual(self.check_in(auth=self.steward_auth).status_code, 200)

    def test_a_stranger_cannot(self):
        res = self.check_in(auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'valid')

    # ------------------------------------------------------- the duplicate

    def test_a_second_scan_says_when_and_where_and_who(self):
        """The whole point. "Already scanned" sends a steward to a supervisor;
        this lets them decide at the gate."""
        self.check_in(gate='Gate B')
        again = self.check_in(gate='Main Gate')

        self.assertEqual(again.status_code, 409, again.content)
        body = again.json()
        self.assertEqual(body['code'], 'ALREADY_CHECKED_IN')
        self.assertIn('Gate B', body['message'])
        self.assertIn('door_organiser', body['message'])

        # `extra` on this module's _error goes into `data`, not the top level.
        first = body['data']['first_used']
        self.assertEqual(first['gate'], 'Gate B')
        self.assertEqual(first['by'], 'door_organiser')
        self.assertEqual(first['attendee_name'], 'Chidi Okeke')
        self.assertIsNotNone(first['at'])

    def test_the_duplicate_message_survives_a_check_in_with_no_gate_recorded(self):
        """Older tickets have no gate. The message still has to read."""
        self.check_in()
        again = self.check_in()
        self.assertEqual(again.status_code, 409, again.content)
        self.assertIn('Already checked in at', again.json()['message'])

    def test_a_second_scan_does_not_move_the_first_time(self):
        self.check_in(gate='Gate B')
        self.ticket.refresh_from_db()
        first_at = self.ticket.checked_in_at
        self.check_in(gate='Main Gate')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.checked_in_at, first_at)
        self.assertEqual(self.ticket.checked_in_gate, 'Gate B')

    # ------------------------------------------------------------- refusals

    def test_an_unknown_code_is_a_404(self):
        self.assertEqual(self.check_in(code='VT-NOTREAL').status_code, 404)

    def test_a_refunded_ticket_is_refused(self):
        self.ticket.status = 'refunded'
        self.ticket.save(update_fields=['status'])
        res = self.check_in()
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'INVALID_TICKET')


class DoorListTests(TestCase):
    """What the scanner downloads before the gates open.

    Two faults, both the same shape as the check-in ones: door staff could not
    load it at all, and it did not carry where a ticket was first used - which
    is half of what the duplicate warning exists to say.
    """

    def setUp(self):
        self.organiser, self.organiser_auth = a_user('list_organiser')
        self.steward, self.steward_auth = a_user('list_steward')
        self.holder, _ = a_user('list_holder')
        now = timezone.now()
        self.event = Event.objects.create(
            name='List Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=5),
            reg_start_date=now - timedelta(days=2),
            reg_end_date=now + timedelta(hours=4))
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=50)
        self.ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, user=self.holder,
            code='VT-LIST0001', price_vc=0, attendee_name='Amara Obi',
            status='checked_in', checked_in_at=now - timedelta(minutes=40),
            checked_in_gate='Gate B', checked_in_by=self.organiser)

    def url(self):
        return '/event/%s/attendees/' % self.event.event_id

    def test_the_list_carries_where_and_by_whom(self):
        """Without these the scanner can only say "already scanned"."""
        res = self.client.get(self.url(), **self.organiser_auth)
        self.assertEqual(res.status_code, 200, res.content)
        row = res.json()['data']['attendees'][0]
        self.assertEqual(row['checked_in_gate'], 'Gate B')
        self.assertEqual(row['checked_in_by'], 'list_organiser')

    def test_door_staff_can_download_it(self):
        """A steward who cannot load the list cannot scan at all."""
        EventManager.objects.create(
            event=self.event, user=self.steward, role='door')
        res = self.client.get(self.url(), **self.steward_auth)
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_stranger_still_cannot(self):
        stranger, stranger_auth = a_user('list_stranger')
        res = self.client.get(self.url(), **stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)
