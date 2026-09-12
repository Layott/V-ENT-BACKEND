# -*- coding: utf-8 -*-
"""The door can ask about a ticket without admitting anybody.

Every test here is shaped by what actually happened at RIVALRY SERIES SEASON 2
on 4 and 5 September 2026: one check-in recorded out of 1422 tickets, because
the door filtered a list held in the browser and a ticket bought after the page
loaded could not be found.

The case that matters most is `SearchFindsATicketBoughtAfterTheListLoaded`. That
is the actual bug, stated as a test.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from .models import Event, EventManager, TicketTier, Ticket, DoorLookup


def a_user(name):
    """The same shape `tests_door.a_user` builds.

    Copied rather than invented: a hand-written fixture is how a test ends up
    passing against a request nothing makes.
    """
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('l-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user


def _auth(user):
    return {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class DoorFixture(TestCase):
    """One event, one organiser, one steward, one stranger, two tickets."""

    def setUp(self):
        self.organiser = a_user('lu_organiser')
        self.steward = a_user('lu_steward')
        self.stranger = a_user('lu_stranger')

        now = timezone.now()
        self.event = Event.objects.create(
            name='Rivalry Series Season 2', creator=self.organiser,
            event_type='physical', desc='A door to search.', entry_fee=0,
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=5),
            reg_start_date=now - timedelta(days=2),
            reg_end_date=now + timedelta(hours=4),
        )

        self.tier = TicketTier.objects.create(
            event=self.event, name='General Admission', price=0, quantity=2000)

        EventManager.objects.create(event=self.event, user=self.steward,
                                    role='door')

        self.ginnie = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-NWKL9Z2K', price_vc=0,
            attendee_name='Ginnie', attendee_email='regina.e.okoko@gmail.com',
            attendee_phone='08030000001')
        self.riley = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-CXENEJ3Q', price_vc=0,
            attendee_name='Riley', attendee_email='samierasaq@gmail.com',
            attendee_phone='08030000002')


class LookupDoesNotAdmit(DoorFixture):
    """The whole reason this endpoint exists."""

    def test_lookup_leaves_the_ticket_untouched(self):
        res = self.client.get('/event/ticket/VT-NWKL9Z2K/lookup/',
                              **_auth(self.steward))
        self.assertEqual(res.status_code, 200)
        self.ginnie.refresh_from_db()
        self.assertEqual(self.ginnie.status, 'valid')
        self.assertIsNone(self.ginnie.checked_in_at)
        self.assertEqual(self.ginnie.checked_in_gate, '')

    def test_lookup_says_whether_they_would_be_admitted(self):
        res = self.client.get('/event/ticket/VT-NWKL9Z2K/lookup/',
                              **_auth(self.steward))
        self.assertTrue(res.json()['data']['admissible'])
        self.assertFalse(res.json()['data']['already_checked_in'])

    def test_a_used_ticket_is_reported_used_without_a_409(self):
        """A lookup is not a refusal. It reports, it does not judge.

        `check-in/` answers 409 for a used ticket because it is being asked to
        admit somebody. A lookup asked the same question answers 200 and says
        so in the body, because a steward checking a name has not asked for
        anybody to go through.
        """
        self.ginnie.status = 'checked_in'
        self.ginnie.checked_in_at = timezone.now()
        self.ginnie.checked_in_gate = 'Main'
        self.ginnie.save(update_fields=['status', 'checked_in_at',
                                        'checked_in_gate'])
        res = self.client.get('/event/ticket/VT-NWKL9Z2K/lookup/',
                              **_auth(self.steward))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['data']['already_checked_in'])
        self.assertFalse(res.json()['data']['admissible'])

    def test_an_unknown_code_is_404_and_admits_nobody(self):
        res = self.client.get('/event/ticket/VT-ZZZZZZZZ/lookup/',
                              **_auth(self.steward))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(Ticket.objects.filter(status='checked_in').count(), 0)


class DoorSearch(DoorFixture):

    def test_finds_by_partial_name(self):
        res = self.client.get('/event/%d/door-search/?q=Gin' % self.event.event_id,
                              **_auth(self.steward))
        self.assertEqual(res.status_code, 200)
        names = [r['attendee_name'] for r in res.json()['data']['attendees']]
        self.assertEqual(names, ['Ginnie'])

    def test_finds_by_email(self):
        res = self.client.get(
            '/event/%d/door-search/?q=samierasaq' % self.event.event_id,
            **_auth(self.steward))
        self.assertEqual([r['code'] for r in res.json()['data']['attendees']],
                         ['VT-CXENEJ3Q'])

    def test_finds_by_phone(self):
        res = self.client.get(
            '/event/%d/door-search/?q=08030000002' % self.event.event_id,
            **_auth(self.steward))
        self.assertEqual([r['code'] for r in res.json()['data']['attendees']],
                         ['VT-CXENEJ3Q'])

    def test_finds_by_code_regardless_of_case(self):
        res = self.client.get(
            '/event/%d/door-search/?q=vt-nwkl9z2k' % self.event.event_id,
            **_auth(self.steward))
        self.assertEqual([r['code'] for r in res.json()['data']['attendees']],
                         ['VT-NWKL9Z2K'])

    def test_searching_admits_nobody(self):
        self.client.get('/event/%d/door-search/?q=Ginnie' % self.event.event_id,
                        **_auth(self.steward))
        self.assertEqual(Ticket.objects.filter(status='checked_in').count(), 0)

    def test_a_slug_addresses_the_event_as_well_as_a_number(self):
        res = self.client.get('/event/%s/door-search/?q=Ginnie' % self.event.slug,
                              **_auth(self.steward))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['count'], 1)

    def test_a_term_too_short_is_refused_by_code(self):
        res = self.client.get('/event/%d/door-search/?q=G' % self.event.event_id,
                              **_auth(self.steward))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'TERM_TOO_SHORT')

    def test_no_match_is_an_empty_list_not_an_error(self):
        """A miss is a normal answer, and the page has to be able to say so.

        Answering 404 here would make "nobody by that name" indistinguishable
        from "the event does not exist", and the door page would show the wrong
        sentence for the more common of the two.
        """
        res = self.client.get(
            '/event/%d/door-search/?q=Nobody' % self.event.event_id,
            **_auth(self.steward))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['count'], 0)
        self.assertEqual(res.json()['data']['attendees'], [])


class SearchFindsATicketBoughtAfterTheListLoaded(DoorFixture):
    """The actual bug from 4 and 5 September, as a test.

    A steward opens the door page. The list downloads. Somebody then buys a
    ticket and walks up to the gate. Before this change the page filtered its
    snapshot, found nothing and said "Nobody matches that search" without asking
    the server, and the person was turned away holding a valid ticket.
    """

    def test_a_ticket_created_after_the_snapshot_is_still_found(self):
        snapshot_codes = set(
            Ticket.objects.filter(event=self.event).values_list('code', flat=True))

        latecomer = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-LATE0001', price_vc=0,
            attendee_name='Bought At The Gate',
            attendee_email='late@example.com')
        self.assertNotIn(latecomer.code, snapshot_codes)

        res = self.client.get(
            '/event/%d/door-search/?q=Bought' % self.event.event_id,
            **_auth(self.steward))
        self.assertEqual(res.status_code, 200)
        self.assertEqual([r['code'] for r in res.json()['data']['attendees']],
                         ['VT-LATE0001'])


class LookupsAreRecorded(DoorFixture):
    """CEO, 6 September: "every lookup becomes a log line - Do it"."""

    def test_a_search_writes_a_row_carrying_the_term(self):
        self.client.get('/event/%d/door-search/?q=Ginnie&gate=Main'
                        % self.event.event_id, **_auth(self.steward))
        row = DoorLookup.objects.get()
        self.assertEqual(row.term, 'Ginnie')
        self.assertEqual(row.matched, 1)
        self.assertEqual(row.asked_by, self.steward)
        self.assertEqual(row.gate, 'Main')
        self.assertEqual(row.ticket, self.ginnie)

    def test_a_miss_is_recorded_as_a_miss(self):
        """The interesting row. A run of these is a door in trouble."""
        self.client.get('/event/%d/door-search/?q=Nobody' % self.event.event_id,
                        **_auth(self.steward))
        row = DoorLookup.objects.get()
        self.assertEqual(row.matched, 0)
        self.assertIsNone(row.ticket)

    def test_an_ambiguous_term_records_no_single_ticket(self):
        Ticket.objects.create(event=self.event, tier=self.tier,
                              code='VT-GIN00002', price_vc=0, attendee_name='Ginnifer',
                              attendee_email='gin2@example.com')
        self.client.get('/event/%d/door-search/?q=Gin' % self.event.event_id,
                        **_auth(self.steward))
        row = DoorLookup.objects.get()
        self.assertEqual(row.matched, 2)
        self.assertIsNone(row.ticket)

    def test_a_code_lookup_is_recorded_too(self):
        self.client.get('/event/ticket/VT-NWKL9Z2K/lookup/', **_auth(self.steward))
        self.assertEqual(DoorLookup.objects.count(), 1)

    def test_the_organiser_can_read_the_lookups_back(self):
        self.client.get('/event/%d/door-search/?q=Ginnie' % self.event.event_id,
                        **_auth(self.steward))
        self.client.get('/event/%d/door-search/?q=Nobody' % self.event.event_id,
                        **_auth(self.steward))
        res = self.client.get('/event/%d/door-lookups/' % self.event.event_id,
                              **_auth(self.organiser))
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['misses'], 1)
        # Newest first, so a door reads top down.
        self.assertEqual(data['lookups'][0]['term'], 'Nobody')
        self.assertEqual(data['lookups'][0]['asked_by'], 'lu_steward')

    def test_misses_can_be_read_on_their_own(self):
        self.client.get('/event/%d/door-search/?q=Ginnie' % self.event.event_id,
                        **_auth(self.steward))
        self.client.get('/event/%d/door-search/?q=Nobody' % self.event.event_id,
                        **_auth(self.steward))
        res = self.client.get(
            '/event/%d/door-lookups/?misses=1' % self.event.event_id,
            **_auth(self.organiser))
        terms = [row['term'] for row in res.json()['data']['lookups']]
        self.assertEqual(terms, ['Nobody'])

    def test_a_steward_may_not_read_the_door_log(self):
        """Admitting people is not the same as reading what everyone typed."""
        res = self.client.get('/event/%d/door-lookups/' % self.event.event_id,
                              **_auth(self.steward))
        self.assertEqual(res.status_code, 403)


class WhoMayAsk(DoorFixture):

    def test_a_stranger_cannot_search(self):
        res = self.client.get('/event/%d/door-search/?q=Ginnie'
                              % self.event.event_id, **_auth(self.stranger))
        self.assertEqual(res.status_code, 403)

    def test_a_stranger_cannot_look_a_code_up(self):
        res = self.client.get('/event/ticket/VT-NWKL9Z2K/lookup/',
                              **_auth(self.stranger))
        self.assertEqual(res.status_code, 403)

    def test_signed_out_is_401_everywhere(self):
        for url in ('/event/%d/door-search/?q=Ginnie' % self.event.event_id,
                    '/event/ticket/VT-NWKL9Z2K/lookup/',
                    '/event/%d/door-summary/' % self.event.event_id,
                    '/event/%d/door-lookups/' % self.event.event_id):
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_a_stranger_searching_leaves_no_row_behind(self):
        """A refusal is not a lookup, and must not pollute the door log."""
        self.client.get('/event/%d/door-search/?q=Ginnie' % self.event.event_id,
                        **_auth(self.stranger))
        self.assertEqual(DoorLookup.objects.count(), 0)

    def test_door_staff_may_search_without_running_the_event(self):
        res = self.client.get('/event/%d/door-search/?q=Ginnie'
                              % self.event.event_id, **_auth(self.steward))
        self.assertEqual(res.status_code, 200)
