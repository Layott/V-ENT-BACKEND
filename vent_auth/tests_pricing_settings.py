"""Every platform price is a dashboard number, and every dashboard number is read.

CEO, 13 September 2026: "the 5% + NGN100 is something admins should be able to
set on the admin dashboard, they should be able to set what prices it is now
for any premium feature or option and it updates everywhere on the platform."

What these tests hold, key by key: a value written through the settings
endpoint is the value the charging code reads on the next call, with no
restart and no cache. `tools/check-pricing.py` holds the rest (that every key
has a control and a reader in the source); this file holds that the readers
actually read.
"""
import uuid
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth import payouts
from vent_auth.models import DEFAULT_ADMIN_SETTINGS, AdminSetting, Users


def an_admin():
    user = Users.objects.create(
        username='ps_admin_%s' % uuid.uuid4().hex[:4],
        email='ps_admin_%s@vent.test' % uuid.uuid4().hex[:4],
        full_name='Admin', is_staff=True, admin_role='super_admin',
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16])
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return client


class TheListOfMoneyKeysTests(TestCase):
    """The defaults are the one list. A key here is a promise of a reader."""

    def test_the_fee_section_holds_exactly_these_keys(self):
        self.assertEqual(set(DEFAULT_ADMIN_SETTINGS['platform_fees']), {
            'ticket_fee_pct', 'ticket_fee_flat_ngn', 'tournament_fee_pct',
            'tournament_fee_flat_ngn', 'subscription_fee_pct',
            'listing_fee_pct', 'anime_fee_pct', 'withdrawal_fee_pct',
            'withdrawal_fee_flat_ngn', 'payout_min_vc', 'payout_daily_max_vc',
            'topup_max_ngn_per_day',
        })

    def test_the_defaults_are_the_rule_the_ceo_set(self):
        fees = DEFAULT_ADMIN_SETTINGS['platform_fees']
        self.assertEqual((fees['ticket_fee_pct'], fees['ticket_fee_flat_ngn']), (5, 100))
        self.assertEqual(fees['subscription_fee_pct'], 5)
        self.assertEqual((fees['withdrawal_fee_pct'], fees['withdrawal_fee_flat_ngn']), (1, 0))
        self.assertEqual((fees['payout_min_vc'], fees['payout_daily_max_vc']), (5, 500))

    def test_a_fresh_install_serves_the_whole_list(self):
        merged = AdminSetting.load().merged()
        self.assertEqual(set(merged['platform_fees']), set(DEFAULT_ADMIN_SETTINGS['platform_fees']))
        self.assertEqual(set(merged['premium']), {'price_vc_monthly', 'price_vc_yearly'})


class EveryReaderReadsTheDashboardTests(TestCase):
    """Set a key where an admin would, read it where the code charges it."""

    def test_the_ticket_and_stall_fee(self):
        from vent_event.ledger import platform_fee
        self.assertEqual(platform_fee(), (Decimal('5'), Decimal('100')))
        AdminSetting.put('platform_fees', ticket_fee_pct=7, ticket_fee_flat_ngn=150)
        self.assertEqual(platform_fee(), (Decimal('7'), Decimal('150')))

    def test_the_tournament_fee(self):
        """Its own two keys. Removed on the morning of 13 September because an
        entry fee reached nobody; back that afternoon when the CEO said
        tournaments pay organisers a share ("i want it")."""
        from vent_event.ledger import tournament_fee
        self.assertEqual(tournament_fee(), (Decimal('5'), Decimal('100')))
        AdminSetting.put('platform_fees', tournament_fee_pct=8, tournament_fee_flat_ngn=50)
        self.assertEqual(tournament_fee(), (Decimal('8'), Decimal('50')))

    def test_the_subscription_fee_reads_its_own_key_and_not_the_ticket_rate(self):
        from vent_billing.charging import platform_rate
        self.assertEqual(platform_rate(), 5.0)
        AdminSetting.put('platform_fees', ticket_fee_pct=9, subscription_fee_pct=3)
        self.assertEqual(platform_rate(), 3.0)

    def test_the_marketplace_commission(self):
        from vent_marketplace.holds import commission_rate
        self.assertEqual(commission_rate(), 0.0)
        AdminSetting.put('platform_fees', listing_fee_pct=4)
        self.assertEqual(commission_rate(), 4.0)

    def test_the_anime_fee(self):
        from vent_anime.money import fee_on, platform_rate
        self.assertEqual(platform_rate(), 0.0)
        self.assertEqual(fee_on(10), 0)
        AdminSetting.put('platform_fees', anime_fee_pct=10)
        self.assertEqual(platform_rate(), 10.0)
        self.assertEqual(fee_on(10), 1)
        self.assertEqual(fee_on(9), 0, 'rounded down, never up')

    def test_the_payout_limits(self):
        self.assertEqual(payouts.limits(), {'minimum': 5, 'daily_max': 500})
        AdminSetting.put('platform_fees', payout_min_vc=2, payout_daily_max_vc=0)
        self.assertEqual(payouts.limits(), {'minimum': 2, 'daily_max': 0})

    def test_the_withdrawal_fee(self):
        self.assertEqual(payouts.fee_on(10)['fee_ngn'], Decimal('100.00'), '1% by default')
        AdminSetting.put('platform_fees', withdrawal_fee_pct=2, withdrawal_fee_flat_ngn=50)
        priced = payouts.fee_on(10)
        self.assertEqual(priced['gross_ngn'], Decimal('10000'))
        self.assertEqual(priced['fee_ngn'], Decimal('250.00'))
        self.assertEqual(priced['payout_ngn'], Decimal('9750.00'))

    def test_the_topup_ceiling(self):
        from vent_auth.views_wallet import topup_ceiling_ngn
        self.assertEqual(topup_ceiling_ngn(), 0)
        AdminSetting.put('platform_fees', topup_max_ngn_per_day=50000)
        self.assertEqual(topup_ceiling_ngn(), 50000)

    def test_the_premium_prices(self):
        from vent_auth.premium_sale import offer
        self.assertEqual(offer()['price_vc_monthly'], 0)
        AdminSetting.put('premium', price_vc_monthly=3, price_vc_yearly=30)
        self.assertEqual((offer()['price_vc_monthly'], offer()['price_vc_yearly']), (3, 30))


class TheEndpointIsTheDashboardTests(TestCase):
    """What the settings page saves is what the next sale reads."""

    def test_a_change_saved_on_the_dashboard_reaches_the_ledger_at_once(self):
        from vent_event.ledger import platform_fee
        client = an_admin()
        res = client.post('/auth/admin/settings/', {
            'platform_fees': {'ticket_fee_pct': 7, 'ticket_fee_flat_ngn': 150}}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(platform_fee(), (Decimal('7'), Decimal('150')))
        shown = client.get('/auth/admin/settings/').data['data']['platform_fees']
        self.assertEqual((shown['ticket_fee_pct'], shown['ticket_fee_flat_ngn']), (7, 150))
        # The keys nobody touched are still there, at their defaults.
        self.assertEqual(shown['subscription_fee_pct'], 5)


class SettingsRefuseBadMoneyTests(TestCase):
    """A stray string in a rate would break every sale on the platform."""

    def setUp(self):
        self.client = an_admin()

    def post(self, body):
        return self.client.post('/auth/admin/settings/', body, format='json')

    def test_a_string_where_a_number_should_be(self):
        res = self.post({'platform_fees': {'ticket_fee_pct': 'five'}})
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'PRICE_NOT_A_NUMBER')
        self.assertEqual(res.data['key'], 'ticket_fee_pct')
        self.assertEqual(AdminSetting.load().merged()['platform_fees']['ticket_fee_pct'], 5)

    def test_a_negative_rate(self):
        res = self.post({'premium': {'price_vc_monthly': -1}})
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'PRICE_NEGATIVE')

    def test_a_key_nothing_reads(self):
        res = self.post({'platform_fees': {'wager_fee_pct': 5}})
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'UNKNOWN_PRICE')
        self.assertNotIn('wager_fee_pct', AdminSetting.load().data.get('platform_fees') or {})

    def test_a_boolean_is_not_a_price(self):
        res = self.post({'platform_fees': {'listing_fee_pct': True}})
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data['code'], 'PRICE_NOT_A_NUMBER')

    def test_the_other_sections_are_not_policed(self):
        res = self.post({'banner': {'enabled': True, 'title': 'Hi'}})
        self.assertEqual(res.status_code, 200, res.data)

    def test_zero_is_allowed_because_zero_means_off(self):
        res = self.post({'platform_fees': {'withdrawal_fee_pct': 0, 'payout_daily_max_vc': 0}})
        self.assertEqual(res.status_code, 200, res.data)
