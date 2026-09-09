"""Turn premium off where the paid period has ended.

Run nightly. Without it a purchase is a one-way switch: somebody pays for a
month and keeps the features for ever, which is the same as giving it away with
an extra step.

    python manage.py expire_premium            # do it
    python manage.py expire_premium --dry-run  # say what it would do

A grant is untouched, always. `premium_until` is NULL on anything an admin
turned on, and this only ever looks at rows with a date in the past, so there is
no path here that can end an open-ended grant.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_auth import premium_sale
from vent_auth.models import Organization, Users


class Command(BaseCommand):
    help = 'Turn premium off where the paid period has ended.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List what would be turned off and stop.')

    def handle(self, *args, **options):
        now = timezone.now()

        due_users = list(Users.objects.filter(is_premium=True,
                                              premium_until__lte=now))
        due_orgs = list(Organization.objects.filter(is_premium=True,
                                                    premium_until__lte=now))

        for row in due_users:
            self.stdout.write('user %s ran out %s' % (
                row.username, row.premium_until.isoformat()))
        for row in due_orgs:
            self.stdout.write('org %s ran out %s' % (
                row.org_name, row.premium_until.isoformat()))

        if options.get('dry_run'):
            self.stdout.write(self.style.WARNING(
                'dry run: %s user(s) and %s organisation(s) would be turned off'
                % (len(due_users), len(due_orgs))))
            return

        counts = premium_sale.expire_due(now=now)
        self.stdout.write(self.style.SUCCESS(
            'premium turned off for %s user(s) and %s organisation(s)'
            % (counts['users'], counts['orgs'])))
