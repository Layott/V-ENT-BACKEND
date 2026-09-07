"""Tell Discord which slash commands exist.

    python manage.py discord_register_commands            # everywhere
    python manage.py discord_register_commands --guild ID # one server, instantly
    python manage.py discord_register_commands --list     # what is registered

Registering is a PUT of the whole set, so this file is the only description of
what the bot offers and running it makes Discord match it exactly. A command
removed from `COMMANDS` disappears on the next run rather than lingering.

**Global commands take up to an hour to appear.** Guild commands appear at
once, which is why `--guild` exists: it is the difference between testing a
change now and testing it after lunch.
"""
import os

import requests
from django.core.management.base import BaseCommand

from vent_auth.discord import API, bot_token
from vent_auth.discord_commands import COMMANDS


class Command(BaseCommand):
    help = 'Register the V-ENT slash commands with Discord.'

    def add_arguments(self, parser):
        parser.add_argument('--guild', default='',
                            help='Register into one server, which is instant.')
        parser.add_argument('--list', action='store_true',
                            help='Show what Discord currently has.')

    def handle(self, *args, **options):
        token = bot_token()
        app_id = os.environ.get('DISCORD_CLIENT_ID', '')
        if not (token and app_id):
            self.stderr.write('DISCORD_BOT_TOKEN and DISCORD_CLIENT_ID must '
                              'both be set.')
            return

        guild = options['guild']
        path = (f'/applications/{app_id}/guilds/{guild}/commands' if guild
                else f'/applications/{app_id}/commands')
        headers = {'Authorization': f'Bot {token}'}

        if options['list']:
            res = requests.get(f'{API}{path}', headers=headers, timeout=20)
            self.stdout.write(f'HTTP {res.status_code}')
            for row in (res.json() if res.status_code == 200 else []):
                self.stdout.write(f'  /{row.get("name")}  {row.get("description")}')
            return

        res = requests.put(f'{API}{path}', json=COMMANDS, headers=headers,
                           timeout=20)
        if res.status_code not in (200, 201):
            self.stderr.write(f'Discord refused: {res.status_code} '
                              f'{res.text[:400]}')
            return

        where = f'server {guild}' if guild else 'everywhere (up to an hour)'
        self.stdout.write(self.style.SUCCESS(
            f'Registered {len(res.json())} command(s) for {where}.'))
        for row in res.json():
            for sub in (row.get('options') or []):
                self.stdout.write(f'  /{row["name"]} {sub.get("name")}  '
                                  f'{sub.get("description")}')
