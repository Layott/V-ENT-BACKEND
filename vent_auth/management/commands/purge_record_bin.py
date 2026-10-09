"""Throw away bin entries older than 90 days (inbox 420).

Run nightly on the VPS, beside expire_premium:

    15 4 * * * cd /srv/vent/backend && ./venv/bin/python manage.py purge_record_bin

A restored entry is kept as a record of what happened until its date passes too.
Without this the bin is a promise ("90 days") that nothing keeps.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_auth.models import RecordBin


class Command(BaseCommand):
    help = 'Delete record bin entries whose 90 days are over.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would go, and delete nothing.')

    def handle(self, *args, **options):
        due = RecordBin.objects.filter(purge_after__lte=timezone.now())
        count = due.count()
        if options['dry_run']:
            self.stdout.write('%d bin entries are past their 90 days.' % count)
            return
        due.delete()
        self.stdout.write('Purged %d bin entries past their 90 days.' % count)
