"""Who is owed what, and paying them once.

CEO, 7 September 2026, from the ticketing research: who bears the platform fee,
affiliates that actually get PAID, and a settlement run rather than a
one-at-a-time payout queue.

Before this, a ticket sale debited the buyer's wallet and credited nobody. The
money left one account and arrived nowhere.

The decisions worth pinning:

- **A ledger, not a balance.** Balances are summed from lines, never stored.
- **A line is paid once.** Running a settlement twice pays nothing the second
  time, which is what makes a RUN safe where a queue of individual payouts is
  not: a queue retried pays twice, and the person it pays twice never says so.
- **The rate is stamped at the sale.** Changing the platform fee never rewrites
  what an event earned before the change.
- **A free ticket carries no fee.** Either way round. A 0 VC ticket that
  quietly costs 1 VC would land on exactly the events least able to absorb it.
- **An affiliate earns from the ticket price, never from the platform's fee.**
"""
from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import (AdminSetting, Games, Transaction, UserWallet,
                              Users)

from . import ledger
from .models import (Event, EventLedgerEntry, EventReferral, Ticket,
                     TicketTier)


def a_user(name, coins=0):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('l-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    UserWallet.objects.create(user_wallet_id=name[:10], user=user,
                              wallet_balance=coins,
                              pin_hash='pbkdf2_sha256$dummy')
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def set_fee(pct):
    row = AdminSetting.load()
    data = dict(row.data or {})
    fees = dict(data.get('platform_fees') or {})
    fees['ticket_fee_pct'] = pct
    data['platform_fees'] = fees
    row.data = data
    row.save()


class LedgerBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organiser, self.auth = a_user('ld_org')
        self.buyer, self.buyer_auth = a_user('ld_buyer', coins=1000)
        self.stranger, self.stranger_auth = a_user('ld_other')
        game = Games.objects.create(game_title='EA FC LD')
        now = timezone.localtime(timezone.now())
        self.event = Event.objects.create(
            name='Ledger Probe', game=game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now() - timedelta(days=5),
            reg_end_date=timezone.now() + timedelta(days=5),
            event_date=(now + timedelta(days=3)).date(),
            start_time=time(18, 0), end_time=time(22, 0),
            location='Lagos', capacity=100)
        # 20000 NGN at the platform's 1000 NGN per coin is 20 VC, which
        # divides by 10 per cent cleanly enough to read in a test and not so
        # cleanly that a rounding bug hides.
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=20000, quantity=50)
        set_fee(0)

    def a_ticket(self, code='LD00001', vc=20):
        return Ticket.objects.create(event=self.event, tier=self.tier,
                                     user=self.buyer, code=code,
                                     price_vc=vc, price_ngn=20000)


class WhoBearsTheFeeTests(LedgerBase):
    def test_with_no_fee_set_the_buyer_pays_the_price_and_nothing_else(self):
        q = ledger.quote(self.tier, 1, self.event)
        self.assertEqual(q['fee_vc'], 0)
        self.assertEqual(q['total_vc'], q['tickets_vc'])
        self.assertEqual(q['organiser_vc'], q['tickets_vc'])

    def test_on_the_organiser_it_comes_out_of_what_they_receive(self):
        set_fee(10)
        q = ledger.quote(self.tier, 1, self.event)
        self.assertEqual(q['tickets_vc'], 20)
        self.assertEqual(q['fee_vc'], 2)
        # The buyer pays what the page said.
        self.assertEqual(q['total_vc'], 20)
        self.assertEqual(q['organiser_vc'], 18)

    def test_on_the_buyer_it_is_added_on_top_and_the_organiser_keeps_the_price(self):
        set_fee(10)
        self.event.fee_bearer = Event.FEE_BUYER
        self.event.save(update_fields=['fee_bearer'])
        q = ledger.quote(self.tier, 1, self.event)
        self.assertEqual(q['total_vc'], 22)
        self.assertEqual(q['organiser_vc'], 20)

    def test_a_free_ticket_carries_no_fee_either_way(self):
        """A 0 VC ticket that quietly costs 1 VC is the trap, and it would land
        on exactly the events least able to absorb it."""
        set_fee(25)
        free = TicketTier.objects.create(event=self.event, name='Free',
                                         price=0, quantity=10)
        for bearer in (Event.FEE_ORGANISER, Event.FEE_BUYER):
            self.event.fee_bearer = bearer
            self.event.save(update_fields=['fee_bearer'])
            q = ledger.quote(free, 3, self.event)
            self.assertEqual(q['fee_vc'], 0, bearer)
            self.assertEqual(q['total_vc'], 0, bearer)

    def test_the_fee_rounds_down_so_the_platform_never_takes_a_coin_it_did_not_earn(self):
        set_fee(10)
        # 1 VC at 10 per cent is 0.1, which is not a coin.
        self.assertEqual(ledger.fee_on(1), 0)
        self.assertEqual(ledger.fee_on(19), 1)

    def test_the_organiser_can_choose_and_a_stranger_cannot(self):
        res = self.client.post('/event/%s/fee-bearer/' % self.event.slug,
                               {'fee_bearer': 'buyer'}, format='json',
                               **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.event.refresh_from_db()
        self.assertEqual(self.event.fee_bearer, 'buyer')

        res = self.client.post('/event/%s/fee-bearer/' % self.event.slug,
                               {'fee_bearer': 'organiser'}, format='json',
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_nonsense_is_refused_rather_than_stored(self):
        res = self.client.post('/event/%s/fee-bearer/' % self.event.slug,
                               {'fee_bearer': 'somebody else'}, format='json',
                               **self.auth)
        self.assertEqual(res.status_code, 400)
        self.event.refresh_from_db()
        self.assertEqual(self.event.fee_bearer, 'organiser')


class RecordingTests(LedgerBase):
    def test_a_sale_writes_the_organiser_line_and_the_platform_line(self):
        set_fee(10)
        priced = ledger.quote(self.tier, 1, self.event)
        ledger.record_sale(self.event, [self.a_ticket()], priced)
        kinds = dict(EventLedgerEntry.objects.values_list('kind', 'amount_vc'))
        self.assertEqual(kinds['organiser'], 18)
        self.assertEqual(kinds['platform'], 2)

    def test_with_no_fee_there_is_no_platform_line_at_all(self):
        priced = ledger.quote(self.tier, 1, self.event)
        ledger.record_sale(self.event, [self.a_ticket()], priced)
        self.assertEqual(EventLedgerEntry.objects.filter(kind='platform').count(), 0)

    def test_the_rate_is_stamped_so_a_later_change_rewrites_nothing(self):
        set_fee(10)
        priced = ledger.quote(self.tier, 1, self.event)
        ledger.record_sale(self.event, [self.a_ticket()], priced)
        set_fee(50)
        line = EventLedgerEntry.objects.get(kind='organiser')
        self.assertEqual(line.fee_pct, 10)
        self.assertEqual(line.amount_vc, 18)
        self.assertEqual(ledger.balances(self.event)['organiser_owed_vc'], 18)

    def test_an_affiliate_earns_from_the_ticket_price_not_the_platform_fee(self):
        """Otherwise an event with the fee passed to the buyer quietly pays its
        affiliates more than the same event without."""
        set_fee(10)
        self.event.fee_bearer = Event.FEE_BUYER
        self.event.save(update_fields=['fee_bearer'])
        link = EventReferral.objects.create(
            event=self.event, name='Ada', code='ADA', commission_pct=20,
            payee=self.stranger)
        priced = ledger.quote(self.tier, 1, self.event)
        ledger.record_sale(self.event, [self.a_ticket()], priced, referral=link)
        commission = EventLedgerEntry.objects.get(kind='affiliate')
        # 20 per cent of the 20 VC ticket, not of the 22 VC the buyer paid.
        self.assertEqual(commission.amount_vc, 4)

    def test_a_tracking_link_with_no_commission_pays_nobody(self):
        """Which is what every link on the platform did before this, and stays
        the ordinary case."""
        link = EventReferral.objects.create(event=self.event, name='Just tracking',
                                            code='TRK')
        priced = ledger.quote(self.tier, 1, self.event)
        ledger.record_sale(self.event, [self.a_ticket()], priced, referral=link)
        self.assertEqual(EventLedgerEntry.objects.filter(kind='affiliate').count(), 0)

    def test_the_organiser_gets_what_is_left_after_the_fee_and_the_commission(self):
        set_fee(10)
        link = EventReferral.objects.create(
            event=self.event, name='Ada', code='ADA', commission_pct=20,
            payee=self.stranger)
        priced = ledger.quote(self.tier, 1, self.event)
        ledger.record_sale(self.event, [self.a_ticket()], priced, referral=link)
        # 20 gross, less 2 platform, less 4 commission.
        self.assertEqual(EventLedgerEntry.objects.get(kind='organiser').amount_vc, 14)


class ReversalTests(LedgerBase):
    def test_a_refund_writes_an_opposite_line_rather_than_editing_the_original(self):
        set_fee(10)
        ticket = self.a_ticket()
        ledger.record_sale(self.event, [ticket],
                           ledger.quote(self.tier, 1, self.event))
        original = EventLedgerEntry.objects.get(kind='organiser')

        ledger.reverse_sale(ticket, reason='Voided')

        original.refresh_from_db()
        self.assertEqual(original.amount_vc, 18)  # untouched
        self.assertIsNotNone(original.reversed_by_id)
        self.assertEqual(ledger.balances(self.event)['organiser_owed_vc'], 0)

    def test_reversing_twice_does_not_double_the_reversal(self):
        ticket = self.a_ticket()
        ledger.record_sale(self.event, [ticket],
                           ledger.quote(self.tier, 1, self.event))
        ledger.reverse_sale(ticket)
        ledger.reverse_sale(ticket)
        self.assertEqual(ledger.balances(self.event)['organiser_owed_vc'], 0)
        self.assertEqual(
            EventLedgerEntry.objects.filter(kind='reversal').count(), 1)


class SettlementTests(LedgerBase):
    def sell(self, code, referral=None):
        priced = ledger.quote(self.tier, 1, self.event)
        ledger.record_sale(self.event, [self.a_ticket(code)], priced,
                           referral=referral)

    def test_one_run_pays_the_organiser_what_they_are_owed(self):
        set_fee(10)
        self.sell('LD00001')
        self.sell('LD00002')
        run = ledger.settle(self.event, run_by=self.organiser)
        self.assertEqual(run.amount_vc, 36)
        self.assertEqual(
            UserWallet.objects.get(user=self.organiser).wallet_balance, 36)

    def test_running_it_a_second_time_pays_nothing(self):
        """The whole difference between a run and a queue. A queue worked
        through twice pays twice, and the person it pays twice never says so."""
        self.sell('LD00001')
        first = ledger.settle(self.event)
        second = ledger.settle(self.event)
        self.assertEqual(first.amount_vc, 20)
        self.assertEqual(second.amount_vc, 0)
        self.assertEqual(second.lines_paid, 0)
        self.assertEqual(
            UserWallet.objects.get(user=self.organiser).wallet_balance, 20)

    def test_somebody_owed_on_four_sales_gets_one_payment(self):
        for i in range(4):
            self.sell('LD0000%d' % i)
        ledger.settle(self.event)
        rows = Transaction.objects.filter(wallet__user=self.organiser,
                                          type='prize')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().amount, 80)

    def test_the_affiliate_is_paid_in_the_same_run(self):
        link = EventReferral.objects.create(
            event=self.event, name='Ada', code='ADA', commission_pct=25,
            payee=self.stranger)
        self.sell('LD00001', referral=link)
        ledger.settle(self.event)
        self.assertEqual(
            UserWallet.objects.get(user=self.stranger).wallet_balance, 5)
        self.assertEqual(
            UserWallet.objects.get(user=self.organiser).wallet_balance, 15)

    def test_a_link_can_be_addressed_to_somebody_with_no_account(self):
        """The earnings screen said "it is paid the day they make an account"
        and NOTHING made that true: a payee could only ever be an existing
        account, so nobody could arrive later to claim. Found by walking it."""
        link = EventReferral.objects.create(
            event=self.event, name='Future Streamer', code='SOON',
            commission_pct=25, payee=None,
            payee_email='streamer@example.com')
        self.sell('LD00001', referral=link)
        self.assertEqual(ledger.balances(self.event)['affiliates_owed_vc'], 5)

    def test_signing_up_with_that_address_attaches_the_link_AND_its_open_lines(self):
        """Attaching the link alone is not enough. The lines already written
        name no user, so a settlement would skip them for ever."""
        from vent_auth.invites import claim_pending
        link = EventReferral.objects.create(
            event=self.event, name='Future Streamer', code='SOON',
            commission_pct=25, payee=None,
            payee_email='streamer@example.com')
        self.sell('LD00001', referral=link)

        newcomer, _auth = a_user('ld_streamer')
        newcomer.email = 'streamer@example.com'
        newcomer.save(update_fields=['email'])
        claim_pending(newcomer)

        link.refresh_from_db()
        self.assertEqual(link.payee, newcomer)
        self.assertEqual(
            EventLedgerEntry.objects.filter(
                referral=link, user__isnull=True).count(), 0)

    def test_the_money_that_accrued_is_paid_by_the_next_settlement(self):
        from vent_auth.invites import claim_pending
        link = EventReferral.objects.create(
            event=self.event, name='Future Streamer', code='SOON',
            commission_pct=25, payee=None,
            payee_email='streamer@example.com')
        self.sell('LD00001', referral=link)

        # A settlement BEFORE they arrive must not give their money away.
        first = ledger.settle(self.event)
        self.assertEqual(first.amount_vc, 15)
        self.assertEqual(ledger.balances(self.event)['affiliates_owed_vc'], 5)

        newcomer, _auth = a_user('ld_streamer')
        newcomer.email = 'streamer@example.com'
        newcomer.save(update_fields=['email'])
        claim_pending(newcomer)

        second = ledger.settle(self.event)
        self.assertEqual(second.amount_vc, 5)
        self.assertEqual(
            UserWallet.objects.get(user=newcomer).wallet_balance, 5)

    def test_an_unclaimed_link_keeps_its_money_rather_than_losing_it(self):
        """A code handed to somebody who has not signed up yet. The line stays
        open so it is paid the day they claim it."""
        link = EventReferral.objects.create(
            event=self.event, name='Nobody yet', code='SOON',
            commission_pct=25, payee=None)
        self.sell('LD00001', referral=link)
        ledger.settle(self.event)
        owed = ledger.balances(self.event)['affiliates_owed_vc']
        self.assertEqual(owed, 5)

    def test_the_platform_fee_is_never_paid_into_anybody_wallet(self):
        set_fee(10)
        self.sell('LD00001')
        before = UserWallet.objects.count()
        ledger.settle(self.event)
        self.assertEqual(UserWallet.objects.count(), before)
        self.assertEqual(ledger.balances(self.event)['platform_fee_vc'], 2)

    def test_a_refund_that_outweighs_the_sales_never_claws_coins_back(self):
        """Taking money out of somebody's wallet is not something a settlement
        run may decide to do on its own."""
        ticket = self.a_ticket('LD00001')
        ledger.record_sale(self.event, [ticket],
                           ledger.quote(self.tier, 1, self.event))
        ledger.settle(self.event)
        ledger.reverse_sale(ticket, reason='Refunded after settlement')
        balance = UserWallet.objects.get(user=self.organiser).wallet_balance
        ledger.settle(self.event)
        self.assertEqual(
            UserWallet.objects.get(user=self.organiser).wallet_balance, balance)

    def test_the_endpoint_is_the_organiser_and_nobody_else(self):
        self.sell('LD00001')
        res = self.client.post('/event/%s/settle/' % self.event.slug, {},
                               format='json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        res = self.client.post('/event/%s/settle/' % self.event.slug, {},
                               format='json')
        self.assertEqual(res.status_code, 401)

    def test_settling_an_event_with_nothing_owed_says_so_rather_than_failing(self):
        """An error there reads as something being broken and invites somebody
        to press it again."""
        res = self.client.post('/event/%s/settle/' % self.event.slug, {},
                               format='json', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['lines_paid'], 0)


class EarningsEndpointTests(LedgerBase):
    def test_the_organiser_sees_what_they_are_owed(self):
        set_fee(10)
        ledger.record_sale(self.event, [self.a_ticket()],
                           ledger.quote(self.tier, 1, self.event))
        res = self.client.get('/event/%s/earnings/' % self.event.slug,
                              **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:200])
        data = res.json()['data']
        self.assertEqual(data['organiser_owed_vc'], 18)
        self.assertEqual(data['platform_fee_vc'], 2)
        self.assertEqual(data['fee_bearer'], 'organiser')

    def test_a_stranger_cannot_read_the_takings(self):
        res = self.client.get('/event/%s/earnings/' % self.event.slug,
                              **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_cannot_either(self):
        res = self.client.get('/event/%s/earnings/' % self.event.slug)
        self.assertEqual(res.status_code, 401)

    def test_an_affiliate_is_named_rather_than_numbered(self):
        link = EventReferral.objects.create(
            event=self.event, name='Ada Okoro', code='ADA', commission_pct=10,
            payee=self.stranger)
        ledger.record_sale(self.event, [self.a_ticket()],
                           ledger.quote(self.tier, 1, self.event),
                           referral=link)
        res = self.client.get('/event/%s/earnings/' % self.event.slug,
                              **self.auth)
        rows = res.json()['data']['affiliates']
        self.assertEqual(rows[0]['name'], 'Ada Okoro')
        self.assertEqual(rows[0]['owed_vc'], 2)
