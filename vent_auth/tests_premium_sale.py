"""Buying premium, instead of asking somebody for it.

CEO, 10 September 2026: "They shouldnt be requesting a vent admin to turn on
anything."

What these hold, beyond "it sets the field":

  * zero price means NOT ON SALE, and the refusal is a code the page branches
    on rather than a sentence;
  * the button still does something when it is not on sale, and what it does is
    recorded where the console can read it;
  * time is ADDED, never overwritten, so paying twice buys two months;
  * an open-ended grant is never quietly converted into a dated subscription by
    somebody paying for what they already have;
  * a lapsed subscription starts again today rather than backdating itself;
  * the coins come out of the same wallet everything else uses, with a
    Transaction to show for it;
  * expiry turns it off, and never touches a grant.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import premium_admin, premium_sale
from .models import (AdminSetting, Organization, OrgMember, PremiumInterest,
                     PremiumPurchase, Transaction, Users, UserWallet)


def make_user(i, *, coins=0):
    user = Users.objects.create(
        username='ps%s' % i, email='ps%s@test.co' % i,
        login_session_token='pst%s' % str(i).zfill(11),
        login_session_created_at=timezone.now(),
        is_active=True,
    )
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user,
                              wallet_balance=coins)
    return user


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return client


def set_price(monthly=0, yearly=0):
    row = AdminSetting.load()
    data = dict(row.data or {})
    data['premium'] = {'price_vc_monthly': monthly, 'price_vc_yearly': yearly}
    row.data = data
    row.save()


class NotOnSaleTests(TestCase):
    """Zero is the default, and it is not the same as free."""

    def setUp(self):
        self.user = make_user(1, coins=1000)
        self.client = client_for(self.user)

    def test_offer_says_it_is_not_on_sale(self):
        res = self.client.get('/auth/premium/offer/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['data']['on_sale'])
        self.assertEqual(res.data['data']['price_vc_monthly'], 0)

    def test_offer_lists_the_real_gated_features(self):
        """Not marketing copy. The list comes from the module that gates."""
        res = self.client.get('/auth/premium/offer/')
        keys = {f['key'] for f in res.data['data']['features']}
        self.assertIn('automated_prizes', keys)
        self.assertIn('entry_requirements_advanced', keys)

    def test_offer_is_open_to_a_stranger(self):
        """A price behind a sign-in wall is a price nobody finds."""
        res = APIClient().get('/auth/premium/offer/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['data']['signed_in'])

    def test_buying_is_refused_with_a_code(self):
        res = self.client.post('/auth/premium/buy/', {'months': 1},
                               format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PREMIUM_NOT_ON_SALE')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_premium)

    def test_nothing_is_charged_when_it_is_refused(self):
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance,
                         1000)

    def test_the_button_still_does_something(self):
        """The whole point. Nobody is sent to find a member of staff."""
        res = self.client.post('/auth/premium/interest/',
                               {'surface': 'entry-requirements'}, format='json')
        self.assertEqual(res.status_code, 200)
        row = PremiumInterest.objects.get(user=self.user)
        self.assertEqual(row.surface, 'entry-requirements')
        self.assertEqual(row.times, 1)

    def test_pressing_twice_is_one_person(self):
        self.client.post('/auth/premium/interest/', {}, format='json')
        res = self.client.post('/auth/premium/interest/', {}, format='json')
        self.assertEqual(res.data['data']['times'], 2)
        self.assertEqual(PremiumInterest.objects.filter(user=self.user).count(), 1)

    def test_interest_is_refused_once_it_is_on_sale(self):
        set_price(monthly=25)
        res = self.client.post('/auth/premium/interest/', {}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PREMIUM_ON_SALE')

    def test_a_stranger_cannot_register_interest(self):
        res = APIClient().post('/auth/premium/interest/', {}, format='json')
        self.assertEqual(res.status_code, 401)


class BuyingTests(TestCase):

    def setUp(self):
        set_price(monthly=25, yearly=250)
        self.user = make_user(2, coins=100)
        self.client = client_for(self.user)

    def test_one_month_turns_it_on_and_takes_the_coins(self):
        res = self.client.post('/auth/premium/buy/', {'months': 1},
                               format='json')
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_premium)
        self.assertIsNotNone(self.user.premium_until)
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance,
                         75)

    def test_the_period_is_thirty_days(self):
        before = timezone.now()
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        self.user.refresh_from_db()
        self.assertAlmostEqual(
            (self.user.premium_until - before).days, 30, delta=1)

    def test_a_year_uses_the_yearly_price(self):
        """A year that costs twelve times the month is arithmetic, not a year."""
        self.user.wallet.wallet_balance = 400
        self.user.wallet.save(update_fields=['wallet_balance'])
        res = self.client.post('/auth/premium/buy/', {'months': 12},
                               format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['coins'], 250)

    def test_three_months_costs_three_times(self):
        self.client.post('/auth/premium/buy/', {'months': 3}, format='json')
        self.assertEqual(PremiumPurchase.objects.get().coins, 75)

    def test_it_writes_a_transaction_on_the_real_wallet(self):
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        tx = Transaction.objects.filter(wallet__user=self.user).latest('id')
        self.assertEqual(tx.amount, -25)
        self.assertIn('premium', tx.description.lower())

    def test_it_records_the_purchase_with_the_price_it_charged(self):
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        row = PremiumPurchase.objects.get()
        self.assertEqual(row.coins, 25)
        self.assertEqual(row.buyer_id, self.user.user_id)
        self.assertEqual(row.user_id, self.user.user_id)
        self.assertIsNone(row.org_id)

    def test_a_price_change_does_not_rewrite_what_was_paid(self):
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        set_price(monthly=99)
        self.assertEqual(PremiumPurchase.objects.get().coins, 25)

    def test_it_is_refused_when_the_balance_is_short(self):
        poor = make_user(3, coins=5)
        res = client_for(poor).post('/auth/premium/buy/', {'months': 1},
                                    format='json')
        self.assertEqual(res.status_code, 402)
        self.assertEqual(res.data['code'], 'INSUFFICIENT_FUNDS')
        poor.refresh_from_db()
        self.assertFalse(poor.is_premium)
        self.assertEqual(UserWallet.objects.get(user=poor).wallet_balance, 5)

    def test_paying_twice_buys_two_months(self):
        """Time is added, never overwritten."""
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        self.user.refresh_from_db()
        first_end = self.user.premium_until
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        self.user.refresh_from_db()
        self.assertAlmostEqual(
            (self.user.premium_until - first_end).days, 30, delta=1)

    def test_a_lapsed_subscription_starts_again_today(self):
        self.user.is_premium = False
        self.user.premium_until = timezone.now() - timedelta(days=40)
        self.user.save(update_fields=['is_premium', 'premium_until'])
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        self.user.refresh_from_db()
        self.assertGreater(self.user.premium_until,
                           timezone.now() + timedelta(days=29))

    def test_a_granted_account_is_refused_rather_than_charged(self):
        premium_admin.apply_premium(self.user, on=True, note='Partner')
        res = self.client.post('/auth/premium/buy/', {'months': 1},
                               format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'ALREADY_PREMIUM')
        self.assertEqual(UserWallet.objects.get(user=self.user).wallet_balance,
                         100)

    def test_the_note_says_it_was_paid_for(self):
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        self.user.refresh_from_db()
        self.assertEqual(self.user.premium_note, 'Paid 25 VC for 1 month')

    def test_a_stranger_cannot_buy(self):
        res = APIClient().post('/auth/premium/buy/', {'months': 1},
                               format='json')
        self.assertEqual(res.status_code, 401)

    def test_the_offer_says_where_the_buyer_stands(self):
        self.client.post('/auth/premium/buy/', {'months': 1}, format='json')
        res = self.client.get('/auth/premium/offer/')
        self.assertTrue(res.data['data']['me']['is_premium'])
        self.assertFalse(res.data['data']['me']['granted'])
        self.assertEqual(res.data['data']['balance_vc'], 75)


class BuyingForAnOrganisationTests(TestCase):

    def setUp(self):
        set_price(monthly=25)
        self.owner = make_user(4, coins=100)
        self.org = Organization.objects.create(
            org_name='Premium Buyers', org_creator=self.owner,
            org_owner=self.owner)
        self.stranger = make_user(5, coins=100)

    def test_the_owner_may_buy_for_it(self):
        res = client_for(self.owner).post(
            '/auth/premium/buy/',
            {'months': 1, 'organisation': self.org.slug}, format='json')
        self.assertEqual(res.status_code, 200)
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_premium)
        # Charged to the person, because an organisation has no wallet.
        self.assertEqual(UserWallet.objects.get(user=self.owner).wallet_balance,
                         75)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_premium)

    def test_a_stranger_may_not(self):
        res = client_for(self.stranger).post(
            '/auth/premium/buy/',
            {'months': 1, 'organisation': self.org.slug}, format='json')
        self.assertEqual(res.status_code, 403)
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_premium)
        self.assertEqual(
            UserWallet.objects.get(user=self.stranger).wallet_balance, 100)

    def test_a_member_may_not(self):
        """MAY_LINK is owner, admin and manager. A member speaks for nobody."""
        member = make_user(6, coins=100)
        OrgMember.objects.create(org=self.org, user=member, role='member')
        res = client_for(member).post(
            '/auth/premium/buy/',
            {'months': 1, 'organisation': self.org.slug}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_an_unknown_organisation_is_a_404(self):
        res = client_for(self.owner).post(
            '/auth/premium/buy/',
            {'months': 1, 'organisation': 'nope-not-here'}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_the_offer_lists_what_they_may_buy_for(self):
        res = client_for(self.owner).get('/auth/premium/offer/')
        slugs = [o['slug'] for o in res.data['data']['organisations']]
        self.assertIn(self.org.slug, slugs)


class ExpiryTests(TestCase):

    def setUp(self):
        set_price(monthly=25)
        self.paid = make_user(7, coins=100)
        self.granted = make_user(8, coins=0)

    def test_a_finished_period_turns_off(self):
        self.paid.is_premium = True
        self.paid.premium_until = timezone.now() - timedelta(hours=1)
        self.paid.premium_note = 'Paid 25 VC'
        self.paid.save(update_fields=['is_premium', 'premium_until',
                                      'premium_note'])
        counts = premium_sale.expire_due()
        self.assertEqual(counts['users'], 1)
        self.paid.refresh_from_db()
        self.assertFalse(self.paid.is_premium)
        self.assertEqual(self.paid.premium_note, '')

    def test_a_live_period_is_untouched(self):
        self.paid.is_premium = True
        self.paid.premium_until = timezone.now() + timedelta(days=5)
        self.paid.save(update_fields=['is_premium', 'premium_until'])
        premium_sale.expire_due()
        self.paid.refresh_from_db()
        self.assertTrue(self.paid.is_premium)

    def test_a_grant_never_expires(self):
        """The failure that would cost the most: a partner switched off."""
        premium_admin.apply_premium(self.granted, on=True, note='Rivalry season')
        premium_sale.expire_due()
        self.granted.refresh_from_db()
        self.assertTrue(self.granted.is_premium)
        self.assertEqual(self.granted.premium_note, 'Rivalry season')

    def test_granting_over_a_purchase_removes_the_end_date(self):
        """The grant is the newer decision, so it wins."""
        self.paid.is_premium = True
        self.paid.premium_until = timezone.now() + timedelta(days=2)
        self.paid.save(update_fields=['is_premium', 'premium_until'])
        premium_admin.apply_premium(self.paid, on=True, note='On us now')
        self.paid.refresh_from_db()
        self.assertIsNone(self.paid.premium_until)

    def test_an_organisation_expires_too(self):
        owner = make_user(9)
        org = Organization.objects.create(org_name='Lapsing Org',
                                          org_creator=owner, org_owner=owner)
        org.is_premium = True
        org.premium_until = timezone.now() - timedelta(minutes=1)
        org.save(update_fields=['is_premium', 'premium_until'])
        counts = premium_sale.expire_due()
        self.assertEqual(counts['orgs'], 1)
        org.refresh_from_db()
        self.assertFalse(org.is_premium)

    def test_lapsed_is_visible_before_the_sweep_runs(self):
        self.paid.is_premium = True
        self.paid.premium_until = timezone.now() - timedelta(minutes=1)
        self.paid.save(update_fields=['is_premium', 'premium_until'])
        self.assertTrue(self.paid.premium_has_lapsed())

    def test_a_grant_has_not_lapsed(self):
        premium_admin.apply_premium(self.granted, on=True, note='x')
        self.assertFalse(self.granted.premium_has_lapsed())


class BuyingClearsTheInterestTests(TestCase):

    def test_wanting_it_and_then_buying_it_removes_the_row(self):
        user = make_user(10, coins=100)
        premium_sale.register_interest(user, 'prize-plan')
        set_price(monthly=25)
        client_for(user).post('/auth/premium/buy/', {'months': 1},
                              format='json')
        self.assertEqual(PremiumInterest.objects.filter(user=user).count(), 0)
