"""What nearly happened, which is the half the tickets table cannot report.

CEO, 7 September 2026: "organizers hsould be able o see mad metric for thier
events and tickets, how many clicks, how many people opened it up, how many
tapped buy, how many check out vendor, stuff like that, very detailed stuff."

The decisions worth pinning:

- **`sold` is counted, everything above it is reported.** A browser beacon can
  fire twice, be a crawler, or never arrive. The bottom of the funnel is the
  one number an organiser reconciles money against, so it comes from the
  tickets table and nothing else.
- **A rate with no denominator is unanswerable, not zero.** Rounding "nobody
  has opened the page" into "0% convert" is how an organiser concludes their
  checkout is broken on a day nothing happened.
- **A day, not a person.** No address, no user agent, no row per visitor.
- **Reading these numbers is organiser-only, writing them is public.** The
  people being counted are the ones without accounts, which is the point.
"""
from datetime import date, time, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Users

from . import funnel
from .models import Event, EventFunnelDay, Ticket, TicketTier, Vendor


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('f-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class FunnelBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser, self.auth = a_user('fn_org')
        self.stranger, self.stranger_auth = a_user('fn_other')
        game = Games.objects.create(game_title='EA FC FN')
        now = timezone.localtime(timezone.now())
        self.event = Event.objects.create(
            name='Funnel Probe', game=game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now() - timedelta(days=5),
            reg_end_date=timezone.now() + timedelta(days=5),
            event_date=now.date(), start_time=time(18, 0), end_time=time(22, 0),
            location='Lagos', capacity=100)
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=5000, quantity=50)

    def hit(self, step, **body):
        return self.client.post('/event/%s/track/' % self.event.slug,
                                dict(step=step, **body), format='json')


class RecordingTests(FunnelBase):
    def test_one_open_is_one_count(self):
        self.hit('page_open')
        row = EventFunnelDay.objects.get()
        self.assertEqual(row.step, 'page_open')
        self.assertEqual(row.count, 1)
        self.assertEqual(row.people, 0)

    def test_the_same_step_twice_in_a_day_is_one_row_and_two_counts(self):
        self.hit('page_open')
        self.hit('page_open')
        self.assertEqual(EventFunnelDay.objects.count(), 1)
        self.assertEqual(EventFunnelDay.objects.get().count, 2)

    def test_a_browser_that_says_it_is_new_moves_the_people_column(self):
        self.hit('page_open', first_time=True)
        self.hit('page_open', first_time=False)
        row = EventFunnelDay.objects.get()
        self.assertEqual(row.count, 2)
        self.assertEqual(row.people, 1)

    def test_no_account_is_needed_to_be_counted(self):
        """The whole reason this is public. A signed-out visitor opening the
        page is the arrival worth counting, not an edge case."""
        res = self.hit('page_open')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['data']['recorded'])

    def test_a_step_this_build_does_not_know_is_dropped_not_stored(self):
        res = self.hit('teleported')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['data']['recorded'])
        self.assertEqual(EventFunnelDay.objects.count(), 0)

    def test_an_event_that_does_not_exist_answers_200_and_records_nothing(self):
        res = self.client.post('/event/no-such-event/track/',
                               {'step': 'page_open'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['data']['recorded'])

    def test_a_stall_visit_keeps_which_stall(self):
        self.hit('vendor_stall', ref='mama-t-kitchen')
        self.hit('vendor_stall', ref='mama-t-kitchen')
        self.hit('vendor_stall', ref='drinks-corner')
        self.assertEqual(EventFunnelDay.objects.filter(step='vendor_stall').count(), 2)

    def test_a_step_with_no_sub_thing_drops_the_ref(self):
        """Otherwise a bug on the page turns one step into ten thousand rows."""
        self.hit('page_open', ref='something')
        self.hit('page_open', ref='something-else')
        self.assertEqual(EventFunnelDay.objects.count(), 1)
        self.assertEqual(EventFunnelDay.objects.get().ref, '')

    def test_two_days_are_two_rows(self):
        funnel.record(self.event, 'page_open', when=date(2026, 9, 1))
        funnel.record(self.event, 'page_open', when=date(2026, 9, 2))
        self.assertEqual(EventFunnelDay.objects.count(), 2)


class SummaryTests(FunnelBase):
    def test_the_bottom_of_the_funnel_comes_from_the_tickets_not_a_beacon(self):
        """A beacon can fire twice. The number an organiser reconciles money
        against cannot be allowed to."""
        for i in range(3):
            Ticket.objects.create(event=self.event, tier=self.tier,
                                  code='FN0000%02d' % i, price_vc=5,
                                  price_ngn=5000)
        data = funnel.summary(self.event)
        self.assertEqual(data['sold'], 3)
        self.assertEqual(data['steps'][-1], {'step': 'sold', 'count': 3,
                                             'people': 3})

    def test_a_refunded_ticket_is_not_a_sale(self):
        Ticket.objects.create(event=self.event, tier=self.tier, code='FNR001',
                              price_vc=5, price_ngn=5000, status='refunded')
        self.assertEqual(funnel.summary(self.event)['sold'], 0)

    def test_the_steps_come_back_in_journey_order_even_when_empty(self):
        data = funnel.summary(self.event)
        self.assertEqual([s['step'] for s in data['steps']][:4],
                         ['page_open', 'ticket_open', 'buy_tap',
                          'checkout_start'])
        self.assertTrue(all(s['count'] == 0 for s in data['steps']))

    def test_conversion_is_of_the_step_above(self):
        for _ in range(10):
            funnel.record(self.event, 'page_open')
        for _ in range(4):
            funnel.record(self.event, 'buy_tap')
        for _ in range(2):
            funnel.record(self.event, 'checkout_start')
        Ticket.objects.create(event=self.event, tier=self.tier, code='FNC001',
                              price_vc=5, price_ngn=5000)
        c = funnel.summary(self.event)['conversion']
        self.assertEqual(c['open_to_buy'], 40.0)
        self.assertEqual(c['buy_to_checkout'], 50.0)
        self.assertEqual(c['checkout_to_sold'], 50.0)
        self.assertEqual(c['open_to_sold'], 10.0)

    def test_a_rate_with_nothing_above_it_is_unanswerable_not_zero(self):
        """Zero per cent and "nobody has been here" are different facts, and
        rounding the second into the first reads as a broken checkout."""
        c = funnel.summary(self.event)['conversion']
        self.assertIsNone(c['open_to_buy'])
        self.assertIsNone(c['checkout_to_sold'])

    def test_stalls_are_named_not_slugged(self):
        stall = Vendor.objects.create(event=self.event, owner=self.stranger,
                                      name='Mama T Kitchen', status='approved')
        funnel.record(self.event, 'vendor_stall', ref=stall.slug)
        funnel.record(self.event, 'vendor_stall', ref=stall.slug)
        stalls = funnel.summary(self.event)['stalls']
        self.assertEqual(len(stalls), 1)
        self.assertEqual(stalls[0]['name'], 'Mama T Kitchen')
        self.assertEqual(stalls[0]['visits'], 2)

    def test_a_stall_that_has_since_gone_keeps_its_slug_rather_than_a_blank(self):
        funnel.record(self.event, 'vendor_stall', ref='deleted-stall')
        stalls = funnel.summary(self.event)['stalls']
        self.assertEqual(stalls[0]['name'], 'deleted-stall')

    def test_stalls_are_ordered_by_who_got_walked_to_most(self):
        funnel.record(self.event, 'vendor_stall', ref='quiet')
        for _ in range(5):
            funnel.record(self.event, 'vendor_stall', ref='busy')
        self.assertEqual([s['stall'] for s in funnel.summary(self.event)['stalls']],
                         ['busy', 'quiet'])

    def test_the_curve_is_one_row_per_day_with_a_column_per_step(self):
        funnel.record(self.event, 'page_open', when=date(2026, 9, 1))
        funnel.record(self.event, 'page_open', when=date(2026, 9, 1))
        funnel.record(self.event, 'buy_tap', when=date(2026, 9, 1))
        funnel.record(self.event, 'page_open', when=date(2026, 9, 3))
        days = funnel.summary(self.event)['by_day']
        self.assertEqual([d['date'] for d in days], ['2026-09-01', '2026-09-03'])
        self.assertEqual(days[0]['page_open'], 2)
        self.assertEqual(days[0]['buy_tap'], 1)
        # No zero row invented for the 2nd. A gap is a real thing to see, and
        # filling it would bury the day nothing happened.
        self.assertNotIn('buy_tap', days[1])


class WhoMayReadTests(FunnelBase):
    def test_the_organiser_sees_the_funnel_on_the_metrics_endpoint(self):
        funnel.record(self.event, 'page_open')
        res = self.client.get('/event/%s/metrics/' % self.event.slug, **self.auth)
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']['funnel']
        self.assertEqual(data['steps'][0]['count'], 1)

    def test_a_stranger_cannot_read_the_numbers_they_helped_create(self):
        res = self.client.get('/event/%s/metrics/' % self.event.slug,
                              **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_cannot_read_them_either(self):
        res = self.client.get('/event/%s/metrics/' % self.event.slug)
        self.assertEqual(res.status_code, 401)


class ExportTests(FunnelBase):
    def test_the_funnel_downloads_as_a_real_csv(self):
        funnel.record(self.event, 'page_open', when=date(2026, 9, 1))
        funnel.record(self.event, 'buy_tap', when=date(2026, 9, 1))
        res = self.client.get(
            '/event/%s/metrics/export/?sheet=funnel' % self.event.slug,
            **self.auth)
        self.assertEqual(res.status_code, 200)
        body = res.content.decode('utf-8')
        # An HttpResponse, not a DRF Response: rendered through the JSON
        # renderer this arrives as a quoted string with escaped newlines.
        self.assertTrue(body.startswith('date,page_open'))
        self.assertIn('2026-09-01', body)
