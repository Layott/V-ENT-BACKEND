"""Giving a ticket to somebody else.

CEO, 7 September 2026, from the ticketing research: give a ticket to somebody
else, the code reissues, the door sees the new holder.

The decisions worth pinning:

- **The code reissues.** A ticket keeping its code after being given away means
  the old holder still holds something that opens the gate. Two people, one
  seat, and the one turned away has done nothing wrong.
- **The old code still ANSWERS.** Dead is not unknown. "No such ticket" makes a
  steward think the person in front of them is lying.
- **A checked-in ticket cannot move.** The seat is taken, and moving it would
  make the attendance figures name somebody who was not there.
- **The organiser can do it too.** Somebody who lost their phone cannot reach
  the transfer screen, and the organiser is who they ask at the door.
"""
from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Users

from .models import Event, Ticket, TicketTier, TicketTransfer


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('t-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class TransferBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser, self.org_auth = a_user('tf_org')
        self.holder, self.holder_auth = a_user('tf_holder')
        self.friend, self.friend_auth = a_user('tf_friend')
        self.stranger, self.stranger_auth = a_user('tf_other')
        game = Games.objects.create(game_title='EA FC TF')
        now = timezone.localtime(timezone.now())
        self.event = Event.objects.create(
            name='Transfer Probe', game=game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now() - timedelta(days=5),
            reg_end_date=timezone.now() + timedelta(days=5),
            event_date=(now + timedelta(days=3)).date(),
            start_time=time(18, 0), end_time=time(22, 0),
            start_date=timezone.now() + timedelta(days=3),
            end_date=timezone.now() + timedelta(days=3, hours=4),
            location='Lagos', capacity=100)
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=5000, quantity=50)
        self.ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, user=self.holder,
            code='TF000001', price_vc=5, price_ngn=5000,
            attendee_name='The Holder',
            attendee_email=self.holder.email, attendee_phone='08000000000')

    def give(self, to, auth=None, **body):
        return self.client.post(
            '/event/ticket/%s/transfer/' % self.ticket.code,
            dict(to=to, **body), format='json', **(auth or self.holder_auth))


class TransferTests(TransferBase):
    def test_the_code_reissues(self):
        """A ticket that keeps its code means the old holder still holds
        something that opens the gate."""
        old = self.ticket.code
        res = self.give(self.friend.email)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.ticket.refresh_from_db()
        self.assertNotEqual(self.ticket.code, old)
        self.assertEqual(res.json()['data']['old_code'], old)

    def test_the_old_code_no_longer_finds_a_ticket(self):
        old = self.ticket.code
        self.give(self.friend.email)
        self.assertFalse(Ticket.objects.filter(code=old).exists())

    def test_the_new_holder_is_on_the_ticket(self):
        self.give(self.friend.email, name='Ada Okoro')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.user, self.friend)
        self.assertEqual(self.ticket.attendee_email, self.friend.email)
        self.assertEqual(self.ticket.attendee_name, 'Ada Okoro')

    def test_the_previous_holder_phone_number_does_not_travel_with_it(self):
        """It would put one person's number against another person's name on
        the door list."""
        self.give(self.friend.email)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.attendee_phone, '')

    def test_somebody_with_no_account_can_be_given_one(self):
        res = self.give('nobody@example.com', name='A Friend')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.user_id)
        self.assertEqual(self.ticket.attendee_email, 'nobody@example.com')

    def test_a_trail_is_left(self):
        self.give(self.friend.email)
        record = TicketTransfer.objects.get()
        self.assertEqual(record.from_user, self.holder)
        self.assertEqual(record.to_user, self.friend)
        self.assertEqual(record.old_code, 'TF000001')

    def test_the_organiser_can_transfer_it_too(self):
        """Somebody who lost their phone cannot reach the transfer screen, and
        the organiser is who they will ask at the door."""
        res = self.give(self.friend.email, auth=self.org_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_a_stranger_cannot(self):
        res = self.give(self.friend.email, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_cannot(self):
        res = self.client.post('/event/ticket/%s/transfer/' % self.ticket.code,
                               {'to': self.friend.email}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_a_used_ticket_cannot_be_given_away(self):
        """Moving it would make the attendance figures name somebody who was
        not there, and that is the number next year is decided on."""
        self.ticket.status = 'checked_in'
        self.ticket.checked_in_at = timezone.now()
        self.ticket.save(update_fields=['status', 'checked_in_at'])
        res = self.give(self.friend.email)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ALREADY_USED')

    def test_a_void_ticket_cannot_be_given_away(self):
        self.ticket.status = 'cancelled'
        self.ticket.save(update_fields=['status'])
        res = self.give(self.friend.email)
        self.assertEqual(res.status_code, 409)

    def test_it_cannot_be_given_away_after_the_event(self):
        self.event.end_date = timezone.now() - timedelta(days=1)
        self.event.save(update_fields=['end_date'])
        res = self.give(self.friend.email)
        self.assertEqual(res.status_code, 409)

    def test_giving_it_to_the_person_who_already_holds_it_is_refused(self):
        res = self.give(self.holder.email)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'NO_CHANGE')

    def test_nonsense_is_refused(self):
        res = self.give('this is not an address')
        self.assertEqual(res.status_code, 404)

    def test_the_organiser_ticket_limit_still_applies(self):
        """Otherwise a transfer is how somebody buys eight tickets on a
        four-ticket limit using two accounts."""
        self.event.max_tickets_per_email = 1
        self.event.save(update_fields=['max_tickets_per_email'])
        Ticket.objects.create(event=self.event, tier=self.tier,
                              code='TF000002', price_vc=5, price_ngn=5000,
                              attendee_email=self.friend.email)
        res = self.give(self.friend.email)
        self.assertEqual(res.status_code, 409, res.content[:200])


class TheDoorSeesItTests(TransferBase):
    def test_the_door_names_the_transfer_rather_than_denying_the_code(self):
        """"No such ticket" makes a steward think the person in front of them
        is lying."""
        old = self.ticket.code
        self.give(self.friend.email, name='Ada Okoro')
        res = self.client.get('/event/ticket/%s/lookup/' % old, **self.org_auth)
        self.assertEqual(res.status_code, 409, res.content[:300])
        body = res.json()
        self.assertEqual(body['code'], 'TICKET_TRANSFERRED')
        self.assertEqual(body['data']['to_name'], 'Ada Okoro')

    def test_the_door_never_hands_back_the_working_code(self):
        """That would make this endpoint a way to turn any old screenshot into
        a valid pass."""
        old = self.ticket.code
        self.give(self.friend.email)
        self.ticket.refresh_from_db()
        res = self.client.get('/event/ticket/%s/lookup/' % old, **self.org_auth)
        self.assertNotIn(self.ticket.code, res.content.decode('utf-8'))

    def test_the_new_code_works_at_the_door(self):
        self.give(self.friend.email)
        self.ticket.refresh_from_db()
        res = self.client.get('/event/ticket/%s/lookup/' % self.ticket.code,
                              **self.org_auth)
        self.assertEqual(res.status_code, 200, res.content[:200])

    def test_a_code_that_never_existed_is_still_a_plain_not_found(self):
        res = self.client.get('/event/ticket/VT-NOSUCH1/lookup/', **self.org_auth)
        self.assertEqual(res.status_code, 404)

    def test_EVERY_door_names_the_transfer_not_only_the_lookup(self):
        """The fault, caught on the emulator on 7 September.

        The transferred answer was added to `ticket_lookup` and the scanner
        still said "Not on the list", because the scanner posts to
        `check_in_ticket`. Six endpoints resolve a ticket by code and all six
        have to answer the same way; a code that used to work is dead, not
        unknown, wherever it is presented.

        `tools/check-dead-codes.py` holds the class from here on.
        """
        old = self.ticket.code
        self.give(self.friend.email)

        doors = [
            ('post', '/event/ticket/%s/check-in/' % old),
            ('get', '/event/ticket/%s/lookup/' % old),
            ('post', '/event/ticket/%s/undo-check-in/' % old),
            ('post', '/event/ticket/%s/self-check-in/' % old),
        ]
        for method, url in doors:
            res = (self.client.post(url, {}, format='json', **self.org_auth)
                   if method == 'post'
                   else self.client.get(url, **self.org_auth))
            self.assertEqual(res.status_code, 409, '%s said %s'
                             % (url, res.status_code))
            self.assertEqual(res.json()['code'], 'TICKET_TRANSFERRED', url)
            self.assertTrue(res.json()['data']['now_held_by'], url)


class HistoryTests(TransferBase):
    def test_the_history_answers_for_a_dead_code(self):
        old = self.ticket.code
        self.give(self.friend.email)
        res = self.client.get('/event/ticket/%s/transfers/' % old,
                              **self.org_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertFalse(data['still_valid'])
        self.assertEqual(len(data['transfers']), 1)

    def test_the_history_says_a_live_code_is_live(self):
        res = self.client.get('/event/ticket/%s/transfers/' % self.ticket.code,
                              **self.org_auth)
        self.assertTrue(res.json()['data']['still_valid'])

    def test_a_stranger_cannot_read_it(self):
        res = self.client.get('/event/ticket/%s/transfers/' % self.ticket.code,
                              **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_the_holder_can_read_their_own(self):
        res = self.client.get('/event/ticket/%s/transfers/' % self.ticket.code,
                              **self.holder_auth)
        self.assertEqual(res.status_code, 200)

    def test_two_transfers_leave_two_rows_in_order(self):
        self.give(self.friend.email)
        self.ticket.refresh_from_db()
        self.client.post('/event/ticket/%s/transfer/' % self.ticket.code,
                         {'to': self.stranger.email}, format='json',
                         **self.friend_auth)
        self.ticket.refresh_from_db()
        res = self.client.get('/event/ticket/%s/transfers/' % self.ticket.code,
                              **self.org_auth)
        self.assertEqual(len(res.json()['data']['transfers']), 2)
