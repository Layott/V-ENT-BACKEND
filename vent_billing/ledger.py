"""What a plan has earned, who is owed it, and paying them once.

The same three rules as the event ledger built on 7 September, and for the same
reasons. They are repeated here rather than imported because the tables are
different; the RULES are the shared thing, and they are worth reading twice:

1. **A ledger, not a balance.** A balance is the sum of unsettled lines. A
   running total incremented at the till drifts the first time a refund lands,
   and once it has drifted there is no way to find out by how much.
2. **A line is paid once.** A settlement stamps every line it paid with the run
   that paid it, inside the same transaction that moves the coins. Running it
   twice pays nothing the second time, which is what makes a RUN safe where a
   queue is not.
3. **The fee is decided at the charge, not at the settlement.** The rate is
   stamped on the lines when the money is taken, so a rate change next month
   never rewrites what a plan earned last month.

## Where a subscription's money goes

To the organisation's wallet when the plan belongs to one, and to the owner's
own wallet when it does not. That is the only difference from the event ledger,
and it exists because a plan can belong to a person who has not made an
organisation - which is deliberate, since making somebody create an
organisation before they can sell a membership is a wall in front of the
feature.
"""
from django.db import transaction
from django.db.models import Sum

from .models import BillingLedgerEntry, BillingSettlement


def balances(plan):
    """What is owed and what has been paid on this plan. Summed from lines."""

    def total(kind, settled):
        rows = BillingLedgerEntry.objects.filter(plan=plan)
        # A reversal carries its own kind, so it is matched against the kind it
        # reverses rather than counted on its own.
        rows = rows.filter(kind=kind) | rows.filter(
            kind=BillingLedgerEntry.KIND_REVERSAL, reverses__kind=kind)
        rows = rows.filter(settled_at__isnull=not settled)
        return rows.aggregate(n=Sum('amount_vc'))['n'] or 0

    return {
        'owed_vc': total(BillingLedgerEntry.KIND_ORGANISER, False),
        'paid_vc': total(BillingLedgerEntry.KIND_ORGANISER, True),
        'platform_fee_vc': (total(BillingLedgerEntry.KIND_PLATFORM, False)
                            + total(BillingLedgerEntry.KIND_PLATFORM, True)),
    }


def settle(plan, run_by=None, note=''):
    """Pay everybody owed anything on this plan, once, in one pass.

    Returns the run, whose `lines_paid` is 0 when there was nothing to do. That
    is a real answer rather than an error: pressing Settle on a plan that is
    already settled should say so, not fail, because an error there reads as
    something being broken and invites somebody to press it again.
    """
    from vent_auth.models import OrgWallet, Transaction, UserWallet

    with transaction.atomic():
        run = BillingSettlement.objects.create(plan=plan, run_by=run_by,
                                               note=note[:200])

        open_lines = list(BillingLedgerEntry.objects
                          .select_for_update()
                          .filter(plan=plan, settled_at__isnull=True)
                          .exclude(kind=BillingLedgerEntry.KIND_PLATFORM)
                          .exclude(reverses__kind=BillingLedgerEntry.KIND_PLATFORM))

        amount = sum(line.amount_vc for line in open_lines)
        ids = [line.pk for line in open_lines]
        paid_total = 0
        paid_lines = 0

        if amount > 0 and ids:
            wallet = None
            if plan.org_id:
                wallet = OrgWallet.objects.select_for_update().filter(
                    org_id=plan.org_id).first()
            if wallet is None:
                wallet = UserWallet.objects.select_for_update().filter(
                    user_id=plan.owner_id).first()

            if wallet is not None:
                wallet.wallet_balance += amount
                wallet.save(update_fields=['wallet_balance'])
                # A transaction belongs to exactly ONE of the three wallets,
                # and the database has a constraint saying so. Decided from
                # what the wallet IS rather than from what the plan has, so a
                # plan whose organisation has no wallet row still pays its
                # owner rather than writing a row the constraint refuses.
                kwargs = ({'org_wallet': wallet}
                          if isinstance(wallet, OrgWallet) else {'wallet': wallet})
                Transaction.objects.create(
                    type='prize', amount=amount,
                    description='Memberships - %s' % plan.name,
                    status='completed', **kwargs)
                BillingLedgerEntry.objects.filter(pk__in=ids).update(
                    settled_at=run.created_at, settlement=run)
                paid_total = amount
                paid_lines = len(ids)

        # Refunds outweighing charges leaves the lines OPEN rather than paying a
        # negative amount. Clawing coins back out of somebody's wallet is not
        # something a settlement run may do on its own, and leaving the lines
        # open keeps the debt visible so it nets against the next run.

        run.amount_vc = paid_total
        run.lines_paid = paid_lines
        run.save(update_fields=['amount_vc', 'lines_paid'])
        return run
