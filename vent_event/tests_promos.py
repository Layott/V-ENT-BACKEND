"""Day tickets, influencer links, promo codes, and who may manage an event."""
import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Organization, Users

from .models import Event, EventManager, EventPromo, EventReferral, TicketTier


def a_user(name):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tok-%s' % name)[:16],
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class TicketingSetupTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        self.other, self.other_auth = a_user('other')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Three Day Con', creator=self.owner, event_type='physical',
            desc='Three days.', location='Lagos',
            start_date=now + timedelta(days=30), end_date=now + timedelta(days=32),
        )

    # ------------------------------------------------------------- day tickets
    def test_a_tier_can_be_sold_for_one_day(self):
        friday = (self.event.start_date).date()
        tier = TicketTier.objects.create(event=self.event, name='Day 1', price=5000,
                                         quantity=100, day=friday, day_label='Day 1')
        self.assertEqual(tier.day, friday)

    def test_days_can_be_priced_differently(self):
        d1 = self.event.start_date.date()
        d3 = self.event.end_date.date()
        TicketTier.objects.create(event=self.event, name='Day 1', price=5000, day=d1)
        TicketTier.objects.create(event=self.event, name='Finals', price=12000, day=d3)
        prices = sorted(t.price for t in self.event.ticket_tiers.all())
        self.assertEqual(prices, [Decimal('5000'), Decimal('12000')])

    def test_a_tier_with_no_day_covers_the_whole_run(self):
        tier = TicketTier.objects.create(event=self.event, name='Full pass', price=20000)
        self.assertIsNone(tier.day)

    # -------------------------------------------------------------- referrals
    def test_the_organiser_can_add_an_influencer_link(self):
        res = self.client.post('/event/%s/referrals/' % self.event.event_id,
                               data=json.dumps({'name': 'Big Streamer', 'code': 'BIGST',
                                                'allocation': 50}),
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['data']['remaining'], 50)

    def test_a_stranger_cannot_add_a_link(self):
        res = self.client.post('/event/%s/referrals/' % self.event.event_id,
                               data=json.dumps({'name': 'Nope', 'code': 'NOPE'}),
                               content_type='application/json', **self.other_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_two_links_cannot_share_a_code(self):
        EventReferral.objects.create(event=self.event, name='A', code='DUP')
        res = self.client.post('/event/%s/referrals/' % self.event.event_id,
                               data=json.dumps({'name': 'B', 'code': 'dup'}),
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 409, res.content)

    def test_an_allocation_cannot_drop_below_what_was_sold(self):
        ref = EventReferral.objects.create(event=self.event, name='A', code='SOLD',
                                           allocation=50, sold=20)
        res = self.client.patch('/event/%s/referrals/%s/' % (self.event.event_id, ref.id),
                                data=json.dumps({'allocation': 10}),
                                content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_link_that_sold_tickets_is_switched_off_rather_than_deleted(self):
        """Deleting it would erase the record of who is owed for those sales."""
        ref = EventReferral.objects.create(event=self.event, name='A', code='PAID', sold=5)
        res = self.client.delete('/event/%s/referrals/%s/' % (self.event.event_id, ref.id),
                                 **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        ref.refresh_from_db()
        self.assertFalse(ref.is_active)

    def test_an_unused_link_is_deleted_outright(self):
        ref = EventReferral.objects.create(event=self.event, name='A', code='UNUSED')
        res = self.client.delete('/event/%s/referrals/%s/' % (self.event.event_id, ref.id),
                                 **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(EventReferral.objects.filter(pk=ref.pk).exists())

    # ----------------------------------------------------------------- promos
    def test_a_promo_can_be_capped_and_credited_to_an_influencer(self):
        ref = EventReferral.objects.create(event=self.event, name='Streamer', code='STR')
        res = self.client.post('/event/%s/promos/' % self.event.event_id,
                               data=json.dumps({'code': 'STR10', 'kind': 'percent',
                                                'value': 10, 'max_tickets': 100,
                                                'referral_id': ref.id}),
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()['data']
        self.assertEqual(body['referral_name'], 'Streamer')
        self.assertEqual(body['remaining'], 100)

    def test_a_percentage_over_one_hundred_is_refused(self):
        res = self.client.post('/event/%s/promos/' % self.event.event_id,
                               data=json.dumps({'code': 'TOOMUCH', 'kind': 'percent',
                                                'value': 150}),
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 400, res.content)

    def test_the_cap_counts_tickets_and_runs_out(self):
        promo = EventPromo.objects.create(event=self.event, code='CAP', value=10,
                                          max_tickets=5, used_tickets=4)
        self.assertEqual(promo.is_usable(quantity=1)[0], True)
        self.assertEqual(promo.is_usable(quantity=2)[1], 'PROMO_EXHAUSTED')

    def test_an_expired_promo_says_so(self):
        past = timezone.now() - timedelta(days=1)
        promo = EventPromo.objects.create(event=self.event, code='OLD', value=10,
                                          ends_at=past)
        self.assertEqual(promo.is_usable()[1], 'PROMO_EXPIRED')

    def test_a_discount_never_exceeds_the_price(self):
        """A 5000-off code on a 2000 ticket takes 2000, not 5000."""
        promo = EventPromo.objects.create(event=self.event, code='BIG',
                                          kind=EventPromo.AMOUNT, value=5000)
        self.assertEqual(promo.discount_for(2000, 1), Decimal('2000'))

    def test_a_percentage_discount_is_worked_out_on_the_whole_order(self):
        promo = EventPromo.objects.create(event=self.event, code='TEN', value=10)
        self.assertEqual(promo.discount_for(1000, 3), Decimal('300'))

    def test_a_used_promo_is_switched_off_rather_than_deleted(self):
        promo = EventPromo.objects.create(event=self.event, code='USED', value=10,
                                          used_tickets=3)
        res = self.client.delete('/event/%s/promos/%s/' % (self.event.event_id, promo.id),
                                 **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        promo.refresh_from_db()
        self.assertFalse(promo.is_active)

    # --------------------------------------------------------------- managers
    def test_a_personal_event_cannot_be_handed_to_anybody(self):
        """The door list and the attendee data go with management.

        CEO, 4 September 2026, after being offered the alternative: "dont
        ulock it, instead do a way to add events to an oganization". So this
        stays refused, and the way out is to move the event into an
        organisation, which is now something a screen can do.
        """
        res = self.client.post('/event/%s/managers/' % self.event.event_id,
                               data=json.dumps({'username': self.other.username}),
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'EVENT_NOT_IN_ORGANISATION')

    def test_an_organisation_event_can_be_shared(self):
        org = Organization.objects.create(org_name='Vermillion %s' % uuid.uuid4().hex[:4],
                                          org_creator=self.owner, org_owner=self.owner)
        self.event.organization = org
        self.event.save(update_fields=['organization'])

        res = self.client.post('/event/%s/managers/' % self.event.event_id,
                               data=json.dumps({'username': self.other.username}),
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(EventManager.objects.filter(event=self.event, user=self.other).exists())

    def test_the_screen_is_told_whether_it_may_offer_the_control(self):
        res = self.client.get('/event/%s/managers/' % self.event.event_id, **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['data']['can_add'])

    def test_a_manager_cannot_add_more_managers(self):
        """Otherwise an event quietly acquires people nobody chose."""
        org = Organization.objects.create(org_name='Org %s' % uuid.uuid4().hex[:4],
                                          org_creator=self.owner, org_owner=self.owner)
        self.event.organization = org
        self.event.save(update_fields=['organization'])
        EventManager.objects.create(event=self.event, user=self.other, role='manager')

        third, _ = a_user('third')
        res = self.client.post('/event/%s/managers/' % self.event.event_id,
                               data=json.dumps({'username': third.username}),
                               content_type='application/json', **self.other_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_a_manager_can_still_run_the_promos(self):
        org = Organization.objects.create(org_name='Org %s' % uuid.uuid4().hex[:4],
                                          org_creator=self.owner, org_owner=self.owner)
        self.event.organization = org
        self.event.save(update_fields=['organization'])
        EventManager.objects.create(event=self.event, user=self.other, role='manager')

        res = self.client.post('/event/%s/promos/' % self.event.event_id,
                               data=json.dumps({'code': 'MGR', 'value': 5}),
                               content_type='application/json', **self.other_auth)
        self.assertEqual(res.status_code, 201, res.content)

    # ------------------------------------------------------- addressed by name
    def test_the_organiser_routes_work_by_slug(self):
        """The named address is the one the project rule requires.

        /events/three-day-con/manage passes the slug through, and these routes
        were int-only, so every named URL answered 404 while ?id= worked.
        """
        self.assertTrue(self.event.slug)
        res = self.client.get('/event/%s/promos/' % self.event.slug, **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_numeric_address_still_works(self):
        """Links shared before the slug rule have to keep opening."""
        res = self.client.get('/event/%s/promos/' % self.event.event_id, **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)


class MyEventsTests(TestCase):
    """The events you run - there was no way to see your own at all."""

    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        self.helper, self.helper_auth = a_user('helper')
        self.stranger, self.stranger_auth = a_user('nosy')
        now = timezone.now()
        self.mine = Event.objects.create(
            name='My Own Event', creator=self.owner, event_type='physical',
            desc='Mine.', location='Lagos',
            start_date=now + timedelta(days=5), end_date=now + timedelta(days=6),
        )
        self.theirs = Event.objects.create(
            name='Somebody Elses', creator=self.stranger, event_type='physical',
            desc='Not mine.', location='Abuja',
            start_date=now + timedelta(days=7), end_date=now + timedelta(days=8),
        )

    def test_it_lists_what_you_created(self):
        res = self.client.get('/event/my-events/', **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        names = [e['name'] for e in res.json()['data']['results']]
        self.assertEqual(names, ['My Own Event'])

    def test_it_does_not_list_somebody_elses(self):
        res = self.client.get('/event/my-events/', **self.owner_auth)
        names = [e['name'] for e in res.json()['data']['results']]
        self.assertNotIn('Somebody Elses', names)

    def test_an_event_you_help_run_is_listed_too(self):
        """From the organiser's side both are events you must act on."""
        EventManager.objects.create(event=self.theirs, user=self.helper, role='manager')
        res = self.client.get('/event/my-events/', **self.helper_auth)
        rows = res.json()['data']['results']
        self.assertEqual([e['name'] for e in rows], ['Somebody Elses'])
        self.assertEqual(rows[0]['role'], 'manager')
        self.assertFalse(rows[0]['is_owner'])

    def test_a_retired_event_is_still_listed(self):
        """This is the only screen that can show it, and it may need fixing."""
        self.mine.is_active = False
        self.mine.save(update_fields=['is_active'])
        res = self.client.get('/event/my-events/', **self.owner_auth)
        self.assertEqual([e['name'] for e in res.json()['data']['results']],
                         ['My Own Event'])

    def test_a_signed_out_visitor_is_refused(self):
        res = self.client.get('/event/my-events/')
        self.assertIn(res.status_code, (400, 401), res.content)
