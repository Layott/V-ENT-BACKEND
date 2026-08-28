"""The organiser's questions reach both checkouts, and the number reaches the door.

Two faults this file exists to stop coming back.

**The questions were only asked of guests.** They are configured on the event,
so they belong to every buyer of that event. Asking them on one of the two
checkouts gives the door a shirt size for half the queue and nothing for the
other half, and nobody finds out until the shirts are already printed. That is
the same feature built for half the product, which is the fault the one-model
rule is about.

**The number was stored where nothing reads it.** A phone answer lands in the
answers blob keyed by field id, which is right for the export and useless to
anything that has to ring somebody: the door list, a cancellation, the "so we
can reach you on the day" the organiser wrote under the field. Those read
`attendee_phone`, and it was empty while the number sat two layers down.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from .models import Event, EventCheckoutField, Ticket, TicketTier


def a_user(name, balance=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('q-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('qw%s' % name)[:10], user=user, wallet_balance=balance,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class SameQuestionsBothWaysTests(TestCase):
    def setUp(self):
        self.organiser, _ = a_user('sq_org')
        self.buyer, self.buyer_auth = a_user('sq_buyer', balance=100000)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Same Questions', creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('10000'),
            quantity=100)
        self.size = EventCheckoutField.objects.create(
            event=self.event, label='Shirt size', kind='choice',
            options=['S', 'M', 'L'], required=True, per_ticket=True, order=1)
        self.phone = EventCheckoutField.objects.create(
            event=self.event, label='Phone number', kind='phone',
            required=False, per_ticket=True, order=2)
        self.company = EventCheckoutField.objects.create(
            event=self.event, label='Company', kind='text',
            required=False, per_ticket=False, order=3)

    # --------------------------------------------------------- signed in

    def buy(self, attendees, answers=None, quantity=1):
        return self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id,
            data={'tier_id': self.tier.id, 'quantity': quantity, 'pin': '1234',
                  'answers': answers or {}, 'attendees': attendees},
            content_type='application/json', **self.buyer_auth)

    def test_a_signed_in_buyer_is_asked_the_same_questions(self):
        res = self.buy([{'answers': {str(self.size.id): 'M'}}],
                       answers={str(self.company.id): 'Vermillion'})
        self.assertEqual(res.status_code, 201, res.json())
        ticket = Ticket.objects.get(user=self.buyer)
        self.assertEqual(ticket.answers[str(self.size.id)], 'M')
        self.assertEqual(ticket.answers[str(self.company.id)], 'Vermillion')

    def test_a_signed_in_buyer_is_refused_the_same_way(self):
        res = self.buy([{'answers': {}}])
        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertEqual(body['code'], 'FIELD_REQUIRED')
        # Which field, so the form can point at it rather than making somebody
        # hunt the page for what they missed.
        self.assertEqual(body['field'], self.size.id)

    def test_a_refusal_costs_nothing(self):
        # Refused before the wallet moves. Refusing after it would mean a
        # refund for a question that could have been asked first.
        before = UserWallet.objects.get(user=self.buyer).wallet_balance
        self.buy([{'answers': {}}])
        self.assertEqual(
            UserWallet.objects.get(user=self.buyer).wallet_balance, before)
        self.assertFalse(Ticket.objects.filter(user=self.buyer).exists())

    def test_two_tickets_carry_two_different_answers(self):
        res = self.buy([{'answers': {str(self.size.id): 'S'}},
                        {'answers': {str(self.size.id): 'L'}}], quantity=2)
        self.assertEqual(res.status_code, 201, res.json())
        sizes = sorted(t.answers[str(self.size.id)]
                       for t in Ticket.objects.filter(user=self.buyer))
        self.assertEqual(sizes, ['L', 'S'])

    def test_a_per_order_answer_is_copied_onto_every_ticket(self):
        # Asked once, and true of both tickets. The door reads one ticket at a
        # time and would otherwise see it on only the first.
        self.buy([{'answers': {str(self.size.id): 'S'}},
                  {'answers': {str(self.size.id): 'L'}}],
                 answers={str(self.company.id): 'Vermillion'}, quantity=2)
        for ticket in Ticket.objects.filter(user=self.buyer):
            self.assertEqual(ticket.answers[str(self.company.id)], 'Vermillion')

    # ------------------------------------------------- the number, both ways

    def test_a_phone_answer_reaches_the_column_the_door_reads(self):
        self.buy([{'answers': {str(self.size.id): 'M',
                               str(self.phone.id): '08031234567'}}])
        ticket = Ticket.objects.get(user=self.buyer)
        self.assertEqual(ticket.attendee_phone, '08031234567')

    def test_a_guest_phone_answer_reaches_it_too(self):
        # The same assertion against the other checkout. A free tier, because a
        # paid one goes to the gateway and issues nothing until it confirms.
        self.tier.price = Decimal('0')
        self.tier.save(update_fields=['price'])
        res = self.client.post(
            '/event/%s/guest-buy/' % self.event.event_id,
            data={'tier_id': self.tier.id, 'quantity': 1,
                  'email': 'ada@example.test',
                  'attendees': [{'name': 'Ada',
                                 'answers': {str(self.size.id): 'L',
                                             str(self.phone.id): '08099999999'}}]},
            content_type='application/json')
        self.assertEqual(res.status_code, 201, res.json())
        ticket = Ticket.objects.get(attendee_email='ada@example.test')
        self.assertEqual(ticket.attendee_phone, '08099999999')

    def test_an_explicit_number_wins_over_the_answer(self):
        # Somebody who gave a number in the field meant for it is not
        # overruled by a question that happens to be phone-shaped.
        self.buy([{'phone': '08000000000',
                   'answers': {str(self.size.id): 'M',
                               str(self.phone.id): '08031234567'}}])
        ticket = Ticket.objects.get(user=self.buyer)
        self.assertEqual(ticket.attendee_phone, '08000000000')

    def test_no_questions_is_still_a_working_checkout(self):
        EventCheckoutField.objects.filter(event=self.event).delete()
        res = self.buy([{}])
        self.assertEqual(res.status_code, 201, res.json())
        self.assertEqual(Ticket.objects.get(user=self.buyer).answers, {})
