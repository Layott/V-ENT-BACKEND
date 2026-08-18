"""Credit founding members with VENT COINS, after the fact.

There was no budget for a launch bonus, so the claim flow credits nothing and
the claim email promises nothing. What it does do is mark every claimer as a
founding member, which leaves the door open: when there is money, this pays them
all in one pass.

    python manage.py grant_founding_bonus --amount 2            # dry run
    python manage.py grant_founding_bonus --amount 2 --send

Idempotent. A user who already has a 'Founding member bonus' transaction is
skipped, so running it twice does not pay twice.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from vent_auth.models import Users, UserWallet, Transaction

BONUS_DESCRIPTION = 'Founding member bonus'


class Command(BaseCommand):
    help = "Credit VENT COINS to every founding member who has not been paid yet."

    def add_arguments(self, parser):
        parser.add_argument('--amount', type=int, required=True,
                            help='VENT COINS per founding member')
        parser.add_argument('--send', action='store_true',
                            help='Actually credit. Without this nothing is written.')

    def handle(self, *args, **options):
        amount = options['amount']
        send = options['send']

        if amount <= 0:
            self.stdout.write(self.style.ERROR('Amount must be positive.'))
            return

        founders = Users.objects.filter(is_founding_member=True).order_by('founding_position')
        already_paid = set(
            Transaction.objects
            .filter(description=BONUS_DESCRIPTION)
            .values_list('wallet__user_id', flat=True)
        )
        pending = [u for u in founders if u.pk not in already_paid]

        if not pending:
            self.stdout.write('Every founding member has already been credited.')
            return

        total = amount * len(pending)
        self.stdout.write(
            f'{len(pending)} founding members to credit at {amount} VC each. '
            f'Total {total} VC ({total * 1000:,} NGN).')

        if not send:
            self.stdout.write(self.style.WARNING('DRY RUN. Add --send to credit.'))
            return

        credited = skipped = 0
        for user in pending:
            with transaction.atomic():
                wallet = UserWallet.objects.select_for_update().filter(user=user).first()
                if wallet is None:
                    skipped += 1
                    continue
                wallet.wallet_balance += amount
                wallet.save(update_fields=['wallet_balance'])
                Transaction.objects.create(
                    wallet=wallet,
                    type='top_up',
                    amount=amount,
                    description=BONUS_DESCRIPTION,
                    status='completed',
                )
                credited += 1

        self.stdout.write(self.style.SUCCESS(f'Credited {credited}. Skipped {skipped} (no wallet).'))
