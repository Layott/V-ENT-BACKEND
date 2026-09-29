"""Settle or close top-ups that were started and left pending.

CEO, 29 September 2026: a top-up somebody started and never paid stayed
"Pending" on their statement for good, and was counted as money earned. A
payment page closed half way leaves exactly that row behind, and so does a
webhook that never arrived after a real payment.

For every pending Flutterwave top-up older than `--after` minutes this asks
Flutterwave what happened, through the same locked `settle_flutterwave_topup`
the webhook and the return page use, so it can never credit twice:

  paid           credited now (a payment whose webhook was lost is rescued)
  failed         marked failed
  never paid     marked cancelled once older than `--close-after` minutes
                 (a Flutterwave link lives 15 minutes)

Run from cron every 15 minutes:

    python manage.py settle_pending_topups
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_auth.models import Transaction


class Command(BaseCommand):
    help = 'Settle or close Flutterwave top-ups left pending.'

    def add_arguments(self, parser):
        parser.add_argument('--after', type=int, default=20,
                            help='Only rows older than this many minutes.')
        parser.add_argument('--close-after', type=int, default=120,
                            help='Close unpaid rows older than this many minutes.')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, after, close_after, dry_run, **_):
        from vent_auth import flutterwave
        from vent_auth.views_wallet import settle_flutterwave_topup

        now = timezone.now()
        rows = Transaction.objects.filter(
            type='top_up', status='pending', reference__startswith=flutterwave.PREFIX,
            created_at__lte=now - timedelta(minutes=after)).order_by('created_at')
        counts = {'credited': 0, 'failed': 0, 'cancelled': 0, 'waiting': 0, 'unreachable': 0}
        for row in rows:
            if dry_run:
                self.stdout.write('would check %s (%s)' % (row.reference, row.created_at))
                continue
            code, txn, _ = settle_flutterwave_topup(row.reference)
            if code == 'credited':
                counts['credited'] += 1
            elif code == 'unreachable':
                counts['unreachable'] += 1
            elif txn is not None and txn.status == 'failed':
                counts['failed'] += 1
            elif row.created_at <= now - timedelta(minutes=close_after):
                # Only a row still pending: never touch one that settled in
                # between (the update is conditional on the status).
                done = Transaction.objects.filter(pk=row.pk, status='pending').update(status='cancelled')
                counts['cancelled' if done else 'waiting'] += 1
            else:
                counts['waiting'] += 1
        self.stdout.write('pending Flutterwave top-ups: ' + ', '.join(
            '%s %d' % (k, v) for k, v in counts.items()))
