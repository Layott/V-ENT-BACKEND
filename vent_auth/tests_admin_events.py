"""The console managing an event, its tickets, and reading what was sent.

CEO, 30 August 2026: "For admin section we should be able to fully manage events
also and tickets and sese the full details about what was sent out by tournament
organizers and event managers also."

The tests that matter are the ones about a seat and about a trail. A void that
does not return the seat shrinks the room by one every time somebody is refused
entry, and an action with no `AdminAction` behind it cannot answer "who did this
and why" six weeks later, which is the only question anybody asks.
"""
import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import AdminAction, Games, Users
from vent_event.models import (Event, EventAnnouncement, EventManager, Ticket,
                               TicketTier, VendorInvite)


def a_user(name, **extra):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        full_name=name.title(),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
        **extra)
    user.login_session_created_at = timezone.now()
    # The console reads the ordinary session and insists it went through the
    # second factor. A session without this reaches nothing here.
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class AdminEventBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.game = Games.objects.get_or_create(game_title='Free Fire')[0]
        self.admin, self.auth = a_user('ae_admin', is_staff=True, admin_role='super_admin')
        self.organiser, self.organiser_auth = a_user('ae_org')
        self.event = Event.objects.create(
            name='Lagos Anime Con %s' % uuid.uuid4().hex[:4], game=self.game,
            creator=self.organiser, event_type='physical', desc='probe',
            entry_fee=0, capacity=100,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            event_date=(timezone.now() + timezone.timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00', location='Lagos')
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=5000, quantity=10, sold=0)

    def _ticket(self, **extra):
        fields = dict(
            event=self.event, tier=self.tier, status='valid',
            price_vc=5, price_ngn=5000,
            code=uuid.uuid4().hex[:12].upper(),
            attendee_name='Ada Obi', attendee_email='ada@example.test',
        )
        fields.update(extra)
        ticket = Ticket.objects.create(**fields)
        TicketTier.objects.filter(pk=self.tier.pk).update(sold=self.tier.sold + 1)
        self.tier.refresh_from_db()
        return ticket

    def _get(self, path, auth=None, **params):
        return self.client.get(path, params, **(auth if auth is not None else self.auth))

    def _post(self, path, body=None, auth=None):
        return self.client.post(path, body or {}, format='json',
                                **(auth if auth is not None else self.auth))


class EventDetailTests(AdminEventBase):
    """X1: the console sees an event's real numbers."""

    def test_the_numbers_are_counted_from_tickets(self):
        self._ticket()
        self._ticket(status='checked_in', checked_in_at=timezone.now())
        self._ticket(status='refunded')
        res = self._get('/auth/admin/events/%s/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        n = res.json()['data']['numbers']
        self.assertEqual(n['tickets'], 3)
        self.assertEqual(n['valid'], 1)
        self.assertEqual(n['checked_in'], 1)
        self.assertEqual(n['refunded'], 1)

    def test_revenue_leaves_out_a_refunded_ticket(self):
        """Money taken on a ticket that was refunded is not revenue, and showing
        it as such is the number an organiser would quote back at us."""
        self._ticket()
        self._ticket(status='refunded')
        n = self._get('/auth/admin/events/%s/' % self.event.slug).json()['data']['numbers']
        self.assertEqual(n['revenue_vc'], 5)

    def test_comped_tickets_are_counted_apart_from_sales(self):
        """"Sold 200" and "sold 140 and gave away 60" are different rooms."""
        self._ticket()
        self._ticket(price_vc=0, price_ngn=0, answers={'comped_by': 'ae_org'})
        n = self._get('/auth/admin/events/%s/' % self.event.slug).json()['data']['numbers']
        self.assertEqual(n['tickets'], 2)
        self.assertEqual(n['comped'], 1)

    def test_a_tier_says_what_is_left(self):
        self._ticket()
        tier = self._get('/auth/admin/events/%s/' % self.event.slug).json()['data']['tiers'][0]
        self.assertEqual(tier['sold'], 1)
        self.assertEqual(tier['remaining'], 9)

    def test_the_managers_an_organiser_added_are_listed(self):
        helper, _ = a_user('ae_helper')
        EventManager.objects.create(event=self.event, user=helper, role='door',
                                    added_by=self.organiser)
        data = self._get('/auth/admin/events/%s/' % self.event.slug).json()['data']
        self.assertEqual(len(data['managers']), 1)
        self.assertEqual(data['managers'][0]['role'], 'door')
        self.assertEqual(data['managers'][0]['added_by']['username'], self.organiser.username)

    def test_an_event_can_be_found_by_id_as_well_as_slug(self):
        res = self._get('/auth/admin/events/%s/' % self.event.event_id)
        self.assertEqual(res.status_code, 200)

    def test_an_unknown_event_is_a_404(self):
        self.assertEqual(self._get('/auth/admin/events/no-such-event/').status_code, 404)


class EventStateTests(AdminEventBase):
    """X2: cancelling an event, and putting it back."""

    def test_an_admin_cancels_an_event(self):
        res = self._post('/auth/admin/events/%s/state/' % self.event.slug,
                         {'action': 'cancel', 'reason': 'Venue pulled out'})
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_active)

    def test_cancelling_without_a_reason_is_refused(self):
        """"Why is this cancelled" is the first question support gets, and the
        answer has to be in the row rather than in somebody's memory."""
        res = self._post('/auth/admin/events/%s/state/' % self.event.slug,
                         {'action': 'cancel'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'REASON_REQUIRED')
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_active)

    def test_a_cancel_is_written_to_the_audit_log_with_who_and_why(self):
        self._post('/auth/admin/events/%s/state/' % self.event.slug,
                   {'action': 'cancel', 'reason': 'Venue pulled out'})
        row = AdminAction.objects.get(action_type='cancel_event')
        self.assertEqual(row.admin_id, self.admin.user_id)
        self.assertEqual(row.reason, 'Venue pulled out')
        self.assertEqual(row.target_id, str(self.event.event_id))

    def test_restoring_puts_it_back(self):
        self._post('/auth/admin/events/%s/state/' % self.event.slug,
                   {'action': 'cancel', 'reason': 'x'})
        res = self._post('/auth/admin/events/%s/state/' % self.event.slug,
                         {'action': 'restore'})
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_active)
        self.assertTrue(AdminAction.objects.filter(action_type='restore_event').exists())

    def test_cancelling_something_already_cancelled_says_so(self):
        self._post('/auth/admin/events/%s/state/' % self.event.slug,
                   {'action': 'cancel', 'reason': 'x'})
        res = self._post('/auth/admin/events/%s/state/' % self.event.slug,
                         {'action': 'cancel', 'reason': 'x'})
        self.assertEqual(res.status_code, 409)

    def test_an_action_nobody_defined_is_refused(self):
        res = self._post('/auth/admin/events/%s/state/' % self.event.slug,
                         {'action': 'delete', 'reason': 'x'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'BAD_ACTION')


class EventTicketTests(AdminEventBase):
    """X3: the tickets on an event, and acting on one."""

    def test_the_tickets_are_listed_with_who_holds_them(self):
        self._ticket()
        res = self._get('/auth/admin/events/%s/tickets/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        rows = res.json()['data']['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['attendee_name'], 'Ada Obi')
        self.assertTrue(rows[0]['is_guest'])

    def test_search_finds_a_ticket_by_code_name_or_email(self):
        """Whoever is looking has exactly one of the three and does not know
        which field it lives in."""
        ticket = self._ticket(attendee_name='Chidi Eze', attendee_email='chidi@example.test')
        self._ticket(attendee_name='Somebody Else', attendee_email='else@example.test')
        for term in (ticket.code, 'Chidi', 'chidi@example.test'):
            rows = self._get('/auth/admin/events/%s/tickets/' % self.event.slug,
                             search=term).json()['data']['results']
            self.assertEqual([r['code'] for r in rows], [ticket.code], term)

    def test_the_list_can_be_narrowed_to_comped_tickets(self):
        self._ticket()
        comped = self._ticket(price_vc=0, price_ngn=0, answers={'comped_by': 'ae_org'})
        rows = self._get('/auth/admin/events/%s/tickets/' % self.event.slug,
                         status='comped').json()['data']['results']
        self.assertEqual([r['code'] for r in rows], [comped.code])
        self.assertEqual(rows[0]['comped_by'], 'ae_org')

    def test_voiding_a_ticket_returns_the_seat(self):
        """A tier's `sold` is what the next buyer is checked against. A void
        that left it alone would shrink the room by one every time."""
        ticket = self._ticket()
        self.assertEqual(self.tier.sold, 1)
        res = self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                         {'action': 'void', 'reason': 'Chargeback'})
        self.assertEqual(res.status_code, 200, res.content[:300])
        ticket.refresh_from_db()
        self.tier.refresh_from_db()
        self.assertEqual(ticket.status, 'cancelled')
        self.assertEqual(self.tier.sold, 0)

    def test_a_voided_ticket_keeps_its_row_and_its_code(self):
        """Somebody turned away at the door holding it needs the scanner to say
        why, and a deleted row says nothing."""
        ticket = self._ticket()
        self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                   {'action': 'void', 'reason': 'Chargeback'})
        self.assertTrue(Ticket.objects.filter(code=ticket.code).exists())

    def test_voiding_without_a_reason_is_refused(self):
        ticket = self._ticket()
        res = self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                         {'action': 'void'})
        self.assertEqual(res.status_code, 400)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'valid')

    def test_a_void_is_written_to_the_audit_log(self):
        ticket = self._ticket()
        self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                   {'action': 'void', 'reason': 'Chargeback'})
        row = AdminAction.objects.get(action_type='void_ticket')
        self.assertEqual(row.target_id, ticket.code)
        self.assertEqual(row.reason, 'Chargeback')
        self.assertEqual(row.metadata['was'], 'valid')

    def test_reinstating_gives_the_seat_back_and_restores_the_status(self):
        ticket = self._ticket()
        self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                   {'action': 'void', 'reason': 'x'})
        res = self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                         {'action': 'reinstate'})
        self.assertEqual(res.status_code, 200, res.content[:300])
        ticket.refresh_from_db()
        self.tier.refresh_from_db()
        self.assertEqual(ticket.status, 'valid')
        self.assertEqual(self.tier.sold, 1)

    def test_reinstating_somebody_who_had_already_arrived_keeps_that(self):
        ticket = self._ticket(status='checked_in', checked_in_at=timezone.now())
        self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                   {'action': 'void', 'reason': 'x'})
        self._post('/auth/admin/tickets/%s/action/' % ticket.code, {'action': 'reinstate'})
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'checked_in')

    def test_reinstating_into_a_full_tier_is_refused(self):
        """An event that sold out while the ticket was void has no seat to give
        back, and issuing one anyway puts the venue over capacity."""
        ticket = self._ticket()
        self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                   {'action': 'void', 'reason': 'x'})
        TicketTier.objects.filter(pk=self.tier.pk).update(sold=self.tier.quantity)
        res = self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                         {'action': 'reinstate'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'TIER_FULL')

    def test_voiding_twice_says_so_rather_than_returning_two_seats(self):
        ticket = self._ticket()
        self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                   {'action': 'void', 'reason': 'x'})
        res = self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                         {'action': 'void', 'reason': 'x'})
        self.assertEqual(res.status_code, 409)
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.sold, 0)

    def test_an_unknown_ticket_code_is_a_404(self):
        res = self._post('/auth/admin/tickets/NOSUCHCODE/action/',
                         {'action': 'void', 'reason': 'x'})
        self.assertEqual(res.status_code, 404)


class WhatWasSentTests(AdminEventBase):
    """X4: what the organiser actually sent out."""

    def test_an_announcement_is_shown_in_full(self):
        """Somebody asking "what did they send my customer" needs the message,
        not a preview of it."""
        EventAnnouncement.objects.create(
            event=self.event, sent_by=self.organiser,
            subject='Gate change', body='We have moved to Gate B. ' * 20,
            audience='all', recipients=143, notified_in_app=40)
        res = self._get('/auth/admin/events/%s/sent/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = res.json()['data']['announcements'][0]
        self.assertEqual(row['subject'], 'Gate change')
        self.assertIn('Gate B', row['body'])
        self.assertEqual(row['recipients'], 143)
        self.assertEqual(row['sent_by']['username'], self.organiser.username)

    def test_a_send_that_half_worked_says_so(self):
        """This is the field that explains a complaint about a message somebody
        never received."""
        EventAnnouncement.objects.create(
            event=self.event, sent_by=self.organiser, subject='x', body='y',
            recipients=10, email_error='SMTP timeout after 4 addresses')
        row = self._get('/auth/admin/events/%s/sent/'
                        % self.event.slug).json()['data']['announcements'][0]
        self.assertIn('SMTP timeout', row['email_error'])

    def test_free_tickets_show_who_gave_them_to_whom(self):
        self._ticket(price_vc=0, price_ngn=0,
                     attendee_email='press@example.test',
                     answers={'comped_by': self.organiser.username, 'note': 'Press'})
        data = self._get('/auth/admin/events/%s/sent/' % self.event.slug).json()['data']
        self.assertEqual(len(data['comped_tickets']), 1)
        comp = data['comped_tickets'][0]
        self.assertEqual(comp['given_by'], self.organiser.username)
        self.assertEqual(comp['to_email'], 'press@example.test')
        self.assertEqual(comp['note'], 'Press')

    def test_a_sold_ticket_is_not_listed_as_a_free_one(self):
        self._ticket()
        data = self._get('/auth/admin/events/%s/sent/' % self.event.slug).json()['data']
        self.assertEqual(data['comped_tickets'], [])

    def test_vendor_invitations_are_listed(self):
        VendorInvite.objects.create(event=self.event, name='Otaku Prints',
                                    email='hi@otaku.test', booth='B12')
        data = self._get('/auth/admin/events/%s/sent/' % self.event.slug).json()['data']
        self.assertEqual(data['vendor_invites'][0]['booth'], 'B12')

    def test_the_totals_add_up(self):
        EventAnnouncement.objects.create(event=self.event, sent_by=self.organiser,
                                         subject='a', body='b', recipients=12)
        EventAnnouncement.objects.create(event=self.event, sent_by=self.organiser,
                                         subject='c', body='d', recipients=30)
        totals = self._get('/auth/admin/events/%s/sent/'
                           % self.event.slug).json()['data']['totals']
        self.assertEqual(totals['announcements'], 2)
        self.assertEqual(totals['announced_to'], 42)


class AdminOnlyTests(AdminEventBase):
    """X6: none of it is reachable without an admin session."""

    def test_signed_out_reaches_nothing(self):
        for path, method in (
            ('/auth/admin/events/%s/' % self.event.slug, 'get'),
            ('/auth/admin/events/%s/tickets/' % self.event.slug, 'get'),
            ('/auth/admin/events/%s/sent/' % self.event.slug, 'get'),
        ):
            res = getattr(self.client, method)(path)
            self.assertIn(res.status_code, (400, 401, 403), path)

    def test_an_ordinary_player_reaches_nothing(self):
        res = self._get('/auth/admin/events/%s/' % self.event.slug,
                        auth=self.organiser_auth)
        self.assertIn(res.status_code, (401, 403))

    def test_the_organiser_of_the_event_is_still_not_an_admin(self):
        """Owning the event is not the same as running the platform."""
        res = self._post('/auth/admin/events/%s/state/' % self.event.slug,
                         {'action': 'cancel', 'reason': 'mine'},
                         auth=self.organiser_auth)
        self.assertIn(res.status_code, (401, 403))
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_active)

    def test_a_session_that_skipped_the_second_factor_reaches_nothing(self):
        """A password alone still opens no part of the console."""
        weak, weak_auth = a_user('ae_weak', is_staff=True, admin_role='super_admin')
        weak.login_session_2fa_at = None
        weak.save(update_fields=['login_session_2fa_at'])
        res = self._get('/auth/admin/events/%s/' % self.event.slug, auth=weak_auth)
        self.assertIn(res.status_code, (401, 403))

    def test_a_read_only_admin_can_look_but_not_act(self):
        """Reading is open to every admin role. Voiding a ticket takes a seat
        away from somebody, so it is not."""
        _mod, mod_auth = a_user('ae_mod', is_staff=True, admin_role='mod_admin')
        ticket = self._ticket()

        self.assertEqual(
            self._get('/auth/admin/events/%s/' % self.event.slug, auth=mod_auth).status_code,
            200)

        res = self._post('/auth/admin/tickets/%s/action/' % ticket.code,
                         {'action': 'void', 'reason': 'x'}, auth=mod_auth)
        self.assertEqual(res.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'valid')


class TournamentSentTests(TestCase):
    """X4, the other half: "what was sent out by tournament organizers".

    A tournament organiser sends different things from an event manager -
    scheduled reminders, addressed invitations, and codes handed out - so the
    console reads those rather than pretending they are announcements.
    """

    def setUp(self):
        self.client = APIClient()
        from vent_tournament.models import Tournament
        self.game = Games.objects.get_or_create(game_title='Free Fire')[0]
        self.admin, self.auth = a_user('ts_admin', is_staff=True, admin_role='super_admin')
        self.organiser, self.organiser_auth = a_user('ts_org')
        self.tournament = Tournament.objects.create(
            tournament_title='Naija Weekly %s' % uuid.uuid4().hex[:4],
            tournament_game=self.game, tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timezone.timedelta(days=2),
            end_date_and_time=timezone.now() + timezone.timedelta(days=3),
            tournament_type='online', tournament_access='individual',
            entry_fee='Free', is_draft=False)

    def _sent(self, auth=None):
        return self.client.get(
            '/auth/admin/tournaments/%s/sent/' % (self.tournament.slug or self.tournament.tournament_id),
            **(auth if auth is not None else self.auth))

    def test_a_scheduled_reminder_shows_its_anchor_and_its_offset(self):
        """"An hour before check-in" is what the organiser set. A computed
        timestamp would say something they never typed."""
        from vent_tournament.models import ScheduledReminder
        ScheduledReminder.objects.create(
            tournament=self.tournament, kind='check_in', subject='Check in',
            body='Check in opens in an hour.', anchor='check_in_opens',
            offset_minutes=-60, created_by=self.organiser)
        res = self._sent()
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = res.json()['data']['reminders'][0]
        self.assertEqual(row['anchor'], 'check_in_opens')
        self.assertEqual(row['offset_minutes'], -60)
        self.assertEqual(row['state'], 'scheduled')
        self.assertEqual(row['created_by']['username'], self.organiser.username)

    def test_a_reminder_that_has_gone_says_so_and_says_how_many(self):
        from vent_tournament.models import ScheduledReminder
        ScheduledReminder.objects.create(
            tournament=self.tournament, kind='custom', subject='x', body='y',
            anchor='fixed', fixed_at=timezone.now(),
            sent_at=timezone.now(), people_reached=52)
        row = self._sent().json()['data']['reminders'][0]
        self.assertEqual(row['state'], 'sent')
        self.assertEqual(row['people_reached'], 52)

    def test_a_reminder_that_was_skipped_is_not_reported_as_sent(self):
        """"Nobody to send to" is a different complaint from "sent at the wrong
        time", and support has to be able to tell them apart."""
        from vent_tournament.models import ScheduledReminder
        ScheduledReminder.objects.create(
            tournament=self.tournament, kind='custom', subject='x', body='y',
            anchor='fixed', fixed_at=timezone.now(),
            skipped_reason='nobody had checked in')
        row = self._sent().json()['data']['reminders'][0]
        self.assertEqual(row['state'], 'skipped')
        self.assertIn('nobody', row['skipped_reason'])

    def test_an_addressed_invitation_names_who_it_went_to(self):
        from vent_tournament.models import TournamentInvitation
        invitee, _ = a_user('ts_player')
        TournamentInvitation.objects.create(
            tournament=self.tournament, user=invitee, invited_by=self.organiser,
            message='Come and play', status='pending')
        row = self._sent().json()['data']['invitations'][0]
        self.assertEqual(row['to_user']['username'], invitee.username)
        self.assertEqual(row['message'], 'Come and play')
        self.assertEqual(row['invited_by']['username'], self.organiser.username)

    def test_codes_show_how_many_times_they_have_been_spent(self):
        from vent_tournament.models import TournamentInvite
        TournamentInvite.objects.create(
            tournament=self.tournament, code='LAGOS1', label='the Lagos lot',
            max_uses=5, used_count=2, created_by=self.organiser)
        row = self._sent().json()['data']['codes'][0]
        self.assertEqual(row['label'], 'the Lagos lot')
        self.assertEqual(row['used_count'], 2)
        self.assertEqual(row['max_uses'], 5)

    def test_the_totals_count_what_actually_went(self):
        from vent_tournament.models import ScheduledReminder
        ScheduledReminder.objects.create(
            tournament=self.tournament, kind='custom', subject='a', body='b',
            anchor='fixed', fixed_at=timezone.now(), sent_at=timezone.now())
        ScheduledReminder.objects.create(
            tournament=self.tournament, kind='custom', subject='c', body='d',
            anchor='fixed', fixed_at=timezone.now())
        totals = self._sent().json()['data']['totals']
        self.assertEqual(totals['reminders'], 2)
        self.assertEqual(totals['reminders_sent'], 1)

    def test_the_organiser_of_the_tournament_is_still_not_an_admin(self):
        res = self._sent(auth=self.organiser_auth)
        self.assertIn(res.status_code, (401, 403))

    def test_an_unknown_tournament_is_a_404(self):
        res = self.client.get('/auth/admin/tournaments/no-such-thing/sent/', **self.auth)
        self.assertEqual(res.status_code, 404)
