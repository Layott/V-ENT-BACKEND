"""Gate C1: every benefit that promises something is asked for by an endpoint.

The reason this file exists is one grep:

    grep -rn "has_benefit" --include=*.py . | grep -v vent_billing
    (nothing)

The catalogue was complete, the plan page rendered every benefit, an organiser
could sell them and a subscriber could pay for them, and no endpoint anywhere
asked whether anybody held one. A discount that is only ever rendered is worse
than no discount, because somebody paid for it.

So every test here goes over HTTP, as the person, and asserts on money or on
access rather than on a helper returning True. And every one of them has a
LAPSED twin: the benefit is asked on the request, so a membership that ran out
an hour ago is gone now, not at the next deploy and not when a screen happens
to re-render. That sentence is the whole of C1 and it is the half that a helper
test cannot prove.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, UserWallet
from vent_event.models import Event, TicketTier
from vent_tournament.models import Tournament

from . import entitlements, states
from .factories import a_plan, a_user, an_org
from .models import Subscription


def a_live_subscription(user, plan, *, until_days=30):
    """Somebody paid up. Built through the model rather than through the
    charging path, because what is under test here is the READING of it."""
    now = timezone.now()
    sub = Subscription(plan=plan, subscriber=user, state=states.ACTIVE,
                       period_start=now - timedelta(days=1),
                       period_end=now + timedelta(days=until_days),
                       anchor_day=now.day, anchor_month=now.month)
    sub.save()
    states.move(sub, states.FIRST_CHARGE, actor=user, at=now)
    return sub


def lapse(sub):
    """Their period ran out and the renewal slack with it.

    Nothing else is changed and nothing is run: the point is that the NEXT
    request notices on its own, without a cron or a deploy in between.

    Past `RENEWAL_SLACK_DAYS` rather than one minute past `period_end`,
    because an ACTIVE subscription deliberately keeps access for a day after
    its period ends so a cron running an hour late does not lock a paying
    member out. Asserting inside that window would be asserting that a
    documented kindness is a bug.
    """
    sub.period_end = timezone.now() - timedelta(
        days=Subscription.RENEWAL_SLACK_DAYS + 1)
    sub.save(update_fields=['period_end'])
    return sub


def expire(sub):
    """The other way somebody stops being a member: the state itself ends.

    `has_access` refuses an expired subscription whatever the dates say, so
    this is the case that does not depend on the clock at all.
    """
    sub.state = states.EXPIRED
    sub.save(update_fields=['state'])
    return sub


class TicketDiscountTests(TestCase):
    """`member_ticket_discount`, on the endpoint that prices a basket."""

    def setUp(self):
        self.organiser, _ = a_user('ent_torg')
        self.org = an_org(self.organiser, 'Discount Org')
        self.member, self.member_auth = a_user('ent_tmem', coins=100)
        self.outsider, self.outsider_auth = a_user('ent_tout', coins=100)

        self.plan = a_plan(self.organiser, org=self.org, name='Ticket Club',
                           price_vc=5,
                           benefits=[{'key': 'member_ticket_discount',
                                      'value': 25}])
        self.sub = a_live_subscription(self.member, self.plan)

        now = timezone.now()
        self.event = Event.objects.create(
            name='Discount Probe', creator=self.organiser, organization=self.org,
            event_type='physical', desc='x', entry_fee=0,
            start_date=now + timedelta(days=7),
            end_date=now + timedelta(days=7, hours=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=6))
        # 20 VC. A quarter off is 5 VC, which is expressible in whole coins, so
        # the arithmetic under test is the discount and not the rounding.
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=Decimal('20000'),
            quantity=50)

        # A real PIN, so the purchase below actually completes. Rendering
        # correctly is not working: the assertion that matters is what left
        # the wallet, and that needs the press to go all the way through.
        wallet = UserWallet.objects.get(user=self.member)
        wallet.pin_hash = make_password('0000')
        wallet.save(update_fields=['pin_hash'])

    def quote(self, auth=None, quantity=1):
        url = '/event/%s/quote/?tier=%s&quantity=%s' % (
            self.event.slug or self.event.event_id, self.tier.id, quantity)
        return self.client.get(url, **(auth or {}))

    def test_a_stranger_is_quoted_the_list_price(self):
        body = self.quote().json()['data']
        self.assertEqual(body['unit_vc'], 20)
        self.assertEqual(body['member_discount_pct'], 0)

    def test_somebody_with_no_membership_is_quoted_the_list_price(self):
        body = self.quote(self.outsider_auth).json()['data']
        self.assertEqual(body['unit_vc'], 20)
        self.assertEqual(body['member_discount_pct'], 0)

    def test_a_member_is_quoted_the_discount_they_pay_for(self):
        body = self.quote(self.member_auth).json()['data']
        self.assertEqual(body['member_discount_pct'], 25)
        self.assertEqual(body['unit_vc'], 15)
        self.assertEqual(body['member_saving_vc'], 5)

    def test_the_discount_is_charged_and_not_merely_shown(self):
        """The fault this is written against: a panel that says 15 and a
        checkout that takes 20.

        The ticket is actually BOUGHT here rather than quoted twice. A quote
        agreeing with itself proves nothing about what leaves the wallet, and
        what leaves the wallet is the only number the member ever feels.
        """
        quoted = self.quote(self.member_auth).json()['data']
        self.assertEqual(quoted['unit_vc'], 15)

        before = UserWallet.objects.get(user=self.member).wallet_balance
        res = self.client.post(
            '/event/%s/buy-ticket/' % (self.event.slug or self.event.event_id),
            data={'tier_id': self.tier.id, 'quantity': 1, 'pin': '0000'},
            content_type='application/json', **self.member_auth)
        self.assertIn(res.status_code, (200, 201), res.data)

        after = UserWallet.objects.get(user=self.member).wallet_balance
        self.assertEqual(before - after, quoted['total_vc'])
        self.assertEqual(before - after, 15)

    def test_a_non_member_is_charged_the_full_price_at_the_same_till(self):
        """The other side of the same assertion, so a passing discount test
        cannot be a discount applied to everybody."""
        wallet = UserWallet.objects.get(user=self.outsider)
        wallet.pin_hash = make_password('0000')
        wallet.save(update_fields=['pin_hash'])

        before = wallet.wallet_balance
        res = self.client.post(
            '/event/%s/buy-ticket/' % (self.event.slug or self.event.event_id),
            data={'tier_id': self.tier.id, 'quantity': 1, 'pin': '0000'},
            content_type='application/json', **self.outsider_auth)
        self.assertIn(res.status_code, (200, 201), res.data)
        after = UserWallet.objects.get(user=self.outsider).wallet_balance
        self.assertEqual(before - after, 20)

    def test_a_lapsed_member_is_quoted_the_full_price_on_the_next_request(self):
        self.assertEqual(self.quote(self.member_auth).json()['data']['unit_vc'], 15)
        lapse(self.sub)
        body = self.quote(self.member_auth).json()['data']
        self.assertEqual(body['unit_vc'], 20)
        self.assertEqual(body['member_discount_pct'], 0)

    def test_a_cancelled_member_keeps_it_until_the_period_ends(self):
        """Cancelling is not losing. They paid for the month."""
        from . import lifecycle
        lifecycle.cancel(self.sub, actor=self.member)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state, states.CANCELLED)
        self.assertEqual(self.quote(self.member_auth).json()['data']['unit_vc'], 15)

    def test_a_membership_of_somebody_else_grants_nothing_here(self):
        """A membership is with ONE seller. This is the assertion that stops
        the cheapest plan on the platform discounting everybody's tickets."""
        other_organiser, _ = a_user('ent_tother')
        other_org = an_org(other_organiser, 'Unrelated Org')
        other_plan = a_plan(other_organiser, org=other_org, name='Elsewhere',
                            benefits=[{'key': 'member_ticket_discount',
                                       'value': 90}])
        a_live_subscription(self.outsider, other_plan)
        body = self.quote(self.outsider_auth).json()['data']
        self.assertEqual(body['unit_vc'], 20)
        self.assertEqual(body['member_discount_pct'], 0)

    def test_the_discount_rounds_the_price_down(self):
        """Rounded in the member's favour, the opposite way from the fee."""
        self.plan.benefits = [{'key': 'member_ticket_discount', 'value': 33}]
        self.plan.save()
        # 20 VC less 33 percent is 13.4 VC. The member pays 13, never 14.
        self.assertEqual(self.quote(self.member_auth).json()['data']['unit_vc'], 13)


class FreeEntryTests(TestCase):
    """`free_entry`, on the endpoint that debits the wallet."""

    def setUp(self):
        self.organiser, _ = a_user('ent_forg')
        self.org = an_org(self.organiser, 'Free Entry Org')
        self.member, self.member_auth = a_user('ent_fmem', coins=50)
        self.outsider, self.outsider_auth = a_user('ent_fout', coins=50)

        self.plan = a_plan(self.organiser, org=self.org, name='Entry Club',
                           benefits=[{'key': 'free_entry'}])
        self.sub = a_live_subscription(self.member, self.plan)

        # A paid tournament demands KYC and a PIN of everybody, member or not,
        # and it demands them BEFORE the fee is worked out. That is the locked
        # CEO decision from 26 May and the waiver does not touch it: a
        # membership pays the entry fee, it does not skip identity checks.
        for who in (self.member, self.outsider):
            wallet = UserWallet.objects.get(user=who)
            wallet.kyc_verified = True
            wallet.pin_hash = make_password('0000')
            wallet.save(update_fields=['kyc_verified', 'pin_hash'])

        game = Games.objects.create(game_title='EA FC Entitlement')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Free Entry Probe', tournament_game=game,
            tournament_creator=self.organiser, tournament_organization=self.org,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Paid', entry_fee_price=10,
            min_number_of_teams=2, max_number_of_teams=32)

    def register(self, auth):
        return self.client.post(
            '/tournament/register-tournament/',
            data={'tournament_id': self.tournament.tournament_id, 'pin': '0000'},
            content_type='application/json', **auth)

    def test_the_membership_pays_the_entry_fee(self):
        before = UserWallet.objects.get(user=self.member).wallet_balance
        res = self.register(self.member_auth)
        self.assertIn(res.status_code, (200, 201), res.data)
        self.assertTrue(res.data['data']['entry_waived_by_membership'])
        self.assertEqual(res.data['data']['coins_deducted'], 0)
        self.assertEqual(
            UserWallet.objects.get(user=self.member).wallet_balance, before)

    def test_a_lapsed_member_is_charged_on_the_next_request(self):
        lapse(self.sub)
        before = UserWallet.objects.get(user=self.member).wallet_balance
        res = self.register(self.member_auth)
        self.assertIn(res.status_code, (200, 201), res.data)
        self.assertFalse(res.data['data']['entry_waived_by_membership'])
        self.assertEqual(res.data['data']['coins_deducted'], 10)
        self.assertEqual(
            UserWallet.objects.get(user=self.member).wallet_balance, before - 10)

    def test_an_expired_membership_is_charged_whatever_the_dates_say(self):
        expire(self.sub)
        res = self.register(self.member_auth)
        self.assertIn(res.status_code, (200, 201), res.data)
        self.assertEqual(res.data['data']['coins_deducted'], 10)

    def test_somebody_with_no_membership_pays(self):
        before = UserWallet.objects.get(user=self.outsider).wallet_balance
        res = self.register(self.outsider_auth)
        self.assertIn(res.status_code, (200, 201), res.data)
        self.assertEqual(res.data['data']['coins_deducted'], 10)
        self.assertEqual(
            UserWallet.objects.get(user=self.outsider).wallet_balance,
            before - 10)

    def test_a_signed_out_caller_registers_for_nothing(self):
        """The API is the permission.

        Asserted on the OUTCOME rather than on the status code: this endpoint
        answers 400 to an anonymous caller where the platform rule asks for 401
        or 403, which is worth fixing and is not this change's to fix. What
        must be true either way is that nobody got in.
        """
        from vent_tournament.models import TournamentRegistration

        res = self.client.post(
            '/tournament/register-tournament/',
            data={'tournament_id': self.tournament.tournament_id},
            content_type='application/json')
        self.assertNotIn(res.status_code, (200, 201))
        self.assertEqual(
            TournamentRegistration.objects.filter(
                tournament=self.tournament).count(), 0)


class RegistrationWindowTests(TestCase):
    """`priority_registration`, and the window it is early to.

    The window was on the model, was sent to every screen, and was enforced by
    nothing, which made early access a benefit with nothing to be early to.
    """

    def setUp(self):
        self.organiser, _ = a_user('ent_worg')
        self.org = an_org(self.organiser, 'Window Org')
        self.member, self.member_auth = a_user('ent_wmem', coins=50)
        self.outsider, self.outsider_auth = a_user('ent_wout', coins=50)

        self.plan = a_plan(self.organiser, org=self.org, name='Early Club',
                           benefits=[{'key': 'priority_registration'}])
        self.sub = a_live_subscription(self.member, self.plan)

        game = Games.objects.create(game_title='EA FC Window')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Window Probe', tournament_game=game,
            tournament_creator=self.organiser, tournament_organization=self.org,
            start_date_and_time=now + timedelta(days=20),
            end_date_and_time=now + timedelta(days=21),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0,
            registration_opens_at=now + timedelta(days=3),
            min_number_of_teams=2, max_number_of_teams=32)

    def register(self, auth):
        return self.client.post(
            '/tournament/register-tournament/',
            data={'tournament_id': self.tournament.tournament_id},
            content_type='application/json', **auth)

    def test_registration_that_has_not_opened_is_refused(self):
        res = self.register(self.outsider_auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'REGISTRATION_NOT_OPEN')
        self.assertEqual(res.data['data']['early_access_benefit'],
                         'priority_registration')

    def test_a_member_with_early_access_gets_in(self):
        res = self.register(self.member_auth)
        self.assertIn(res.status_code, (200, 201), res.data)

    def test_a_lapsed_member_is_refused_on_the_next_request(self):
        lapse(self.sub)
        res = self.register(self.member_auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'REGISTRATION_NOT_OPEN')

    def test_a_tournament_with_no_window_refuses_nobody(self):
        """Almost every tournament. This is the assertion that says the new
        enforcement cannot start refusing registrations that used to work."""
        self.tournament.registration_opens_at = None
        self.tournament.save(update_fields=['registration_opens_at'])
        self.assertIn(self.register(self.outsider_auth).status_code, (200, 201))

    def test_a_window_that_has_already_opened_refuses_nobody(self):
        self.tournament.registration_opens_at = (
            timezone.now() - timedelta(days=1))
        self.tournament.save(update_fields=['registration_opens_at'])
        self.assertIn(self.register(self.outsider_auth).status_code, (200, 201))


class CatalogueHasCallersTests(TestCase):
    """The catcher for the fault class itself.

    Every benefit key that promises something concrete must have somewhere
    outside `vent_billing` that asks about it. A new key added to the catalogue
    without an endpoint that honours it fails HERE, at the moment it is added,
    rather than on the day a member notices they paid for nothing.
    """

    #: Keys that are honestly a rendering concern, with the reason, so that
    #: adding one is a decision somebody wrote down rather than an omission.
    DISPLAY_ONLY = {
        'member_badge': 'a badge is drawn beside a name; there is nothing to enforce',
    }

    def test_every_benefit_is_either_enforced_or_declared_display_only(self):
        from . import benefits

        for key in benefits.CATALOGUE:
            with self.subTest(key=key):
                self.assertTrue(
                    key in entitlements.ENFORCED or key in self.DISPLAY_ONLY,
                    '%s is sold on plan pages and nothing honours it. Either '
                    'add the endpoint that asks, or declare it display only '
                    'with the reason.' % key)

    def test_the_enforced_list_only_names_real_catalogue_keys(self):
        from . import benefits

        for key in entitlements.ENFORCED:
            self.assertIn(key, benefits.CATALOGUE)

    def test_the_seller_resolver_handles_both_models_and_neither(self):
        organiser, _ = a_user('ent_seller')
        org = an_org(organiser, 'Seller Org')
        game = Games.objects.create(game_title='EA FC Seller')
        now = timezone.now()
        tournament = Tournament.objects.create(
            tournament_title='Seller Probe', tournament_game=game,
            tournament_creator=organiser, tournament_organization=org,
            start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=7),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0)
        event = Event.objects.create(
            name='Seller Event', creator=organiser, event_type='virtual',
            desc='x', entry_fee=0, start_date=now, end_date=now)

        self.assertEqual(entitlements.seller_of(tournament), (org, organiser))
        self.assertEqual(entitlements.seller_of(event), (None, organiser))
        self.assertEqual(entitlements.seller_of(None), (None, None))

    def test_nobody_holds_anything(self):
        """A signed-out caller reaches these helpers on every public quote."""
        self.assertEqual(entitlements.ticket_discount_pct(None, None), 0)
        self.assertFalse(entitlements.waives_entry_fee(None, None))
        self.assertFalse(entitlements.may_register_early(None, None))
        self.assertEqual(entitlements.registration_window_error(None, None),
                         (None, None))

    def test_the_discount_arithmetic_is_decimal_and_never_negative(self):
        self.assertEqual(entitlements.discounted(Decimal('20000'), 25),
                         Decimal('15000.00'))
        self.assertEqual(entitlements.discounted(Decimal('20000'), 0),
                         Decimal('20000'))
        self.assertEqual(entitlements.discounted(Decimal('20000'), 100),
                         Decimal('0.00'))
        # A value the catalogue clamps before it is ever stored, asserted here
        # so the arithmetic is safe even if something bypasses the clamp.
        self.assertEqual(entitlements.discounted(Decimal('20000'), 150),
                         Decimal('0.00'))
