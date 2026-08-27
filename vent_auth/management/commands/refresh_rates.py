"""Refresh the exchange rates from the published feed.

Run from cron once a day. The feed updates daily, so anything more often is
traffic for nothing.

    0 4 * * *  cd /srv/vent/backend && ./venv/bin/python manage.py refresh_rates

It exits 0 even when the feed is unreachable, because a missed refresh is not a
failure worth waking anybody for: the previous rates stay in place and prices
stay readable. It says what happened either way.
"""
from django.core.management.base import BaseCommand

from vent_auth.rates import refresh_rates


class Command(BaseCommand):
    help = 'Update currency rates from the published feed.'

    def add_arguments(self, parser):
        parser.add_argument('--quiet', action='store_true',
                            help='Only say something when it goes wrong.')

    def handle(self, *args, **options):
        updated, skipped, error = refresh_rates()

        if error:
            self.stderr.write(self.style.WARNING(
                'Rates not refreshed: %s. The previous rates are still in place.' % error))
            return

        if not options['quiet']:
            self.stdout.write(self.style.SUCCESS('Updated %d rates.' % updated))
        for note in skipped:
            self.stdout.write('  skipped %s' % note)
