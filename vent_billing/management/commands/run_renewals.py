"""Charge every subscription that is due, once.

    python manage.py run_renewals
    python manage.py run_renewals --now 2026-10-01T09:00:00+00:00
    python manage.py run_renewals --dry-run

Run it as often as you like. Running it twice in the same period charges
nobody twice, and that is proven by a test rather than asserted here: see
`tests_renewal.py::test_second_run_charges_nobody`.

`--now` exists so the clock can be driven in a test and so an operator can
replay a day the box was off. `--dry-run` lists what would happen and touches
nothing, which is what somebody wants at 6am the first time they run it against
real subscribers.

Deliberately NOT a Celery task. Celery is installed here and has never had a
task defined, so a beat schedule would be the first thing depending on a worker
nobody has run. A cron entry calling this command is one moving part, it leaves
its output where an operator can read it, and it can be run by hand the first
time somebody needs to know whether it works.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_billing import clock, lifecycle


class Command(BaseCommand):
    help = 'Charge every subscription whose period or dunning retry is due.'

    def add_arguments(self, parser):
        parser.add_argument('--now', default=None,
                            help='ISO datetime to treat as now.')
        parser.add_argument('--dry-run', action='store_true',
                            help='List what is due and change nothing.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Stop after this many subscriptions.')

    def handle(self, *args, **options):
        # The switch guards the scheduler too. Gating only the endpoints would
        # leave the one path that moves money on its own still running, which
        # is the half of "billing is off" that actually costs somebody.
        from vent_billing import switch
        if not switch.billing_is_on():
            self.stdout.write('BILLING_ENABLED is off. Nothing was charged.')
            return

        at = timezone.now()
        if options.get('now'):
            at = clock.parse_moment(options['now'])
            if timezone.is_naive(at):
                at = timezone.make_aware(at, timezone.get_default_timezone())

        rows = lifecycle.due(at)
        if options.get('limit'):
            rows = rows[:options['limit']]
        rows = list(rows)

        if options.get('dry_run'):
            self.stdout.write('%d due at %s' % (len(rows), at.isoformat()))
            for sub in rows:
                self.stdout.write('  %s %s %s until %s' % (
                    sub.token, sub.state, sub.plan.name,
                    sub.period_end.isoformat()))
            return

        tally = {}
        for sub in rows:
            verb = lifecycle.process(sub, at=at)
            tally[verb] = tally.get(verb, 0) + 1

        if not tally:
            self.stdout.write('nothing due at %s' % at.isoformat())
            return
        self.stdout.write(', '.join('%s %d' % (verb, n)
                                    for verb, n in sorted(tally.items())))
