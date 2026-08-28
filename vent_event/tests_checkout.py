"""Buying a ticket without an account, and what the organiser asks for.

CEO: "Hope people can get tickets without having to create accounts on the
website... Or better still, the organizer decides what fields he wants to be
collected."

Two things these are mostly about. Email is always collected and cannot be
switched off, because a ticket with no way to reach the holder is not a ticket.
And nothing that reads a ticket may assume it has an account, because the first
guest through the door would otherwise 500 the scanner.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from . import checkout
from .models import Event, EventCheckoutField, Ticket, TicketTier
from .views_guest import claim_for


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('g-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class FieldEditorTests(TestCase):
    """The organiser composing what is asked for."""

    def setUp(self):
        self.organiser, self.auth = a_user('field_org')
        self.stranger, self.stranger_auth = a_user('field_stranger')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Field Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=6))

    def url(self):
        return '/event/%s/checkout-fields/manage/' % self.event.event_id

    def save(self, fields, auth=None):
        return self.client.put(self.url(), data={'fields': fields},
                               content_type='application/json',
                               **(auth if auth is not None else self.auth))

    def test_the_organiser_decides_what_is_collected(self):
        res = self.save([
            {'label': 'Full name', 'kind': 'text', 'required': True},
            {'label': 'Phone number', 'kind': 'phone'},
            {'label': 'Shirt size', 'kind': 'choice',
             'options': ['S', 'M', 'L'], 'required': True},
        ])
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['data']['fields']
        self.assertEqual([r['label'] for r in rows],
                         ['Full name', 'Phone number', 'Shirt size'])
        self.assertEqual([r['order'] for r in rows], [0, 1, 2])

    def test_a_field_needs_a_label(self):
        res = self.save([{'label': '  ', 'kind': 'text'}])
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'label')

    def test_a_list_needs_something_to_choose_between(self):
        res = self.save([{'label': 'Size', 'kind': 'choice', 'options': ['M']}])
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'options')

    def test_an_unknown_kind_is_refused(self):
        res = self.save([{'label': 'Mood', 'kind': 'telepathy'}])
        self.assertEqual(res.status_code, 400, res.content)

    def test_reordering_keeps_the_answers_already_given(self):
        """Answers are stored by field id. Deleting and recreating the rows
        would orphan every one of them."""
        self.save([{'label': 'Shirt size', 'kind': 'text'}])
        first_id = EventCheckoutField.objects.get().id

        self.save([
            {'label': 'Full name', 'kind': 'text'},
            {'label': 'Shirt size', 'kind': 'text'},
        ])
        kept = EventCheckoutField.objects.get(label='Shirt size')
        self.assertEqual(kept.id, first_id)
        self.assertEqual(kept.order, 1)

    def test_a_stranger_cannot_change_them(self):
        res = self.save([{'label': 'Mine', 'kind': 'text'}],
                        auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_the_public_form_can_read_them_before_anybody_signs_in(self):
        self.save([{'label': 'Full name', 'kind': 'text', 'required': True}])
        res = self.client.get('/event/%s/checkout-fields/' % self.event.event_id)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(data['fields'][0]['label'], 'Full name')
        # Said explicitly rather than left for the form to assume.
        self.assertTrue(data['email_required'])
        self.assertTrue(data['guest_checkout'])


class GuestBuyTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('guest_org')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Guest Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, capacity=100,
            start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=6))
        self.free = TicketTier.objects.create(
            event=self.event, name='Free entry', price=0, quantity=50)
        self.paid = TicketTier.objects.create(
            event=self.event, name='Standard', price=Decimal('5000'), quantity=50)

    def buy(self, **body):
        body.setdefault('tier_id', self.free.id)
        body.setdefault('email', 'amara@example.test')
        return self.client.post(
            '/event/%s/guest-buy/' % self.event.event_id,
            data=body, content_type='application/json')

    # ------------------------------------------------------------------ free

    def test_a_guest_buys_a_free_ticket_with_no_account(self):
        """The whole point. No Authorization header anywhere in this request."""
        res = self.buy()
        self.assertEqual(res.status_code, 201, res.content)
        ticket = Ticket.objects.get()
        self.assertIsNone(ticket.user_id)
        self.assertEqual(ticket.attendee_email, 'amara@example.test')
        self.assertEqual(res.json()['data']['paid'], False)

    def test_the_email_is_required(self):
        res = self.buy(email='')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'email')

    def test_an_address_that_is_not_one_is_refused(self):
        res = self.buy(email='amara at example')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'email')

    def test_several_tickets_in_one_go(self):
        res = self.buy(quantity=3)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Ticket.objects.count(), 3)
        # Each has its own code, because each admits one person.
        self.assertEqual(len({t.code for t in Ticket.objects.all()}), 3)

    def test_a_sold_out_tier_refuses_a_guest_too(self):
        self.free.quantity = 0
        self.free.save(update_fields=['quantity'])
        res = self.buy()
        self.assertEqual(res.status_code, 409, res.content)

    def test_the_venue_ceiling_applies_to_guests(self):
        self.event.capacity = 1
        self.event.save(update_fields=['capacity'])
        self.buy()
        res = self.buy(email='second@example.test')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'EVENT_FULL')

    # --------------------------------------------------------- the questions

    def test_a_required_field_left_blank_is_refused_and_named(self):
        """"Please complete the form" makes somebody hunt for what they missed,
        on a phone, at the moment they were about to pay."""
        EventCheckoutField.objects.create(
            event=self.event, label='Shirt size', kind='text', required=True)
        res = self.buy()
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('Shirt size', res.json()['message'])

    def test_the_answers_are_stored_against_the_ticket(self):
        field = EventCheckoutField.objects.create(
            event=self.event, label='Shirt size', kind='choice',
            options=['S', 'M', 'L'], required=True)
        res = self.buy(attendees=[{'name': 'Amara Obi',
                                   'answers': {str(field.id): 'M'}}])
        self.assertEqual(res.status_code, 201, res.content)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.answers[str(field.id)], 'M')
        self.assertEqual(ticket.attendee_name, 'Amara Obi')

    def test_an_option_that_is_not_on_the_list_is_refused(self):
        field = EventCheckoutField.objects.create(
            event=self.event, label='Shirt size', kind='choice',
            options=['S', 'M'], required=True)
        res = self.buy(attendees=[{'answers': {str(field.id): 'XXL'}}])
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_number_field_refuses_words(self):
        field = EventCheckoutField.objects.create(
            event=self.event, label='Age', kind='number', required=True)
        res = self.buy(attendees=[{'answers': {str(field.id): 'twenty'}}])
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_required_checkbox_has_to_be_ticked(self):
        """Which is how a terms box works."""
        field = EventCheckoutField.objects.create(
            event=self.event, label='I accept the rules', kind='checkbox',
            required=True)
        self.assertEqual(
            self.buy(attendees=[{'answers': {str(field.id): False}}]).status_code,
            400)
        self.assertEqual(
            self.buy(attendees=[{'answers': {str(field.id): True}}]).status_code,
            201)

    def test_an_order_field_is_asked_once_not_once_per_ticket(self):
        """A company name on the receipt is per order. Asking it six times is
        how somebody abandons a basket."""
        field = EventCheckoutField.objects.create(
            event=self.event, label='Company', kind='text', per_ticket=False)
        res = self.buy(quantity=2, answers={str(field.id): 'Vermillion'})
        self.assertEqual(res.status_code, 201, res.content)
        for ticket in Ticket.objects.all():
            self.assertEqual(ticket.answers[str(field.id)], 'Vermillion')

    def test_a_field_the_organiser_never_asked_for_is_not_stored(self):
        """A form that accepts anything is a form somebody puts a novel into."""
        res = self.buy(attendees=[{'answers': {'99999': 'x' * 5000}}])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Ticket.objects.get().answers, {})

    # ------------------------------------------------------------ the money

    def test_a_paid_ticket_goes_to_paystack_rather_than_a_wallet(self):
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': 'sk_test'}), \
             patch('vent_event.views_guest.http_requests.post') as post:
            post.return_value.raise_for_status = lambda: None
            post.return_value.json = lambda: {
                'status': True,
                'data': {'authorization_url': 'https://paystack.test/pay/abc'},
            }
            res = self.buy(tier_id=self.paid.id)

        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn('paystack.test', res.json()['data']['authorization_url'])
        # Nothing is issued until the money lands.
        self.assertEqual(Ticket.objects.count(), 0)

    def test_no_ticket_exists_before_the_payment_does(self):
        """A ticket that exists before the money is a ticket somebody can
        screenshot."""
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': 'sk_test'}), \
             patch('vent_event.views_guest.http_requests.post') as post:
            post.return_value.raise_for_status = lambda: None
            post.return_value.json = lambda: {
                'status': True, 'data': {'authorization_url': 'https://x.test'}}
            self.buy(tier_id=self.paid.id)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_a_paid_ticket_says_so_plainly_when_cards_are_not_set_up(self):
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': ''}):
            res = self.buy(tier_id=self.paid.id)
        self.assertEqual(res.status_code, 503, res.content)
        self.assertEqual(res.json()['code'], 'PAYMENT_UNAVAILABLE')

    def _verify(self, reference='vt-abc', paystack_status='success'):
        with patch.dict('os.environ', {'PAYSTACK_SECRET_KEY': 'sk_test'}), \
             patch('vent_event.views_guest.http_requests.get') as get:
            get.return_value.raise_for_status = lambda: None
            get.return_value.json = lambda: {
                'status': True,
                'data': {
                    'status': paystack_status,
                    'customer': {'email': 'amara@example.test'},
                    'metadata': {
                        'event_id': self.event.event_id,
                        'tier_id': self.paid.id,
                        'quantity': 2,
                        'answers': {},
                        'attendees': [{'name': 'Amara'}, {'name': 'Chidi'}],
                    },
                },
            }
            return self.client.post('/event/guest-verify/',
                                    data={'reference': reference},
                                    content_type='application/json')

    def test_a_confirmed_payment_issues_the_tickets(self):
        res = self._verify()
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Ticket.objects.count(), 2)
        self.assertEqual({t.attendee_name for t in Ticket.objects.all()},
                         {'Amara', 'Chidi'})

    def test_verifying_twice_does_not_issue_twice(self):
        """The browser returning and Paystack calling back are two arrivals for
        one payment. Issuing twice would put two people through one door."""
        self._verify()
        again = self._verify()
        self.assertEqual(again.status_code, 200, again.content)
        self.assertTrue(again.json()['data']['already_issued'])
        self.assertEqual(Ticket.objects.count(), 2)

    def test_a_payment_that_did_not_go_through_issues_nothing(self):
        res = self._verify(paystack_status='abandoned')
        self.assertEqual(res.status_code, 402, res.content)
        self.assertEqual(Ticket.objects.count(), 0)

    # ----------------------------------------------------------- afterwards

    def test_a_guest_finds_their_ticket_with_the_email_and_the_code(self):
        self.buy()
        code = Ticket.objects.get().code
        res = self.client.post('/event/guest-lookup/',
                               data={'email': 'amara@example.test', 'code': code},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['ticket']['code'], code)

    def test_the_email_alone_is_not_enough(self):
        """Otherwise anybody could type an address and read a booking."""
        self.buy()
        res = self.client.post('/event/guest-lookup/',
                               data={'email': 'amara@example.test'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 400, res.content)

    def test_the_wrong_email_for_a_real_code_is_refused(self):
        self.buy()
        code = Ticket.objects.get().code
        res = self.client.post('/event/guest-lookup/',
                               data={'email': 'someone@else.test', 'code': code},
                               content_type='application/json')
        self.assertEqual(res.status_code, 404, res.content)

    def test_signing_up_later_with_that_address_gets_the_tickets(self):
        """Asking somebody to forward themselves a code is not a flow."""
        self.buy(quantity=2)
        self.assertEqual(Ticket.objects.filter(user__isnull=True).count(), 2)

        member = Users.objects.create(
            username='amara', email='amara@example.test', is_active=True)
        self.assertEqual(claim_for(member), 2)
        self.assertEqual(Ticket.objects.filter(user=member).count(), 2)

    def test_claiming_does_not_take_somebody_elses_tickets(self):
        self.buy()
        other = Users.objects.create(
            username='notamara', email='someone@else.test', is_active=True)
        self.assertEqual(claim_for(other), 0)


class GuestAtTheDoorTests(TestCase):
    """Nothing that reads a ticket may assume it has an account."""

    def setUp(self):
        self.organiser, self.auth = a_user('door_guest_org')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Door Guest Probe', creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0,
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=5),
            reg_start_date=now - timedelta(days=2),
            reg_end_date=now + timedelta(hours=4))
        self.tier = TicketTier.objects.create(
            event=self.event, name='Free entry', price=0, quantity=50)
        self.field = EventCheckoutField.objects.create(
            event=self.event, label='Shirt size', kind='text')
        self.ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, user=None,
            code='VT-GUEST001', price_vc=0,
            attendee_name='Amara Obi', attendee_email='amara@example.test',
            answers={str(self.field.id): 'M'})

    def test_a_guest_can_be_checked_in(self):
        """The first guest through the door would otherwise 500 the scanner."""
        res = self.client.post('/event/ticket/VT-GUEST001/check-in/',
                               data={'gate': 'Main'},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn('Amara Obi', res.json()['message'])

    def test_the_door_list_shows_a_guest_and_their_answers(self):
        res = self.client.get('/event/%s/attendees/' % self.event.event_id,
                              **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        row = res.json()['data']['attendees'][0]
        self.assertTrue(row['guest'])
        self.assertEqual(row['attendee_name'], 'Amara Obi')
        # Labelled, because a list showing {"7": "M"} helps nobody.
        self.assertEqual(row['answers'],
                         [{'label': 'Shirt size', 'value': 'M', 'kind': 'text'}])

    def test_a_renamed_field_does_not_orphan_an_answer(self):
        self.field.label = 'T-shirt size'
        self.field.save(update_fields=['label'])
        described = checkout.describe(self.event, self.ticket.answers)
        self.assertEqual(described[0]['label'], 'T-shirt size')
        self.assertEqual(described[0]['value'], 'M')
