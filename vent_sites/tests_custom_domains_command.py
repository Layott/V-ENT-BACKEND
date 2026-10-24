"""The list the server reads to issue certificates (P8c): active names only,
and nothing that is not a plain hostname."""
from io import StringIO

from django.core.management import call_command

from vent_sites.tests_domains import DomainBase


class CommandTests(DomainBase):
    def listed(self):
        out = StringIO()
        call_command('custom_domains', stdout=out)
        return out.getvalue().split()

    def test_only_active_plain_hostnames_are_listed(self):
        self.made('play.theirsite.com', state='active')
        self.made('later.theirsite.com', state='pending')
        self.made('gone.theirsite.com', state='failed')
        self.made('bad;name.theirsite.com', state='active')
        self.assertEqual(self.listed(), ['play.theirsite.com'])

    def test_a_name_of_ours_is_never_listed(self):
        self.made('tournaments.v-ent.co', state='active')
        self.assertEqual(self.listed(), [])
