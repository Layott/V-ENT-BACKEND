"""The endpoints, including who they refuse.

Gates C1, C2, C3, and the signed-out half of the platform rules.

Every gated endpoint is asked twice: once with no Authorization header and once
as somebody who should not be allowed. A hidden control is a courtesy; the API
is the permission, and the way that stops being true is a test nobody wrote.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import OrgMember
from vent_billing import lifecycle, states
from vent_billing.factories import a_plan, a_user, an_org, set_fee
from vent_billing.models import Plan, Subscription


class PublicReadingTests(TestCase):
    """Content is public, the action is gated."""

    def setUp(self):
        self.client = APIClient()
        self.organiser, self.org_auth = a_user('pb_org')
        self.org = an_org(self.organiser, 'Public Reading Org')
        self.plan = a_plan(self.organiser, org=self.org, name='Open Plan',
                           price_vc=5)
        self.draft = a_plan(self.organiser, org=self.org, name='Hidden Plan',
                            status=Plan.STATUS_DRAFT)

    def test_a_stranger_with_no_account_sees_the_plan_and_its_price(self):
        res = self.client.get('/billing/plan/%s/' % self.plan.slug)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['price_vc'], 5)
        self.assertEqual(res.data['data']['price_ngn'], 5000)
        self.assertFalse(res.data['data']['can_manage'])
        self.assertIsNone(res.data['data']['my_subscription'])

    def test_a_draft_is_not_readable_by_somebody_who_could_subscribe_to_it(self):
        res = self.client.get('/billing/plan/%s/' % self.draft.slug)
        self.assertEqual(res.status_code, 404)

    def test_the_organiser_sees_their_own_draft(self):
        res = self.client.get('/billing/plan/%s/' % self.draft.slug,
                              **self.org_auth)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['data']['can_manage'])

    def test_the_list_hides_drafts_from_everybody_else(self):
        res = self.client.get('/billing/plans/?org=%s' % self.org.slug)
        names = [p['name'] for p in res.data['data']['plans']]
        self.assertEqual(names, ['Open Plan'])
        self.assertFalse(res.data['data']['can_manage'])

        res = self.client.get('/billing/plans/?org=%s' % self.org.slug,
                              **self.org_auth)
        names = sorted(p['name'] for p in res.data['data']['plans'])
        self.assertEqual(names, ['Hidden Plan', 'Open Plan'])

    def test_the_address_carries_the_name_and_never_a_number(self):
        self.assertEqual(self.plan.slug, 'open-plan')

    def test_a_renamed_plan_keeps_its_old_address_working(self):
        old = self.plan.slug
        self.plan.name = 'Renamed Plan'
        self.plan.save()
        self.assertEqual(self.plan.slug, 'renamed-plan')
        res = self.client.get('/billing/plan/%s/' % old)
        self.assertEqual(res.status_code, 200)
        # A move is reported in the envelope with a 200, never a 301: fetch()
        # follows a redirect transparently and would chase a frontend path
        # against the API host.
        self.assertEqual(res.data['message'], 'moved')
        self.assertEqual(res.data['data']['url'], '/plans/renamed-plan')

    def test_the_public_list_carries_only_what_is_actually_public(self):
        # A sitemap is a positive claim that a URL is worth crawling, so a
        # draft is not in it.
        res = self.client.get('/billing/plans/public/')
        self.assertEqual(res.status_code, 200)
        slugs = [p['slug'] for p in res.data['data']['plans']]
        self.assertIn(self.plan.slug, slugs)
        self.assertNotIn(self.draft.slug, slugs)

    def test_the_benefit_catalogue_is_sent_rather_than_copied(self):
        res = self.client.get('/billing/catalogue/')
        self.assertEqual(res.status_code, 200)
        keys = [row['key'] for row in res.data['data']['benefits']]
        self.assertIn('member_ticket_discount', keys)

    def test_entitlements_answers_a_stranger_with_an_empty_list(self):
        # 200 with nothing, following `capabilities`, so the interface has one
        # code path for members and strangers rather than a 401 branch only one
        # of them ever exercises.
        res = self.client.get('/billing/entitlements/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['data']['signed_in'])
        self.assertEqual(res.data['data']['memberships'], [])


class SignedOutTests(TestCase):
    """Every endpoint that DOES something refuses an anonymous caller."""

    def setUp(self):
        self.client = APIClient()
        self.organiser, _ = a_user('so_org')
        self.member, self.member_auth = a_user('so_member', coins=100)
        self.plan = a_plan(self.organiser, name='Signed Out Plan')
        self.sub, self.invoice = lifecycle.subscribe(self.member, self.plan)

    def test_every_gated_endpoint_answers_401_with_no_token(self):
        gated = [
            ('post', '/billing/plans/create/'),
            ('post', '/billing/plan/%s/edit/' % self.plan.slug),
            ('post', '/billing/plan/%s/subscribe/' % self.plan.slug),
            ('get', '/billing/plan/%s/members-area/' % self.plan.slug),
            ('get', '/billing/plan/%s/members/' % self.plan.slug),
            ('get', '/billing/plan/%s/earnings/' % self.plan.slug),
            ('post', '/billing/plan/%s/settle/' % self.plan.slug),
            ('get', '/billing/subscriptions/'),
            ('get', '/billing/invoices/'),
            ('get', '/billing/org-overview/'),
            ('get', '/billing/subscription/%s/' % self.sub.token),
            ('post', '/billing/subscription/%s/cancel/' % self.sub.token),
            ('post', '/billing/subscription/%s/resume/' % self.sub.token),
            ('post', '/billing/subscription/%s/change/' % self.sub.token),
            ('post', '/billing/subscription/%s/end/' % self.sub.token),
            ('post', '/billing/invoice/%s/refund/' % self.invoice.token),
        ]
        for method, url in gated:
            with self.subTest(url=url):
                res = getattr(self.client, method)(url, {}, format='json')
                self.assertEqual(res.status_code, 401, url)
                self.assertEqual(res.data['code'], 'UNAUTHORIZED')

    def test_a_stranger_cannot_read_the_members_or_the_takings(self):
        _other, other_auth = a_user('so_other')
        for url in ('/billing/plan/%s/members/' % self.plan.slug,
                    '/billing/plan/%s/earnings/' % self.plan.slug):
            res = self.client.get(url, **other_auth)
            self.assertEqual(res.status_code, 403)


class MembersAreaTests(TestCase):
    """Gate C1: the benefit is enforced on the API and lapses on the next
    request."""

    def setUp(self):
        self.client = APIClient()
        self.organiser, self.org_auth = a_user('ma_org')
        self.member, self.auth = a_user('ma_member', coins=100)
        self.plan = a_plan(self.organiser, name='Members Area Plan',
                           price_vc=5, member_content='Discord: vent.test/x')
        self.sub, _ = lifecycle.subscribe(self.member, self.plan)

    def url(self):
        return '/billing/plan/%s/members-area/' % self.plan.slug

    def test_a_member_gets_what_they_paid_for(self):
        res = self.client.get(self.url(), **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['member_content'],
                         'Discord: vent.test/x')
        self.assertEqual(res.data['data']['as'], 'member')

    def test_somebody_who_never_joined_is_refused(self):
        _other, other_auth = a_user('ma_other')
        res = self.client.get(self.url(), **other_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'NOT_A_MEMBER')

    def test_a_lapsed_member_loses_it_on_the_next_request(self):
        # No deploy, no re-render, nothing running in between. The clock moved
        # and the very next request is refused.
        Subscription.objects.filter(pk=self.sub.pk).update(
            state=states.EXPIRED,
            period_end=timezone.now() - timedelta(days=1))
        res = self.client.get(self.url(), **self.auth)
        self.assertEqual(res.status_code, 403)

    def test_a_cancelled_member_still_has_it_until_the_period_ends(self):
        lifecycle.cancel(self.sub, actor=self.member)
        res = self.client.get(self.url(), **self.auth)
        self.assertEqual(res.status_code, 200)

    def test_the_plan_payload_never_carries_the_member_content(self):
        res = self.client.get('/billing/plan/%s/' % self.plan.slug)
        self.assertNotIn('member_content', res.data['data'])
        self.assertTrue(res.data['data']['has_member_content'])

    def test_the_organiser_can_read_it_without_subscribing_to_themselves(self):
        res = self.client.get(self.url(), **self.org_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['as'], 'organiser')


class BenefitTests(TestCase):
    def setUp(self):
        self.organiser, _ = a_user('bn_org')
        self.other_org, _ = a_user('bn_org2')
        self.member, _ = a_user('bn_member', coins=100)
        self.plan = a_plan(
            self.organiser, name='Benefit Plan', price_vc=5,
            benefits=[{'key': 'member_ticket_discount', 'value': 20},
                      {'key': 'free_entry'}])
        self.sub, _ = lifecycle.subscribe(self.member, self.plan)

    def test_a_member_holds_what_the_plan_grants(self):
        from vent_billing import benefits
        self.assertTrue(benefits.has_benefit(self.member, 'free_entry',
                                             owner=self.organiser))
        self.assertEqual(benefits.benefit_value(
            self.member, 'member_ticket_discount', owner=self.organiser), 20)

    def test_a_membership_of_one_organiser_grants_nothing_from_another(self):
        from vent_billing import benefits
        self.assertFalse(benefits.has_benefit(self.member, 'free_entry',
                                              owner=self.other_org))

    def test_a_lapsed_membership_grants_nothing(self):
        from vent_billing import benefits
        Subscription.objects.filter(pk=self.sub.pk).update(
            state=states.EXPIRED)
        self.assertFalse(benefits.has_benefit(self.member, 'free_entry',
                                              owner=self.organiser))

    def test_a_benefit_key_nobody_can_render_is_never_stored(self):
        from vent_billing import benefits
        cleaned = benefits.clean(['free_entry', 'unlimited_money', 42])
        self.assertEqual([row['key'] for row in cleaned], ['free_entry'])

    def test_a_value_benefit_is_clamped_to_its_maximum(self):
        from vent_billing import benefits
        cleaned = benefits.clean([{'key': 'member_ticket_discount',
                                   'value': 500}])
        self.assertEqual(cleaned[0]['value'], 100)

    def test_entitlements_names_the_seller_and_the_date(self):
        res = self.client.get('/billing/entitlements/',
                              HTTP_AUTHORIZATION='Bearer %s'
                              % self.member.login_session_token)
        rows = res.data['data']['memberships']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['plan_slug'], self.plan.slug)
        self.assertEqual(rows[0]['owner'], 'bn_org')
        self.assertIn('until', rows[0])


class OrganiserConsoleTests(TestCase):
    """Gate C3: members, recurring revenue and who is past due."""

    def setUp(self):
        set_fee(10)
        self.client = APIClient()
        self.organiser, self.org_auth = a_user('oc_org')
        self.org = an_org(self.organiser, 'Console Org')
        self.plan = a_plan(self.organiser, org=self.org, name='Console Plan',
                           price_vc=10)
        self.members = []
        for i in range(3):
            member, _ = a_user('oc_m%d' % i, coins=100)
            sub, _ = lifecycle.subscribe(member, self.plan)
            self.members.append((member, sub))

    def test_the_organiser_sees_every_member_with_a_face(self):
        res = self.client.get('/billing/plan/%s/members/' % self.plan.slug,
                              **self.org_auth)
        self.assertEqual(res.status_code, 200)
        rows = res.data['data']['members']
        self.assertEqual(len(rows), 3)
        # Through the one person builder, so the avatar and the founder badge
        # come with the name rather than being lost to a hand-written dict.
        self.assertIn('avatar', rows[0]['subscriber'])
        self.assertIn('founder_badge', rows[0]['subscriber'])

    def test_recurring_revenue_counts_only_what_will_be_charged_again(self):
        res = self.client.get('/billing/plan/%s/members/' % self.plan.slug,
                              **self.org_auth)
        self.assertEqual(res.data['data']['recurring_monthly_vc'], 30)

        lifecycle.cancel(self.members[0][1], actor=self.members[0][0])
        res = self.client.get('/billing/plan/%s/members/' % self.plan.slug,
                              **self.org_auth)
        # Still three members with access, but only two will pay again.
        self.assertEqual(len(res.data['data']['members']), 3)
        self.assertEqual(res.data['data']['recurring_monthly_vc'], 20)

    def test_a_cancelled_member_is_still_a_member_on_both_screens(self):
        # The walk on 8 September had the plan's public page saying "1 members"
        # and the console saying 0, for the same person, because the two
        # counted different sets. One number, one definition.
        lifecycle.cancel(self.members[0][1], actor=self.members[0][0])

        console = self.client.get(
            '/billing/org-overview/?org=%s' % self.org.slug, **self.org_auth)
        row = console.data['data']['plans'][0]
        self.assertEqual(row['members'], 3)
        self.assertEqual(row['renewing'], 2)

        public = self.client.get('/billing/plan/%s/' % self.plan.slug)
        self.assertEqual(public.data['data']['member_count'], row['members'])

        members = self.client.get('/billing/plan/%s/members/' % self.plan.slug,
                                  **self.org_auth)
        self.assertEqual(members.data['data']['members_live'], row['members'])

    def test_a_refunded_member_stops_being_counted_at_once(self):
        # The walk on 8 September: the plan page still said "1 members" after
        # the only member had been refunded and had no access left. The count
        # asks the clock, not just the state.
        from vent_billing import charging
        from vent_billing.models import Invoice
        member, sub = self.members[0]
        invoice = Invoice.objects.filter(subscription=sub).first()
        charging.refund(invoice, reason='error', actor=self.organiser)
        sub.refresh_from_db()
        self.assertFalse(sub.has_access())

        public = self.client.get('/billing/plan/%s/' % self.plan.slug)
        self.assertEqual(public.data['data']['member_count'], 2)
        console = self.client.get(
            '/billing/org-overview/?org=%s' % self.org.slug, **self.org_auth)
        self.assertEqual(console.data['data']['plans'][0]['members'], 2)

    def test_the_member_count_and_has_access_cannot_drift_apart(self):
        # `Plan.live_subscriptions` is a SQL restatement of
        # `Subscription.has_access`, so the two are checked against each other
        # rather than trusted to stay in step.
        from django.utils import timezone as tz
        now = tz.now()
        counted = set(self.plan.live_subscriptions(now).values_list(
            'subscription_id', flat=True))
        by_access = {s.subscription_id for s in self.plan.subscriptions.all()
                     if s.has_access(now)}
        self.assertEqual(counted, by_access)

        # And again with somebody in each state that can be live.
        lifecycle.cancel(self.members[1][1], actor=self.members[1][0])
        counted = set(self.plan.live_subscriptions(now).values_list(
            'subscription_id', flat=True))
        by_access = {s.subscription_id for s in self.plan.subscriptions.all()
                     if s.has_access(now)}
        self.assertEqual(counted, by_access)

    def test_a_yearly_plan_is_shown_per_month_so_the_figures_compare(self):
        yearly = a_plan(self.organiser, org=self.org, name='Yearly Console',
                        price_vc=120, interval='yearly')
        member, _ = a_user('oc_year', coins=200)
        lifecycle.subscribe(member, yearly)
        res = self.client.get('/billing/plan/%s/members/' % yearly.slug,
                              **self.org_auth)
        self.assertEqual(res.data['data']['recurring_monthly_vc'], 10)

    def test_past_due_members_are_named(self):
        from vent_auth.models import UserWallet
        member, sub = self.members[0]
        UserWallet.objects.filter(user=member).update(wallet_balance=0)
        lifecycle.run_renewals(at=sub.period_end + timedelta(minutes=1))
        res = self.client.get('/billing/plan/%s/members/' % self.plan.slug,
                              **self.org_auth)
        self.assertEqual(res.data['data']['past_due'], 1)

    def test_earnings_add_up_and_name_the_fee(self):
        res = self.client.get('/billing/plan/%s/earnings/' % self.plan.slug,
                              **self.org_auth)
        data = res.data['data']
        self.assertEqual(data['collected_vc'], 30)
        self.assertEqual(data['owed_vc'], 27)
        self.assertEqual(data['platform_fee_vc'], 3)
        self.assertEqual(data['fee_pct'], 10)

    def test_settling_twice_says_so_rather_than_failing(self):
        first = self.client.post('/billing/plan/%s/settle/' % self.plan.slug,
                                 {}, format='json', **self.org_auth)
        self.assertEqual(first.data['data']['amount_vc'], 27)
        second = self.client.post('/billing/plan/%s/settle/' % self.plan.slug,
                                  {}, format='json', **self.org_auth)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['data']['lines_paid'], 0)

    def test_the_overview_is_one_request_for_the_whole_organisation(self):
        a_plan(self.organiser, org=self.org, name='Second Console Plan',
               price_vc=5)
        res = self.client.get('/billing/org-overview/?org=%s' % self.org.slug,
                              **self.org_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['data']['plans']), 2)
        self.assertEqual(res.data['data']['recurring_monthly_vc'], 30)

    def test_an_events_manager_can_run_the_plans_and_a_member_cannot(self):
        manager, manager_auth = a_user('oc_manager')
        row = OrgMember.objects.create(org=self.org, user=manager,
                                       role=OrgMember.ROLE_MANAGER,
                                       scopes=['events'])
        res = self.client.get('/billing/plan/%s/members/' % self.plan.slug,
                              **manager_auth)
        self.assertEqual(res.status_code, 200)

        row.scopes = ['teams']
        row.save(update_fields=['scopes'])
        res = self.client.get('/billing/plan/%s/members/' % self.plan.slug,
                              **manager_auth)
        self.assertEqual(res.status_code, 403)

    def test_the_payment_list_holds_failures_as_well_as_successes(self):
        from vent_auth.models import UserWallet
        member, sub = self.members[0]
        UserWallet.objects.filter(user=member).update(wallet_balance=0)
        lifecycle.run_renewals(at=sub.period_end + timedelta(minutes=1))

        res = self.client.get('/billing/plan/%s/invoices/' % self.plan.slug,
                              **self.org_auth)
        self.assertEqual(res.status_code, 200)
        rows = res.data['data']['invoices']
        self.assertEqual(len({r['state'] for r in rows}), 2)
        # Every row names the person, through the one person builder.
        self.assertIn('avatar', rows[0]['subscriber'])

    def test_a_stranger_cannot_read_the_payment_list(self):
        _other, other_auth = a_user('oc_stranger')
        res = self.client.get('/billing/plan/%s/invoices/' % self.plan.slug,
                              **other_auth)
        self.assertEqual(res.status_code, 403)

    def test_refunding_gives_the_money_back_and_ends_the_membership(self):
        from vent_billing.models import Invoice
        member, sub = self.members[0]
        invoice = Invoice.objects.filter(subscription=sub).first()
        res = self.client.post('/billing/invoice/%s/refund/' % invoice.token,
                               {'reason': 'charged in error'}, format='json',
                               **self.org_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['invoice']['state'], 'refunded')
        sub.refresh_from_db()
        self.assertFalse(sub.has_access())

    def test_a_refund_needs_a_reason(self):
        from vent_billing.models import Invoice
        invoice = Invoice.objects.filter(plan=self.plan).first()
        res = self.client.post('/billing/invoice/%s/refund/' % invoice.token,
                               {}, format='json', **self.org_auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'REASON_REQUIRED')


class PlanWritingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser, self.auth = a_user('pw_org')
        self.org = an_org(self.organiser, 'Writing Org')

    def test_a_price_that_is_not_whole_coins_is_refused_not_rounded(self):
        res = self.client.post('/billing/plans/create/', {
            'org': self.org.slug, 'name': 'Odd Price', 'price_ngn': 1500,
        }, format='json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PRICE_NOT_WHOLE_COINS')
        # And it says what the nearest allowed prices are, so somebody can fix
        # it rather than guess.
        self.assertEqual(res.data['data']['nearest_lower'], 1000)
        self.assertEqual(res.data['data']['nearest_upper'], 2000)

    def test_a_plan_is_created_as_a_draft_by_default(self):
        res = self.client.post('/billing/plans/create/', {
            'org': self.org.slug, 'name': 'New Plan', 'price_ngn': 5000,
            'benefits': [{'key': 'free_entry'}],
        }, format='json', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['status'], 'draft')
        self.assertEqual(res.data['data']['price_vc'], 5)
        self.assertEqual(res.data['data']['price_ngn'], 5000)

    def test_a_price_cannot_change_under_people_already_paying(self):
        plan = a_plan(self.organiser, org=self.org, name='Priced Plan',
                      price_vc=5)
        member, _ = a_user('pw_member', coins=100)
        lifecycle.subscribe(member, plan)
        res = self.client.post('/billing/plan/%s/edit/' % plan.slug, {
            'name': 'Priced Plan', 'price_ngn': 20000,
        }, format='json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PLAN_HAS_MEMBERS')

    def test_the_description_can_still_be_edited_with_members_on_it(self):
        plan = a_plan(self.organiser, org=self.org, name='Editable Plan',
                      price_vc=5)
        member, _ = a_user('pw_member2', coins=100)
        lifecycle.subscribe(member, plan)
        res = self.client.post('/billing/plan/%s/edit/' % plan.slug, {
            'name': 'Editable Plan', 'price_ngn': 5000,
            'description': 'Now with more detail.',
        }, format='json', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['description'],
                         'Now with more detail.')

    def test_somebody_outside_the_organisation_cannot_create_its_plans(self):
        _other, other_auth = a_user('pw_other')
        res = self.client.post('/billing/plans/create/', {
            'org': self.org.slug, 'name': 'Not Yours', 'price_ngn': 1000,
        }, format='json', **other_auth)
        self.assertEqual(res.status_code, 403)

    def test_a_person_with_no_organisation_can_still_sell_a_membership(self):
        solo, solo_auth = a_user('pw_solo')
        res = self.client.post('/billing/plans/create/', {
            'name': 'Solo Membership', 'price_ngn': 2000,
            'status': 'public',
        }, format='json', **solo_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['seller']['kind'], 'person')
        self.assertEqual(res.data['data']['seller']['username'], 'pw_solo')


class SubscriberScreenTests(TestCase):
    """Gate C2: what they pay for, when the next charge lands, what it takes
    from, and every past invoice."""

    def setUp(self):
        self.client = APIClient()
        self.organiser, _ = a_user('ss_org')
        self.member, self.auth = a_user('ss_member', coins=100)
        self.plan = a_plan(self.organiser, name='Subscriber Plan', price_vc=5)
        self.sub, _ = lifecycle.subscribe(self.member, self.plan)

    def test_the_list_says_what_the_next_charge_takes_from(self):
        res = self.client.get('/billing/subscriptions/', **self.auth)
        data = res.data['data']
        self.assertEqual(data['wallet_balance_vc'], 95)
        self.assertIsNone(data['default_card'])
        row = data['subscriptions'][0]
        self.assertEqual(row['source'], 'wallet')
        self.assertEqual(row['next_charge_at'], row['period_end'])
        self.assertEqual(row['plan']['name'], 'Subscriber Plan')

    def test_the_detail_carries_every_invoice_and_the_whole_history(self):
        res = self.client.get('/billing/subscription/%s/' % self.sub.token,
                              **self.auth)
        data = res.data['data']
        self.assertEqual(len(data['invoices']), 1)
        self.assertEqual(data['invoices'][0]['state'], 'paid')
        self.assertEqual(data['history'][0]['reason'], states.FIRST_CHARGE)

    def test_a_cancelled_subscription_says_it_will_not_be_charged_again(self):
        lifecycle.cancel(self.sub, actor=self.member)
        res = self.client.get('/billing/subscription/%s/' % self.sub.token,
                              **self.auth)
        data = res.data['data']
        self.assertIsNone(data['next_charge_at'])
        self.assertFalse(data['renews'])
        self.assertTrue(data['has_access'])
        self.assertEqual(data['access_until'], data['period_end'])

    def test_failed_charges_are_on_the_statement_too(self):
        from vent_auth.models import UserWallet
        UserWallet.objects.filter(user=self.member).update(wallet_balance=0)
        lifecycle.run_renewals(at=self.sub.period_end + timedelta(minutes=1))
        res = self.client.get('/billing/invoices/', **self.auth)
        states_seen = [i['state'] for i in res.data['data']['invoices']]
        self.assertIn('failed', states_seen)
        self.assertIn('paid', states_seen)

    def test_a_failure_is_reported_by_code_never_by_the_gateways_words(self):
        from vent_auth.models import UserWallet
        UserWallet.objects.filter(user=self.member).update(wallet_balance=0)
        lifecycle.run_renewals(at=self.sub.period_end + timedelta(minutes=1))
        res = self.client.get('/billing/invoices/', **self.auth)
        failed = [i for i in res.data['data']['invoices']
                  if i['state'] == 'failed'][0]
        self.assertEqual(failed['failure_code'], 'INSUFFICIENT_FUNDS')
        self.assertNotIn('failure_detail', failed)

    def test_subscribing_through_the_api_answers_with_the_invoice(self):
        other, other_auth = a_user('ss_other', coins=50)
        res = self.client.post('/billing/plan/%s/subscribe/' % self.plan.slug,
                               {}, format='json', **other_auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['invoice']['collected_vc'], 5)
        self.assertEqual(res.data['data']['state'], states.ACTIVE)

    def test_subscribing_with_no_money_says_which_and_names_the_price(self):
        # A wallet that exists and is empty is INSUFFICIENT_FUNDS, which tells
        # somebody to top up. NO_PAYMENT_METHOD is for having no way to pay at
        # all, and the two lead to different screens.
        broke, broke_auth = a_user('ss_broke', coins=0)
        res = self.client.post('/billing/plan/%s/subscribe/' % self.plan.slug,
                               {}, format='json', **broke_auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_FUNDS')
        self.assertEqual(res.data['data']['price_vc'], 5)
        self.assertEqual(res.data['data']['price_ngn'], 5000)
