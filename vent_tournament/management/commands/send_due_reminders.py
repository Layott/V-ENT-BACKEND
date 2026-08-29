"""Send the reminders that have come due. Run by cron every few minutes.

    */5 * * * * cd /srv/vent/backend && ./venv/bin/python manage.py send_due_reminders

There is no scheduler process on this deployment: Celery is installed and no
task has ever been defined. A management command under cron is deliberately
less machinery than a broker plus a worker, and it has two properties that
matter more than elegance here.

**It is safe to run twice.** `sent_at` is what decides, and it is written
inside the same transaction that claims the row with `select_for_update`. Two
overlapping cron runs cannot both send the same reminder.

**It is an ordinary function.** The whole thing can be unit tested by moving a
clock, which is not true of anything that needs a broker running.

`--dry-run` prints what would go out and writes nothing, because the first
question anybody asks about a scheduler is what it is about to do.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from vent_tournament.models import ScheduledReminder
from vent_tournament.views_reminders import ReminderRefused, deliver


class Command(BaseCommand):
    help = 'Send any scheduled tournament reminders that have come due.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Say what would be sent and change nothing.')

    def handle(self, *args, **options):
        now = timezone.now()
        dry = options['dry_run']

        # Filtered in Python rather than SQL: `due_at` is computed from the
        # tournament's own times, so it cannot be a WHERE clause without
        # duplicating the rule in two places. The candidate set is small - only
        # unsent, uncancelled rows ever reach it.
        pending = (ScheduledReminder.objects
                   .filter(sent_at__isnull=True, cancelled_at__isnull=True)
                   .select_related('tournament', 'tournament__tournament_game'))

        due = [row for row in pending if row.is_due(now)]
        if not due:
            self.stdout.write('Nothing due.')
            return

        sent = skipped = 0
        for row in due:
            if dry:
                self.stdout.write('Would send #%d: %s for %s'
                                  % (row.pk, row.kind,
                                     row.tournament.tournament_title))
                continue

            with transaction.atomic():
                # Claimed under a lock and re-checked. Two overlapping cron
                # runs would otherwise both pass the check above and send the
                # same reminder twice.
                locked = (ScheduledReminder.objects
                          .select_for_update()
                          .select_related('tournament')
                          .get(pk=row.pk))
                if locked.sent_at or locked.cancelled_at:
                    continue

                try:
                    result = deliver(locked.tournament, locked.kind,
                                     subject=locked.subject, body=locked.body)
                except ReminderRefused as refusal:
                    # Recorded on the row, not logged and forgotten. An
                    # organiser whose reminder was skipped is owed the reason
                    # on the screen where they scheduled it.
                    locked.skipped_reason = refusal.message[:200]
                    locked.sent_at = timezone.now()
                    locked.save(update_fields=['skipped_reason', 'sent_at'])
                    skipped += 1
                    continue

                locked.people_reached = result['people']
                locked.sent_at = timezone.now()
                locked.save(update_fields=['people_reached', 'sent_at'])
                sent += 1

        if dry:
            self.stdout.write('%d due.' % len(due))
        else:
            self.stdout.write('Sent %d, skipped %d.' % (sent, skipped))
