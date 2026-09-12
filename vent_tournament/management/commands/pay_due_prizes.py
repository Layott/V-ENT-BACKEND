"""Pay the prizes that are due, and warn about the ones that soon will be.

Run from cron on the VPS, beside the nightly backup:

    */15 * * * * cd /srv/vent/backend && venv/bin/python manage.py pay_due_prizes

Two passes, in this order, because the order is the promise: nothing is ever
paid that was not warned about first, unless the organiser asked for zero hours
of notice.

Why a command and not Celery: Celery is installed here and has never had a task
defined, and the backup that matters most on this box is a cron line. A cron
line is visible in `crontab -l`, survives a restart, and fails loudly in a log
somebody already reads. Adding a first Celery worker to pay real money is a
larger promise than this needs.

`--dry-run` prints what it would do and writes nothing, which is what to run the
first time it goes on a box.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_auth.views_notifications import create_notification
from vent_tournament.models import PrizeSchedule
from vent_tournament.services import prizes as prize_service


class Command(BaseCommand):
    help = 'Pay scheduled prize distributions that are due, and warn ahead of them.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would happen and change nothing.')

    def handle(self, *args, **options):
        dry = options['dry_run']
        now = timezone.now()

        warned = self._warn(now, dry)
        paid, failed = self._pay(now, dry)

        self.stdout.write('%s%s warned, %s paid, %s failed'
                          % ('would be: ' if dry else '', warned, paid, failed))

    # ------------------------------------------------------------- warning
    def _warn(self, now, dry):
        """Tell the organiser before the money moves, never after."""
        count = 0
        due = PrizeSchedule.objects.filter(
            state='scheduled', warned_at__isnull=True
        ).select_related('tournament', 'created_by')

        for schedule in due:
            if schedule.warn_at() > now:
                continue
            tournament = schedule.tournament
            plan = prize_service.plan(tournament)
            self.stdout.write('warn: %s (%s coins to %s)'
                              % (tournament.tournament_title, plan['total'],
                                 len([r for r in plan['rows'] if not r['problem']])))
            if dry:
                count += 1
                continue

            create_notification(
                tournament.tournament_creator_id,
                'prizes',
                'Prizes for %s are about to be paid' % tournament.tournament_title,
                body=('%s VENT COINS go out to %s winners at the time you set. '
                      'Open the tournament to check the list or call it off.'
                      % (plan['total'],
                         len([r for r in plan['rows'] if not r['problem']]))),
                link='/tournaments/%s' % (tournament.slug or tournament.pk),
                metadata={'tournament_id': tournament.pk,
                          'run_at': schedule.run_at.isoformat(),
                          'total': plan['total']},
            )
            schedule.warned_at = now
            schedule.state = 'warned'
            schedule.save(update_fields=['warned_at', 'state'])
            count += 1
        return count

    # ------------------------------------------------------------- paying
    def _pay(self, now, dry):
        paid = 0
        failed = 0
        due = PrizeSchedule.objects.filter(
            state__in=('scheduled', 'warned'), run_at__lte=now
        ).select_related('tournament')

        for schedule in due:
            tournament = schedule.tournament
            if dry:
                self.stdout.write('pay: %s' % tournament.tournament_title)
                paid += 1
                continue

            try:
                results = prize_service.distribute(
                    tournament, triggered_by=schedule.created_by, auto=True)
            except prize_service.PrizeError as exc:
                # A tournament that is not finished, or has no winners resolved,
                # is not an error to retry for ever. It is recorded and left for
                # the organiser, who is the only one who can fix it.
                schedule.state = 'failed'
                schedule.problem = exc.code[:80]
                schedule.ran_at = now
                schedule.save(update_fields=['state', 'problem', 'ran_at'])
                create_notification(
                    tournament.tournament_creator_id, 'prizes',
                    'Prizes for %s could not be paid' % tournament.tournament_title,
                    body=exc.message,
                    link='/tournaments/%s' % (tournament.slug or tournament.pk),
                    metadata={'tournament_id': tournament.pk, 'problem': exc.code},
                )
                self.stderr.write('failed: %s (%s)'
                                  % (tournament.tournament_title, exc.code))
                failed += 1
                continue

            schedule.state = 'paid'
            schedule.ran_at = now
            schedule.save(update_fields=['state', 'ran_at'])
            create_notification(
                tournament.tournament_creator_id, 'prizes',
                'Prizes for %s have been paid' % tournament.tournament_title,
                body='%s prize positions were paid out.' % len(results),
                link='/tournaments/%s' % (tournament.slug or tournament.pk),
                metadata={'tournament_id': tournament.pk,
                          'positions': len(results)},
            )
            self.stdout.write('paid: %s (%s positions)'
                              % (tournament.tournament_title, len(results)))
            paid += 1
        return paid, failed
