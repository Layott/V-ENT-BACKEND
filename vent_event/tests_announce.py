"""Telling everybody holding a ticket something.

The parts worth pinning are the ones a mail-merge gets wrong: guests are
included, one address is told once however many tickets they bought, refunded
holders are left alone, and nobody's address is exposed to anybody else.
"""
from datetime import time, timedelta

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Notification, Users

from .models import Event, EventAnnouncement, EventManager, Ticket, TicketTier


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('a-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class AnnounceBase(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('an_org')
        self.stranger, self.stranger_auth = a_user('an_other')
        game = Games.objects.create(game_title='EA FC AN')
        now = timezone.localtime(timezone.now())
        self.event = Event.objects.create(
            name='Announce Probe', game=game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timedelta(days=3),
            event_date=(now + timedelta(days=3)).date(),
            start_time=time(18, 0), end_time=time(22, 0), location='Lagos')
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)

        # Two guests, and one member holding two tickets.
        self.guest_a = Ticket.objects.create(
            event=self.event, tier=self.tier, code='AN000001',
            attendee_email='guest.a@example.com')
        self.guest_b = Ticket.objects.create(
            event=self.event, tier=self.tier, code='AN000002',
            attendee_email='guest.b@example.com')
        self.member, _ = a_user('an_member')
        for code in ('AN000003', 'AN000004'):
            Ticket.objects.create(
                event=self.event, tier=self.tier, code=code, user=self.member,
                attendee_email=self.member.email)
        mail.outbox = []

    def send(self, payload=None, auth=None):
        return self.client.post(
            '/event/%s/announcements/' % self.event.event_id,
            payload or {'subject': 'Gate has moved',
                        'body': 'Use the Ozumba gate, not the main one.'},
            content_type='application/json', **(auth or self.auth))

    def listing(self):
        return self.client.get('/event/%s/announcements/' % self.event.event_id)


class AnnounceTests(AnnounceBase):
    def test_the_organiser_sends_one(self):
        res = self.send()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(EventAnnouncement.objects.count(), 1)

    def test_guests_are_included(self):
        # Most ticket holders here have no account. Missing them is the same as
        # not sending it.
        self.send()
        recipients = {to for m in mail.outbox for to in m.to}
        self.assertIn('guest.a@example.com', recipients)
        self.assertIn('guest.b@example.com', recipients)

    def test_one_address_is_told_once(self):
        # The member holds two tickets and is one person.
        self.send()
        self.assertEqual(len(mail.outbox), 3)
        row = EventAnnouncement.objects.get()
        self.assertEqual(row.recipients, 3)

    def test_nobody_is_bcc_to_anybody(self):
        # A bcc field is one mis-click away from publishing the attendee list.
        self.send()
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)
            self.assertFalse(getattr(message, 'bcc', []))
            self.assertFalse(getattr(message, 'cc', []))

    def test_account_holders_also_get_it_in_the_inbox(self):
        self.send()
        rows = Notification.objects.filter(user=self.member, category='event')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().title, 'Gate has moved')

    def test_the_notification_links_by_slug(self):
        self.send()
        link = Notification.objects.filter(user=self.member).first().link
        self.assertNotIn('?id=', link)
        self.assertIn(self.event.slug, link)

    def test_a_refunded_holder_is_left_alone(self):
        # They are not going. Telling them the gate moved is noise.
        self.guest_a.status = 'refunded'
        self.guest_a.save()
        self.send()
        recipients = {to for m in mail.outbox for to in m.to}
        self.assertNotIn('guest.a@example.com', recipients)

    def test_it_can_go_to_only_the_people_who_have_not_arrived(self):
        self.guest_a.status = 'checked_in'
        self.guest_a.checked_in_at = timezone.now()
        self.guest_a.save()
        self.send({'subject': 'Doors close in 20 minutes',
                   'body': 'Last entry at 8pm.',
                   'audience': 'not_checked_in'})
        recipients = {to for m in mail.outbox for to in m.to}
        self.assertNotIn('guest.a@example.com', recipients)
        self.assertIn('guest.b@example.com', recipients)

    def test_it_can_go_to_only_the_people_inside(self):
        self.guest_a.status = 'checked_in'
        self.guest_a.save()
        self.send({'subject': 'Cosplay judging at 9',
                   'body': 'Main stage.', 'audience': 'checked_in'})
        recipients = {to for m in mail.outbox for to in m.to}
        self.assertEqual(recipients, {'guest.a@example.com'})

    def test_what_was_sent_is_readable_afterwards(self):
        self.send()
        res = self.listing()
        self.assertEqual(res.status_code, 200)
        rows = res.data['data']['announcements']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['subject'], 'Gate has moved')
        self.assertEqual(rows[0]['recipients'], 3)

    def test_the_listing_is_public(self):
        # A reader deciding whether to buy benefits from seeing that the
        # organiser has moved the doors twice.
        self.send()
        res = self.client.get('/event/%s/announcements/' % self.event.slug)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['data']['announcements']), 1)

    def test_the_audience_is_counted_before_anything_is_written(self):
        res = self.client.get(
            '/event/%s/announcements/audience/' % self.event.event_id,
            **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['audiences']['all'], 3)
        self.assertEqual(res.data['data']['audiences']['checked_in'], 0)
        self.assertEqual(res.data['data']['sent_today'], 0)


class AnnounceRefusalTests(AnnounceBase):
    def test_a_stranger_sends_nothing(self):
        res = self.send(auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_signed_out_sends_nothing(self):
        res = self.client.post(
            '/event/%s/announcements/' % self.event.event_id,
            {'subject': 'a', 'body': 'b'}, content_type='application/json')
        self.assertEqual(res.status_code, 401)

    def test_a_manager_may_send_but_door_staff_may_not(self):
        manager, manager_auth = a_user('an_mgr')
        EventManager.objects.create(event=self.event, user=manager,
                                    role='manager')
        self.assertEqual(self.send(auth=manager_auth).status_code, 201)

        steward, steward_auth = a_user('an_door')
        EventManager.objects.create(event=self.event, user=steward, role='door')
        self.assertEqual(self.send(auth=steward_auth).status_code, 403)

    def test_an_empty_message_is_refused(self):
        self.assertEqual(self.send({'subject': 'Hi', 'body': ''}).status_code, 400)
        self.assertEqual(self.send({'subject': '', 'body': 'Hi'}).status_code, 400)

    def test_an_unknown_audience_is_refused(self):
        res = self.send({'subject': 'a', 'body': 'b', 'audience': 'everyone'})
        self.assertEqual(res.status_code, 400)

    def test_the_fifth_message_today_is_the_last(self):
        # An organiser with a send button is an organiser who can empty an inbox.
        for i in range(5):
            self.assertEqual(
                self.send({'subject': 'Note %d' % i, 'body': 'x'}).status_code,
                201)
        res = self.send({'subject': 'Note 6', 'body': 'x'})
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data['code'], 'RATE_LIMITED')

    def test_a_long_message_is_refused_rather_than_truncated(self):
        # Silently cutting somebody's message in half is worse than refusing it.
        res = self.send({'subject': 'a', 'body': 'x' * 2001})
        self.assertEqual(res.status_code, 400)

    def test_an_unknown_event_is_a_404(self):
        res = self.client.post('/event/999999/announcements/',
                               {'subject': 'a', 'body': 'b'},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 404)
