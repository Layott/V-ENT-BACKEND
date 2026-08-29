"""One address, however many tickets the organiser allows.

CEO: "it should be just one per email, so if a ticket has been sent to an email
before, it should not be sent again, even if they refresh and type in that same
email again. also add this option for the event creator to be able to manage."

The refresh is the case that matters. A check against what THIS request is
asking for would wave it through every time, because each request asks for one
ticket. The check is against what the address already holds.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from .models import Event, Ticket, TicketTier


def a_user(name, balance=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('l-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('lw%s' % name)[:10], user=user, wallet_balance=balance,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class OneTicketPerEmailTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('lim_org')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Limit Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4),
            max_tickets_per_email=1)
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('0'), quantity=100)

    def buy(self, email='ada@example.test', quantity=1):
        return self.client.post(
            '/event/%s/guest-buy/' % self.event.event_id,
            data={'tier_id': self.tier.id, 'quantity': quantity, 'email': email},
            content_type='application/json')

    # ------------------------------------------------------------ the rule

    def test_the_first_ticket_goes_out(self):
        self.assertEqual(self.buy().status_code, 201)

    def test_the_same_address_is_refused_the_second_time(self):
        self.buy()
        res = self.buy()
        self.assertEqual(res.status_code, 409)
        body = res.json()
        self.assertEqual(body['code'], 'EMAIL_LIMIT_REACHED')
        self.assertEqual(body['field'], 'email')
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 1)

    def test_case_and_spacing_do_not_get_around_it(self):
        self.buy('Ada@Example.test')
        res = self.buy('  ada@example.TEST ')
        self.assertEqual(res.status_code, 409, res.json())
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 1)

    def test_asking_for_two_at_once_is_refused_when_the_limit_is_one(self):
        res = self.buy(quantity=2)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 0)

    def test_another_address_is_unaffected(self):
        self.buy('ada@example.test')
        self.assertEqual(self.buy('chidi@example.test').status_code, 201)

    def test_the_limit_is_per_event(self):
        now = timezone.now()
        other = Event.objects.create(
            name='Elsewhere', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0, start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4), max_tickets_per_email=1)
        other_tier = TicketTier.objects.create(
            event=other, name='General', price=Decimal('0'), quantity=10)
        self.buy()
        res = self.client.post(
            '/event/%s/guest-buy/' % other.event_id,
            data={'tier_id': other_tier.id, 'quantity': 1,
                  'email': 'ada@example.test'},
            content_type='application/json')
        self.assertEqual(res.status_code, 201, res.json())

    def test_a_cancelled_ticket_frees_the_address(self):
        # Somebody refunded holds nothing. Refusing them on the strength of a
        # record that no longer admits anybody is the platform arguing with
        # itself.
        self.buy()
        Ticket.objects.filter(event=self.event).update(status='cancelled')
        self.assertEqual(self.buy().status_code, 201)

    # ------------------------------------------------- the organiser's switch

    def test_no_limit_means_no_limit(self):
        self.event.max_tickets_per_email = None
        self.event.save(update_fields=['max_tickets_per_email'])
        self.buy()
        self.assertEqual(self.buy().status_code, 201)
        self.assertEqual(Ticket.objects.filter(event=self.event).count(), 2)

    def test_a_limit_of_four_lets_a_family_through(self):
        self.event.max_tickets_per_email = 4
        self.event.save(update_fields=['max_tickets_per_email'])
        self.assertEqual(self.buy(quantity=4).status_code, 201)
        self.assertEqual(self.buy().status_code, 409)

    def test_the_organiser_sets_it(self):
        res = self.client.put(
            '/event/edit-event/%s/' % self.event.event_id,
            data={'max_tickets_per_email': 2},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.event.refresh_from_db()
        self.assertEqual(self.event.max_tickets_per_email, 2)

    def test_the_organiser_turns_it_off(self):
        # An empty value has to mean "no limit" rather than "unchanged", or
        # there is no way to express turning it off at all.
        res = self.client.put(
            '/event/edit-event/%s/' % self.event.event_id,
            data={'max_tickets_per_email': ''},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.event.refresh_from_db()
        self.assertIsNone(self.event.max_tickets_per_email)

    def test_a_limit_of_zero_is_read_as_no_limit_not_as_nobody(self):
        self.client.put(
            '/event/edit-event/%s/' % self.event.event_id,
            data={'max_tickets_per_email': 0},
            content_type='application/json', **self.auth)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.max_tickets_per_email)

    def test_a_limit_that_is_not_a_number_is_refused(self):
        res = self.client.put(
            '/event/edit-event/%s/' % self.event.event_id,
            data={'max_tickets_per_email': 'one'},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)

    # ---------------------------------------------------------- signed in too

    def test_a_signed_in_buyer_is_held_to_the_same_limit(self):
        # The rule belongs to the event, not to the door somebody came through.
        buyer, buyer_auth = a_user('lim_buyer', balance=1000)
        first = self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id,
            data={'tier_id': self.tier.id, 'quantity': 1, 'pin': '1234'},
            content_type='application/json', **buyer_auth)
        self.assertEqual(first.status_code, 201, first.json())

        second = self.client.post(
            '/event/%s/buy-ticket/' % self.event.event_id,
            data={'tier_id': self.tier.id, 'quantity': 1, 'pin': '1234'},
            content_type='application/json', **buyer_auth)
        self.assertEqual(second.status_code, 409, second.json())
        self.assertEqual(second.json()['code'], 'EMAIL_LIMIT_REACHED')

    def test_the_form_is_told_the_limit(self):
        res = self.client.get(
            '/event/%s/checkout-fields/' % self.event.event_id)
        self.assertEqual(res.json()['data']['max_tickets_per_email'], 1)
