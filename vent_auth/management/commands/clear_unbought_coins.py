"""Take away coins that nobody bought.

CEO, 29 September 2026: "can we remove all fake coins from the site now, the
only way coins should exist on the site is if someone buys them, cause coins
will soon be equivalent to real money."

On that day production held 47,500 VENT COINS in the sixteen demo seed
accounts (`demo_*` at @seed.v-ent.co), written straight onto their wallets by
`seed_demo`, and one coin somebody had actually paid for.

A seed account's whole balance is set to 0, with a completed `deduction` row
saying why, so the statement still adds up and nothing disappears silently.
Any OTHER wallet holding more than it ever bought is listed and left alone: a
real person's balance is never changed by a script, it is looked at.

    python manage.py clear_unbought_coins            # what would change
    python manage.py clear_unbought_coins --apply    # do it
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q, Sum

from vent_auth.models import Transaction, UserWallet

SEED_PREFIX = 'demo_'
SEED_DOMAIN = '@seed.v-ent.co'
NOTE = 'Removed: coins that were never bought (demo account)'


def bought(wallet):
    """Coins this wallet paid real money for (a gateway reference, completed)."""
    paid = Q(type='top_up', status='completed') & (
        Q(reference__startswith='FLW-') | Q(reference__startswith='VENT-'))
    return int(wallet.transactions.filter(paid).aggregate(n=Sum('amount'))['n'] or 0)


class Command(BaseCommand):
    help = 'Set demo seed wallets to 0 and list any other wallet holding coins it did not buy.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, apply, **_):
        seed = Q(user__username__startswith=SEED_PREFIX) & Q(user__email__endswith=SEED_DOMAIN)
        cleared = total = 0
        for wallet in UserWallet.objects.filter(seed, wallet_balance__gt=0).select_related('user'):
            amount = wallet.wallet_balance
            self.stdout.write('%s %-20s %6d VC -> 0' % ('clear' if apply else 'would clear',
                                                         wallet.user.username, amount))
            total += amount
            if apply:
                with transaction.atomic():
                    locked = UserWallet.objects.select_for_update().get(pk=wallet.pk)
                    if locked.wallet_balance <= 0:
                        continue
                    Transaction.objects.create(
                        wallet=locked, type='deduction', amount=-locked.wallet_balance,
                        description=NOTE, status='completed')
                    locked.wallet_balance = 0
                    locked.save(update_fields=['wallet_balance'])
            cleared += 1

        for wallet in UserWallet.objects.exclude(seed).filter(wallet_balance__gt=0).select_related('user'):
            paid_for = bought(wallet)
            if wallet.wallet_balance > paid_for:
                self.stdout.write('LOOK AT %s: holds %d VC, bought %d VC (left unchanged)'
                                  % (wallet.user.username, wallet.wallet_balance, paid_for))

        self.stdout.write('%s %d demo wallet(s), %d VC' % (
            'cleared' if apply else 'would clear', cleared, total))
