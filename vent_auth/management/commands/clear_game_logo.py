"""Take a game's artwork off the site.

CEO, 8 September 2026: "no Freefire logo anywhere in the site, I don't like
it."

One column, read by every screen that draws a game, so clearing it here removes
it everywhere at once rather than a screen at a time. The FILE is left on disk:
the CEO asked for it not to be shown, and deleting artwork somebody may want
back is a different and irreversible decision.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Clear a game's logo so nothing on the site draws it."

    def add_arguments(self, parser):
        parser.add_argument('title', help='The game title, e.g. "Free Fire".')
        parser.add_argument('--write', action='store_true')

    def handle(self, *args, **options):
        from vent_auth.models import Games

        games = Games.objects.filter(game_title__iexact=options['title'])
        if not games:
            self.stdout.write('No game called %r.' % options['title'])
            return

        for game in games:
            if not game.logo:
                self.stdout.write('%s already has no logo.' % game.game_title)
                continue
            self.stdout.write('%s -> clearing %s' % (game.game_title, game.logo.name))
            if options['write']:
                game.logo = ''
                game.save(update_fields=['logo'])

        if not options['write']:
            self.stdout.write('Nothing was saved. Add --write to save.')
