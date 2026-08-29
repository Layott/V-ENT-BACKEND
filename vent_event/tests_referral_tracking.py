"""An influencer link that counts what it brought in.

CEO, 29 August 2026: "apart from the code for influencers, having an option for
links also that can be tracked, is good."

Before this, EventReferral existed with a code and a comment saying it goes in
/events/x?ref=CODE, and:

  - nothing on the site read ?ref=, so an arrival through it was
    indistinguishable from any other arrival;
  - EventReferral.sold was read in two places and incremented in none.

So every link an organiser handed out reported nought tickets forever, which is
the same answer a link nobody clicked would give. These tests are about the
difference between those two answers.
"""
import json
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from .models import Event, EventReferral, ReferralDay, Ticket, TicketTier


def a_user(name):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ReferralTrackingTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Lagos Meetup', creator=self.owner, event_type='physical',
            desc='A meetup.', location='Lagos',
            start_date=now + timedelta(days=10), end_date=now + timedelta(days=10),
        )
        # Free, because guest checkout refuses a paid ticket unless Paystack
        # is configured, and what is under test here is who gets the credit for
        # a sale rather than how it was paid for.
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)
        self.link = EventReferral.objects.create(
            event=self.event, name='Big Streamer', code='BIGST')
        self.other = EventReferral.objects.create(
            event=self.event, name='Small Streamer', code='SMALL')

    def _visit(self, code, first_time=False):
        return self.client.post(
            '/event/%s/ref/%s/visit/' % (self.event.slug, code),
            data=json.dumps({'first_time': first_time}),
            content_type='application/json')

    def _buy(self, quantity=1, ref=None, email='buyer@vent.test'):
        body = {'tier_id': self.tier.id, 'quantity': quantity, 'email': email}
        if ref is not None:
            body['ref'] = ref
        return self.client.post(
            '/event/%s/guest-buy/' % self.event.slug,
            data=json.dumps(body), content_type='application/json')

    # ------------------------------------------------------------------ visits
    def test_an_arrival_through_a_link_is_counted(self):
        res = self._visit('BIGST', first_time=True)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['data']['recorded'])
        day = ReferralDay.objects.get(referral=self.link)
        self.assertEqual(day.visits, 1)
        self.assertEqual(day.visitors, 1)

    def test_a_returning_browser_is_a_visit_but_not_a_visitor(self):
        for first in (True, False, False):
            self._visit('BIGST', first_time=first)
        day = ReferralDay.objects.get(referral=self.link)
        self.assertEqual(day.visits, 3)
        self.assertEqual(day.visitors, 1)

    def test_the_code_is_not_case_sensitive(self):
        """It is read off a video and typed by hand as often as it is clicked."""
        self.assertTrue(self._visit('bigst').json()['data']['recorded'])

    def test_a_switched_off_link_records_nothing(self):
        self.link.is_active = False
        self.link.save(update_fields=['is_active'])
        self.assertFalse(self._visit('BIGST').json()['data']['recorded'])
        self.assertEqual(ReferralDay.objects.count(), 0)

    def test_an_unknown_code_answers_200_and_records_nothing(self):
        """A wrong code is a stale link off an old post far more often than it
        is an attack, and answering differently for the real ones would let
        anybody enumerate an organiser's influencer list from outside."""
        res = self._visit('NOPE')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['data']['recorded'])
        self.assertEqual(ReferralDay.objects.count(), 0)

    def test_recording_a_visit_needs_no_account(self):
        """Somebody arriving through an influencer's link is by definition
        somebody who has never been here."""
        self.assertEqual(self._visit('BIGST').status_code, 200)

    def test_visits_accumulate_on_one_row_per_day(self):
        for _ in range(5):
            self._visit('BIGST')
        self.assertEqual(ReferralDay.objects.filter(referral=self.link).count(), 1)
        self.assertEqual(ReferralDay.objects.get(referral=self.link).visits, 5)

    def test_two_links_are_counted_apart(self):
        self._visit('BIGST')
        for _ in range(3):
            self._visit('SMALL')
        self.assertEqual(ReferralDay.objects.get(referral=self.link).visits, 1)
        self.assertEqual(ReferralDay.objects.get(referral=self.other).visits, 3)

    # ------------------------------------------------------------ attribution
    def test_a_guest_sale_credits_the_link(self):
        res = self._buy(quantity=2, ref='BIGST', email='b1@vent.test')
        self.assertEqual(res.status_code, 201, res.content[:400])
        self.assertEqual(Ticket.objects.filter(referral=self.link).count(), 2)
        self.link.refresh_from_db()
        self.assertEqual(self.link.sold, 2)

    def test_a_sale_with_no_ref_credits_nobody(self):
        self._buy(quantity=1, email='b2@vent.test')
        self.assertEqual(Ticket.objects.filter(referral__isnull=False).count(), 0)

    def test_an_unknown_ref_does_not_refuse_the_sale(self):
        """A stale code is never a reason to refuse somebody's money."""
        res = self._buy(quantity=1, ref='GONE', email='b3@vent.test')
        self.assertEqual(res.status_code, 201, res.content[:400])
        self.assertEqual(Ticket.objects.filter(referral__isnull=False).count(), 0)

    def test_a_switched_off_link_credits_nothing(self):
        self.link.is_active = False
        self.link.save(update_fields=['is_active'])
        self._buy(quantity=1, ref='BIGST', email='b4@vent.test')
        self.assertEqual(Ticket.objects.filter(referral=self.link).count(), 0)

    # ------------------------------------------------------------------ report
    def test_the_organiser_sees_visits_sales_and_conversion(self):
        for _ in range(10):
            self._visit('BIGST')
        self._buy(quantity=2, ref='BIGST', email='b5@vent.test')

        res = self.client.get('/event/%s/referrals/' % self.event.slug,
                              **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        rows = {r['code']: r for r in res.json()['data']['results']}
        self.assertEqual(rows['BIGST']['visits'], 10)
        self.assertEqual(rows['BIGST']['tickets_sold'], 2)
        self.assertEqual(rows['BIGST']['conversion'], 20.0)
        self.assertEqual(rows['SMALL']['tickets_sold'], 0)
        self.assertIsNone(rows['SMALL']['conversion'])

    def test_a_refunded_ticket_stops_counting(self):
        """The number is counted from the tickets, so a refund corrects itself.
        A counter incremented at the till would stay wrong forever."""
        self._buy(quantity=2, ref='BIGST', email='b6@vent.test')
        one = Ticket.objects.filter(referral=self.link).first()
        one.status = 'refunded'
        one.save(update_fields=['status'])

        res = self.client.get('/event/%s/referrals/' % self.event.slug,
                              **self.owner_auth)
        rows = {r['code']: r for r in res.json()['data']['results']}
        self.assertEqual(rows['BIGST']['tickets_sold'], 1)

    def test_the_organiser_is_given_the_link_to_hand_out(self):
        res = self.client.get('/event/%s/referrals/' % self.event.slug,
                              **self.owner_auth)
        rows = {r['code']: r for r in res.json()['data']['results']}
        self.assertIn('?ref=BIGST', rows['BIGST']['share_url'])
        self.assertIn(self.event.slug, rows['BIGST']['share_url'])

    def test_the_link_points_at_the_apex_whatever_frontend_url_says(self):
        """FRONTEND_URL defaulted to test.app.v-ent.co, a host that has never
        resolved, and every emailed link built from it went nowhere for a week.
        This is a new consumer of the same setting, and a dead link an
        influencer posts to their audience is worse than a dead email: nobody
        reports it, they just never arrive."""
        with self.settings(FRONTEND_URL='https://test.app.v-ent.co'):
            res = self.client.get('/event/%s/referrals/' % self.event.slug,
                                  **self.owner_auth)
        url = {r['code']: r for r in res.json()['data']['results']}['BIGST']['share_url']
        self.assertTrue(url.startswith('https://v-ent.co/'), url)
        self.assertNotIn('test.app', url)

    def test_somebody_else_cannot_read_the_numbers(self):
        _, stranger_auth = a_user('stranger')
        res = self.client.get('/event/%s/referrals/' % self.event.slug,
                              **stranger_auth)
        self.assertIn(res.status_code, (401, 403))


class EventDashboardTests(TestCase):
    """One place showing everything about an event.

    CEO, 29 August 2026: "there should be a place to view metrics of everything
    about an event that was created. like a dashboard."

    The metrics endpoint existed and answered tickets, revenue, tiers, sales by
    day and capacity. Everything else about an event was counted on the screen
    that owned it, so an organiser wanting the whole picture opened six tabs
    and held the total in their head. These tests are about the sections that
    were missing.
    """

    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Rivalry Series', creator=self.owner, event_type='physical',
            desc='A series.', location='Lagos',
            start_date=now + timedelta(days=5), end_date=now + timedelta(days=5),
        )
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=50)
        self.link = EventReferral.objects.create(
            event=self.event, name='Big Streamer', code='BIGST')

    def _metrics(self):
        res = self.client.get('/event/%s/metrics/' % self.event.slug,
                              **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def test_the_dashboard_carries_every_section(self):
        data = self._metrics()
        for section in ('tickets', 'revenue', 'tiers', 'sales_by_day',
                        'capacity', 'referrals', 'engagement', 'shop',
                        'arrivals_by_hour'):
            self.assertIn(section, data, section)

    def test_the_influencer_links_are_on_it(self):
        self.client.post('/event/%s/ref/BIGST/visit/' % self.event.slug,
                         data=json.dumps({'first_time': True}),
                         content_type='application/json')
        self.client.post(
            '/event/%s/guest-buy/' % self.event.slug,
            data=json.dumps({'tier_id': self.tier.id, 'quantity': 1,
                             'email': 'd1@vent.test', 'ref': 'BIGST'}),
            content_type='application/json')

        refs = self._metrics()['referrals']
        self.assertEqual(refs['total_visits'], 1)
        self.assertEqual(refs['total_sold'], 1)
        self.assertEqual(refs['best'], 'Big Streamer')
        self.assertEqual(len(refs['links']), 1)

    def test_a_link_nobody_used_is_not_reported_as_the_best(self):
        """Naming the top link when nothing sold would put an influencer at the
        top of a table for having done nothing."""
        self.assertIsNone(self._metrics()['referrals']['best'])

    def test_arrivals_are_grouped_by_the_hour_people_actually_came(self):
        """An organiser staffs a door from this. Knowing 400 came is not the
        same as knowing 300 of them came in one hour."""
        self.client.post(
            '/event/%s/guest-buy/' % self.event.slug,
            data=json.dumps({'tier_id': self.tier.id, 'quantity': 3,
                             'email': 'd2@vent.test'}),
            content_type='application/json')
        when = timezone.now()
        for ticket in Ticket.objects.filter(event=self.event)[:2]:
            ticket.status = 'checked_in'
            ticket.checked_in_at = when
            ticket.save(update_fields=['status', 'checked_in_at'])

        hours = self._metrics()['arrivals_by_hour']
        self.assertEqual(len(hours), 1)
        self.assertEqual(hours[0]['arrivals'], 2)

    def test_nobody_checked_in_is_an_empty_list_not_a_fake_row(self):
        self.assertEqual(self._metrics()['arrivals_by_hour'], [])

    def test_engagement_counts_polls_and_answers(self):
        from .models import EventPoll
        EventPoll.objects.create(event=self.event, question='Enjoying it?')
        self.assertEqual(self._metrics()['engagement']['polls'], 1)
        self.assertEqual(self._metrics()['engagement']['poll_answers'], 0)

    def test_the_shop_block_is_zero_rather_than_missing_with_no_stalls(self):
        """A section that disappears when it is empty makes the page jump and
        makes the organiser wonder whether it broke."""
        shop = self._metrics()['shop']
        self.assertEqual(shop['orders'], 0)
        self.assertEqual(shop['revenue_vc'], 0)

    def test_somebody_else_cannot_read_the_dashboard(self):
        _, stranger_auth = a_user('stranger')
        res = self.client.get('/event/%s/metrics/' % self.event.slug,
                              **stranger_auth)
        self.assertIn(res.status_code, (401, 403))
