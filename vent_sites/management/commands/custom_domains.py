"""Print the organisers' own addresses that are live, one per line (P8c).

Read by deploy/custom-domains.sh on the box, which gets a certificate for each
new one and writes its nginx block. Only `active` names, and only names made of
the characters a hostname may hold, so nothing else can reach the server's
configuration through this list.
"""
import re

from django.core.management.base import BaseCommand

from vent_sites.models import CustomDomain

HOSTNAME = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')


class Command(BaseCommand):
    help = "List the active custom domains, one per line."

    def handle(self, *args, **options):
        names = (CustomDomain.objects.filter(state='active')
                 .order_by('hostname').values_list('hostname', flat=True))
        for name in names:
            name = name.strip().lower()
            if HOSTNAME.match(name) and name != 'v-ent.co' and not name.endswith('.v-ent.co'):
                self.stdout.write(name)
