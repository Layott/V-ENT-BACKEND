"""Deleting an event without destroying it.

The twin of `vent_tournament/tests_soft_delete.py`, case for case. Written in
the same pass on purpose: this repo's most repeated fault is a capability built
on one of these two models and forgotten on the other, and a test file that
exists on one side only is how that goes unnoticed for weeks.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Organization, Users

from .models import Event, Ticket, TicketTier


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('tok-%s' % name)[:16], **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class SoftDeleteEventTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('esd_owner')
        self.stranger, self.stranger_auth = a_user('esd_stranger')
        self.admin, self.admin_auth = a_user('esd_admin', is_staff=True,
                                             admin_role='super_admin')
        self.event = self.make()

    def make(self, name='Soft Delete Con'):
        now = timezone.now()
        return Event.objects.create(
            name=name, creator=self.owner, event_type='physical',
            desc='An event.', entry_fee=0,
            start_date=now + timedelta(days=3),
            end_date=now + timedelta(days=3, hours=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=2),
        )

    def a_ticket(self, event, price_vc, code):
        tier = TicketTier.objects.create(event=event, name='General',
                                         price=price_vc, quantity=100)
        return Ticket.objects.create(event=event, tier=tier, user=self.stranger,
                                     code=code, price_vc=price_vc,
                                     attendee_name='Ada Nwosu')

    def url(self, suffix='delete/', event=None):
        return '/event/%s/%s' % ((event or self.event).event_id, suffix)

    # ------------------------------------------------------------ the delete

    def test_the_creator_can_delete_their_own_event(self):
        res = self.client.post(self.url(), content_type='application/json',
                               **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        row = Event.all_objects.get(pk=self.event.pk)
        self.assertIsNotNone(row.deleted_at)
        self.assertEqual(row.deleted_by_id, self.owner.user_id)

    def test_an_ordinary_query_cannot_see_it_at_all(self):
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(Event.all_objects.filter(pk=self.event.pk).exists())

    def test_a_ticket_can_still_reach_the_event_it_was_sold_for(self):
        """`base_manager_name` is the unfiltered manager on purpose. Without
        it a deleted event would not disappear from view, it would break every
        row pointing at it."""
        ticket = self.a_ticket(self.event, 0, 'VT-SD-0001')
        self.client.post(self.url(), content_type='application/json',
                         data={'confirm': True}, **self.owner_auth)
        again = Ticket.objects.get(pk=ticket.pk)
        self.assertEqual(again.event.name, 'Soft Delete Con')

    def test_its_own_address_stops_answering(self):
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        res = self.client.get('/event/%s/' % self.event.event_id)
        self.assertEqual(res.status_code, 404, res.content)

    def test_a_stranger_cannot_delete_it(self):
        res = self.client.post(self.url(), content_type='application/json',
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_the_organisations_owner_can(self):
        org = Organization.objects.create(org_name='Vermillion Probe',
                                          org_creator=self.stranger,
                                          org_owner=self.stranger)
        self.event.organization = org
        self.event.save(update_fields=['organization'])
        res = self.client.post(self.url(), content_type='application/json',
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 200, res.content)

    def test_an_admin_can_delete_somebody_elses(self):
        res = self.client.post(self.url(), content_type='application/json',
                               **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)

    # ------------------------------------------------- money and seats first

    def test_a_paid_ticket_refuses_the_delete_outright(self):
        self.a_ticket(self.event, 1500, 'VT-SD-PAID1')
        res = self.client.post(self.url(), data={'confirm': True},
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'PAID_ENTRANTS')
        self.assertIsNone(Event.all_objects.get(pk=self.event.pk).deleted_at)

    def test_free_tickets_ask_a_second_time(self):
        self.a_ticket(self.event, 0, 'VT-SD-FREE1')
        res = self.client.post(self.url(), content_type='application/json',
                               **self.owner_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'CONFIRM_REQUIRED')
        self.assertEqual(res.json()['data']['unpaid'], 1)

        again = self.client.post(self.url(), data={'confirm': True},
                                 content_type='application/json', **self.owner_auth)
        self.assertEqual(again.status_code, 200, again.content)

    # ----------------------------------------------------------- the restore

    def test_an_admin_can_restore_it(self):
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        res = self.client.post(self.url('restore/'), content_type='application/json',
                               **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(Event.objects.get(pk=self.event.pk).deleted_at)

    def test_the_creator_cannot_restore_their_own(self):
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        res = self.client.post(self.url('restore/'), content_type='application/json',
                               **self.owner_auth)
        self.assertEqual(res.status_code, 403, res.content)

    # ------------------------------------------------------------- the lists

    def test_it_leaves_the_public_listing(self):
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        res = self.client.get('/event/get-all-events/')
        self.assertNotIn('Soft Delete Con', res.content.decode('utf-8', 'replace'))

    def test_the_admin_console_can_still_find_it(self):
        self.client.post(self.url(), content_type='application/json',
                         **self.owner_auth)
        res = self.client.get('/auth/admin/events/?status=deleted', **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['data']['results']
        self.assertEqual([r['name'] for r in rows], ['Soft Delete Con'])
        self.assertEqual(rows[0]['status'], 'deleted')
        self.assertEqual(rows[0]['deleted_by'], 'esd_owner')
