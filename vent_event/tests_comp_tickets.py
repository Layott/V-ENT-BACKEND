"""Tickets an organiser sends by typing an email address.

CEO, 29 August 2026: "event creators should be able to send tickets to people
also, by just inputting their emails".

The tests that matter are about the seat, not the happy path. A comped ticket
occupies a place in a finite room, so:

- sending the same list twice must not issue twice;
- the tier's stock must go down, or the organiser oversells;
- a list with one bad address must issue nothing, so they do not have to work
  out which thirty-nine of forty went out.
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Users
from vent_event.models import Event, Ticket, TicketTier


def a_user(name, email=None):
    user = Users.objects.create(
        username=name, email=email or ('%s@vent.test' % name),
        full_name=name.title(), login_session_token=('tk-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class CompTicketTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.game = Games.objects.get_or_create(game_title='Comp')[0]
        self.organiser = a_user('comp_org')
        self.stranger = a_user('comp_stranger')
        self.event = Event.objects.create(
            name='Comp Event', game=self.game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            event_date=(timezone.now() + timezone.timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00', location='Lagos')
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=5, quantity=10, sold=0)

    def _as(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)

    def _send(self, emails, **extra):
        self._as(self.organiser)
        payload = {'tier_id': self.tier.id, 'emails': emails}
        payload.update(extra)
        return self.client.post('/event/%s/comp-tickets/' % self.event.slug,
                                payload, format='json')

    # -------------------------------------------------------------- the basics

    def test_a_ticket_is_issued_per_address(self):
        res = self._send(['a@example.com', 'b@example.com'])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['issued_count'], 2)
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 2)

    def test_it_is_a_real_ticket_with_a_code(self):
        self._send(['a@example.com'])
        ticket = Ticket.objects.get()
        self.assertTrue(ticket.code)
        self.assertEqual(ticket.status, 'valid')
        self.assertEqual(ticket.attendee_email, 'a@example.com')
        self.assertEqual(ticket.price_vc, 0)

    def test_a_block_of_pasted_text_is_accepted(self):
        res = self._send('a@example.com, b@example.com\nc@example.com')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['issued_count'], 3)

    def test_it_attaches_to_an_account_where_one_exists(self):
        holder = a_user('comp_holder', email='holder@example.com')
        self._send(['holder@example.com'])
        self.assertEqual(Ticket.objects.get().user_id, holder.user_id)

    def test_a_guest_with_no_account_still_gets_one(self):
        self._send(['nobody@example.com'])
        ticket = Ticket.objects.get()
        self.assertIsNone(ticket.user_id)
        self.assertEqual(ticket.attendee_email, 'nobody@example.com')

    # ---------------------------------------------------------------- the seat

    def test_the_tier_stock_goes_down(self):
        self._send(['a@example.com', 'b@example.com'])
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.sold, 2)

    def test_sending_the_same_list_twice_does_not_issue_twice(self):
        self._send(['a@example.com', 'b@example.com'])
        res = self._send(['a@example.com', 'b@example.com', 'c@example.com'])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['data']['issued_count'], 1)
        self.assertEqual(sorted(res.data['data']['skipped_already_had_one']),
                         ['a@example.com', 'b@example.com'])
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 3)

    def test_the_same_address_twice_in_one_list_is_one_ticket(self):
        res = self._send(['a@example.com', 'A@Example.com'])
        self.assertEqual(res.data['data']['issued_count'], 1)

    def test_more_than_there_are_left_is_refused_and_issues_nothing(self):
        self.tier.quantity = 2
        self.tier.save(update_fields=['quantity'])
        res = self._send(['a@example.com', 'b@example.com', 'c@example.com'])
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'NOT_ENOUGH_LEFT')
        self.assertEqual(Ticket.objects.count(), 0)
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.sold, 0)

    # -------------------------------------------------------------- the refusals

    def test_one_bad_address_issues_nothing(self):
        res = self._send(['a@example.com', 'not an address', 'b@example.com'])
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'BAD_EMAIL')
        self.assertIn('not an address', res.data['message'])
        self.assertEqual(Ticket.objects.count(), 0)

    def test_an_empty_list_is_refused(self):
        self.assertEqual(self._send([]).status_code, 400)
        self.assertEqual(self._send(['  ']).status_code, 400)

    def test_a_tier_from_another_event_is_refused(self):
        elsewhere = Event.objects.create(
            name='Elsewhere', game=self.game, creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            event_date=(timezone.now() + timezone.timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00', location='Abuja')
        other_tier = TicketTier.objects.create(
            event=elsewhere, name='Other', price=0, quantity=5)
        self._as(self.organiser)
        res = self.client.post(
            '/event/%s/comp-tickets/' % self.event.slug,
            {'tier_id': other_tier.id, 'emails': ['a@example.com']}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_somebody_who_does_not_run_the_event_cannot_send_any(self):
        self._as(self.stranger)
        res = self.client.post(
            '/event/%s/comp-tickets/' % self.event.slug,
            {'tier_id': self.tier.id, 'emails': ['a@example.com']}, format='json')
        self.assertEqual(res.status_code, 403, res.data)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_signed_out_cannot_send_any(self):
        self.client.credentials()
        res = self.client.post(
            '/event/%s/comp-tickets/' % self.event.slug,
            {'tier_id': self.tier.id, 'emails': ['a@example.com']}, format='json')
        self.assertEqual(res.status_code, 401, res.data)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_too_many_at_once_is_refused(self):
        res = self._send(['a%d@example.com' % n for n in range(200)])
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(Ticket.objects.count(), 0)

    # ------------------------------------------------------------- the delivery

    def test_the_response_says_which_addresses_were_emailed(self):
        res = self._send(['a@example.com'])
        data = res.data['data']
        self.assertIn('emailed', data)
        self.assertIn('not_emailed', data)
        # Every issued ticket is accounted for in one list or the other, so an
        # organiser can tell who is still waiting.
        self.assertEqual(
            len(data['emailed']) + len(data['not_emailed']), data['issued_count'])

    def test_the_note_reaches_the_ticket(self):
        self._send(['a@example.com'], note='Guest of the venue')
        self.assertEqual(Ticket.objects.get().answers.get('note'),
                         'Guest of the venue')
