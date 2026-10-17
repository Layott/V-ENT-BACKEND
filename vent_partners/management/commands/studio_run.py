"""Send what is owed to game studios. Run by cron every minute.

    * * * * * cd /srv/vent/backend && ./venv/bin/python manage.py studio_run >> /srv/vent/logs/studio.log 2>&1

One pass does three things:

  1. sends every delivery that is due (`studio_delivery.run_due`): first tries
     the request thread could not make and every retry on its schedule;
  2. game sessions for matches that are ready, when the studio sessions module
     is installed (P6d);
  3. clears what is finished and old: deliveries past 30 days.

Safe to run twice at once: a delivery is claimed with a lease before it is
sent, so two passes never send the same row. `--dry-run` says what is due and
changes nothing, because the first question about a scheduler is what it is
about to do.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_partners import studio_delivery
from vent_partners.models import StudioDelivery


class Command(BaseCommand):
    help = 'Send due deliveries to game studios and clear old ones.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what is due and change nothing.')

    def handle(self, *args, **options):
        now = timezone.now()
        if options['dry_run']:
            due = StudioDelivery.objects.filter(status='pending', next_attempt_at__lte=now).count()
            self.stdout.write('%d due' % due)
            return

        delivered = studio_delivery.run_due(now=now)
        lines = ['%d delivered' % delivered]

        try:
            from vent_partners import studio_sessions
        except ImportError:
            studio_sessions = None
        if studio_sessions is not None:
            summary = studio_sessions.sweep(now=now)
            lines.append('sessions %s' % ', '.join('%s %s' % (k, v) for k, v in summary.items()))

        lines.append('%d cleared' % studio_delivery.clear_old(now=now))
        if studio_sessions is not None:
            lines.append('%d request ids cleared' % studio_sessions.clear_nonces(now=now))
        from datetime import timedelta
        from vent_partners.models import StudioLinkCode
        old_codes, _ = StudioLinkCode.objects.filter(
            claimed_at__isnull=True, expires_at__lt=now - timedelta(days=1)).delete()
        lines.append('%d link codes cleared' % old_codes)
        self.stdout.write('; '.join(lines))
