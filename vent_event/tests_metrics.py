"""Sales and attendance.

The decisions worth pinning: a refund is not a sale, an attendance rate with no
tickets is unanswerable rather than zero, and how somebody was admitted travels
with the number so an organiser can judge whether it is real.
"""
import csv
import io
from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from .models import Event, EventManager, Ticket, TicketTier


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('m-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def rows_of(response):
    """The body as it arrives, not as it was handed to the response."""
    return list(csv.reader(io.StringIO(response.content.decode('utf-8'))))


class MetricsBase(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('me_org')
        self.stranger, self.stranger_auth = a_user('me_other')
        game = Games.objects.create(game_title='EA FC ME')
        now = timezone.localtime(timezone.now())
        self.event = Event.objects.create(
            name='Metrics Probe', game=game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now() - timedelta(days=5),
            reg_end_date=timezone.now() + timedelta(days=5),
            event_date=now.date(), start_time=time(18, 0), end_time=time(22, 0),
            location='Lagos', capacity=100)
        self.general = TicketTier.objects.create(
            event=self.event, name='General', price=5000, quantity=50)
        self.vip = TicketTier.objects.create(
            event=self.event, name='VIP', price=20000, quantity=10)

        # Four general: two checked in at a gate, one by themselves, one absent.
        self.tickets = []
        for i in range(4):
            self.tickets.append(Ticket.objects.create(
                event=self.event, tier=self.general, code='ME0000%02d' % i,
                price_vc=5, price_ngn=5000,
                attendee_email='general%d@example.com' % i))
        for t in self.tickets[:2]:
            t.status = 'checked_in'
            t.checked_in_at = timezone.now()
            t.checked_in_gate = 'Gate A'
            t.checked_in_by = self.organiser
            t.save()
        self.tickets[2].status = 'checked_in'
        self.tickets[2].checked_in_at = timezone.now()
        self.tickets[2].checked_in_gate = 'self'
        self.tickets[2].save()

        # One VIP, bought by an account holder.
        holder, _ = a_user('me_vip')
        self.vip_ticket = Ticket.objects.create(
            event=self.event, tier=self.vip, code='MEVIP001', user=holder,
            price_vc=20, price_ngn=20000, attendee_email='vip@example.com')

        # One refunded. Not a sale that happened.
        self.refunded = Ticket.objects.create(
            event=self.event, tier=self.general, code='MEREF001',
            price_vc=5, price_ngn=5000, status='refunded')

    def metrics(self, auth=None):
        return self.client.get('/event/%s/metrics/' % self.event.event_id,
                               **(auth or self.auth))


class MetricsTests(MetricsBase):
    def test_the_organiser_reads_the_numbers(self):
        res = self.metrics()
        self.assertEqual(res.status_code, 200, res.data)

    def test_a_refund_is_not_a_sale(self):
        # Five tickets exist; one was refunded. Reporting five overstates both
        # the money and the room.
        d = self.metrics().data['data']
        self.assertEqual(d['tickets']['issued'], 5)   # 4 general + 1 vip
        self.assertEqual(d['tickets']['refunded'], 1)
        self.assertEqual(d['revenue']['vc'], 4 * 5 + 20)

    def test_attendance_is_counted_and_rated(self):
        d = self.metrics().data['data']
        self.assertEqual(d['tickets']['checked_in'], 3)
        self.assertEqual(d['tickets']['not_arrived'], 2)
        self.assertEqual(d['tickets']['attendance_rate'], 60.0)

    def test_how_somebody_was_admitted_travels_with_the_number(self):
        # A steward scanning and an attendee marking themselves present are
        # different evidence.
        d = self.metrics().data['data']
        self.assertEqual(d['tickets']['at_door'], 2)
        self.assertEqual(d['tickets']['self_checked_in'], 1)

    def test_an_empty_event_has_no_attendance_rate_rather_than_zero(self):
        Ticket.objects.filter(event=self.event).delete()
        d = self.metrics().data['data']
        self.assertEqual(d['tickets']['issued'], 0)
        self.assertIsNone(d['tickets']['attendance_rate'])

    def test_guests_and_account_holders_are_separated(self):
        d = self.metrics().data['data']
        self.assertEqual(d['tickets']['account_holders'], 1)
        self.assertEqual(d['tickets']['guests'], 4)

    def test_each_tier_reports_its_own_sales(self):
        tiers = {t['name']: t for t in self.metrics().data['data']['tiers']}
        self.assertEqual(tiers['General']['sold'], 4)
        self.assertEqual(tiers['General']['remaining'], 46)
        self.assertEqual(tiers['VIP']['sold'], 1)
        self.assertEqual(tiers['VIP']['revenue_vc'], 20)

    def test_a_tier_with_no_cap_reports_no_remaining(self):
        unlimited = TicketTier.objects.create(
            event=self.event, name='Open', price=0, quantity=0)
        tiers = {t['name']: t for t in self.metrics().data['data']['tiers']}
        self.assertIsNone(tiers['Open']['remaining'])
        self.assertEqual(unlimited.name, 'Open')

    def test_sales_are_grouped_by_day(self):
        days = self.metrics().data['data']['sales_by_day']
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0]['tickets'], 5)

    def test_the_event_capacity_is_reported_against_what_sold(self):
        cap = self.metrics().data['data']['capacity']
        self.assertEqual(cap['event_capacity'], 100)
        self.assertEqual(cap['remaining'], 95)

    # ----------------------------------------------------------- refusals

    def test_a_stranger_reads_nothing(self):
        self.assertEqual(self.metrics(auth=self.stranger_auth).status_code, 403)

    def test_signed_out_reads_nothing(self):
        res = self.client.get('/event/%s/metrics/' % self.event.event_id)
        self.assertEqual(res.status_code, 401)

    def test_door_staff_may_read_them(self):
        steward, steward_auth = a_user('me_door')
        EventManager.objects.create(event=self.event, user=steward, role='door')
        self.assertEqual(self.metrics(auth=steward_auth).status_code, 200)

    def test_an_unknown_event_is_a_404(self):
        res = self.client.get('/event/999999/metrics/', **self.auth)
        self.assertEqual(res.status_code, 404)


class MetricsExportTests(MetricsBase):
    def get(self, sheet, auth=None):
        return self.client.get(
            '/event/%s/metrics/export/' % self.event.event_id,
            {'sheet': sheet}, **(auth or self.auth))

    def test_the_attendee_sheet_downloads_as_real_csv(self):
        res = self.get('attendees')
        self.assertEqual(res.status_code, 200)
        self.assertIn('attachment', res['Content-Disposition'])
        body = res.content.decode('utf-8')
        self.assertTrue(body.startswith('code,'), body[:60])
        self.assertFalse(body.rstrip().endswith('"'))

    def test_the_attendee_sheet_keeps_refunded_rows(self):
        # This is the sheet somebody reconciles against, and a missing row
        # reads as a missing person rather than a refund.
        rows = rows_of(self.get('attendees'))
        self.assertEqual(len(rows), 7)  # header plus six tickets
        statuses = {r[7] for r in rows[1:]}
        self.assertIn('refunded', statuses)

    def test_the_gate_is_in_the_attendee_sheet(self):
        rows = rows_of(self.get('attendees'))
        gates = {r[12] for r in rows[1:]}
        self.assertIn('self', gates)
        self.assertIn('Gate A', gates)

    def test_the_tier_sheet_carries_the_money(self):
        rows = rows_of(self.get('tiers'))
        self.assertEqual(rows[0][0], 'tier')
        names = {r[0] for r in rows[1:]}
        self.assertEqual(names, {'General', 'VIP'})

    def test_the_sales_sheet_is_one_row_per_day(self):
        rows = rows_of(self.get('sales'))
        self.assertEqual(rows[0], ['date', 'tickets'])
        self.assertEqual(len(rows), 2)

    def test_an_unknown_sheet_is_refused(self):
        self.assertEqual(self.get('everything').status_code, 400)

    def test_a_stranger_downloads_nothing(self):
        self.assertEqual(self.get('attendees', auth=self.stranger_auth).status_code, 403)
