"""Make the blueprint editions that have come due (P7). Run by cron.

    */10 * * * * cd /srv/vent/backend && ./venv/bin/python manage.py make_due_editions >> /var/log/vent/editions.log 2>&1

The same shape as send_due_reminders and for the same reasons: no scheduler
process runs on this deployment, and an ordinary function is one a test can
drive by moving a clock. It is safe to run twice: the blueprint row is locked
while an edition is made and the edition number is unique per blueprint, so
two overlapping runs make it once.

`--dry-run` says which editions would be made and changes nothing, because
the first question anybody asks about a scheduler is what it is about to do.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_tournament import blueprints


class Command(BaseCommand):
    help = 'Make every blueprint edition whose time has come.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would be made and change nothing.')

    def handle(self, *args, **options):
        now = timezone.now()
        if options['dry_run']:
            due = blueprints.due(now)
            if not due:
                self.stdout.write('Nothing due.')
            # Two due from one blueprint are #n and #n+1, not #n twice.
            ahead = {}
            for bp, start in due:
                number = bp.next_number + ahead.get(bp.pk, 0)
                ahead[bp.pk] = ahead.get(bp.pk, 0) + 1
                self.stdout.write('Would make %s #%d (%s), starting %s'
                                  % (bp.name, number, bp.token, start.isoformat()))
            return
        results = blueprints.run_due(now)
        if not results:
            self.stdout.write('Nothing due.')
        for bp, edition, code in results:
            if edition is not None:
                self.stdout.write('Made %s (%s) from %s'
                                  % (edition.tournament_title, edition.slug, bp.token))
            else:
                self.stdout.write('Did not make an edition of %s (%s): %s' % (bp.name, bp.token, code))
