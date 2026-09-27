"""Settle matches whose check-in clock has run out. Run by cron every minute.

    * * * * * cd /srv/vent/backend && ./venv/bin/python manage.py settle_no_shows

A side that checked in beats one that did not, 0-3, and is sent on. Neither
checked in is left alone for the organiser. The public bracket and the match
page also settle a stale match when somebody looks, so this is what keeps a
bracket nobody is watching honest, not the only thing that does.

Safe to run twice: each match is claimed with `select_for_update` and settled
only while it is still scheduled.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_tournament.models import BracketMatch
from vent_tournament import stage_engine


class Command(BaseCommand):
    help = 'Forfeit matches where one side checked in and the other never did.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would be settled and change nothing.')

    def handle(self, *args, **options):
        now = timezone.now()
        due = BracketMatch.objects.filter(status='scheduled',
                                          check_in_deadline__lte=now)
        if options['dry_run']:
            for m in due:
                one_side = (m.checked_in_p1_at is None) != (m.checked_in_p2_at is None)
                self.stdout.write('match %s: %s' % (
                    m.pk, 'would forfeit' if one_side else 'left for the organiser'))
            return
        settled = stage_engine.sweep_no_shows(now=now)
        self.stdout.write('%s settled at %s' % (settled, now.isoformat()))
