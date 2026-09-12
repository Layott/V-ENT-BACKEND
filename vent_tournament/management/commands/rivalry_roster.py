"""Fill a tournament's sides and seats from a roster the organiser writes out.

CEO, 4 September 2026, two hours before the Rivalry Series Season 2 broadcast:
"start working on the rivalry series tournament, add in the players and teams",
and then, on how: "You can't like code it in? If I give you all the info from
here? Not like hard coded but like just to fill in the slots?"

So: the roster is DATA, passed in, and this is the tool that applies it. Nothing
about Nigeria, Ghana or anybody's name is written into this file. Run it against
whichever database the tournament lives in.

    python manage.py rivalry_roster --tournament rivalry-series-season-2 \\
        --roster roster.txt --dry-run
    python manage.py rivalry_roster --tournament rivalry-series-season-2 \\
        --roster roster.txt

## The roster

Either JSON, or the plain text below, because a roster arrives in a message and
retyping it as JSON is a step at which a name gets mistyped:

    Nigeria NGA
      @tobi  Tobi Adeyemi
      @kunle Kunle Bakare

    Ghana GHA
      @kwame Kwame Mensah
      @yaw   Yaw Boateng

The first line of a block is the side: its name, then optionally its short tag,
which is what a scorebar shows. Each indented line is one player: a `@handle`, a
display name, or both.

**Order is seat order.** The first player listed sits seat 1 and is recorded as
the captain, because `_seat_players` in services/bracket.py reads members
captain first and then by when they were added. Seat 1 only ever plays seat 1,
which is the rule the whole format rests on, so the order in the file is not
cosmetic.

## What it will not do quietly

A handle that matches no account stops the run and is listed. Inventing an
account for a person is a decision, not a detail, so it takes `--create-missing`
and every account it makes is printed. An account made that way has no usable
password: it exists to be named on a graphic and to be claimed properly later.

Running it twice changes nothing the second time. It is written to be run again
after a name is corrected, which on the morning of a show it will be.
"""
import json
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from vent_auth.models import Users
from vent_tournament.models import (
    SquadMember, Tournament, TournamentRegistration, TournamentSquad,
)


def parse_roster(text):
    """The text or JSON above into `[{name, tag, players: [{handle, name}]}]`.

    Deliberately forgiving about what a player line looks like, because the
    roster is copied out of a message written by somebody who is not thinking
    about a parser.
    """
    stripped = text.strip()
    if stripped.startswith('{') or stripped.startswith('['):
        data = json.loads(stripped)
        squads = data['squads'] if isinstance(data, dict) else data
        out = []
        for squad in squads:
            players = []
            for player in squad.get('players') or []:
                if isinstance(player, str):
                    players.append(_read_player(player))
                else:
                    players.append({
                        'handle': str(player.get('username') or '').lstrip('@'),
                        'name': str(player.get('name') or ''),
                    })
            out.append({
                'name': str(squad.get('name') or '').strip(),
                'tag': str(squad.get('tag') or '').strip(),
                'players': players,
            })
        return out

    blocks = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        # An indented line, or one starting with @ or a number, is a player.
        indented = raw[:1].isspace() or raw.lstrip()[:1] in '@-*'
        numbered = bool(re.match(r'^\s*\d+[.)]\s', raw))
        line = raw.strip()
        if (indented or numbered) and blocks:
            line = re.sub(r'^\d+[.)]\s*', '', line).lstrip('-* ')
            blocks[-1]['players'].append(_read_player(line))
            continue
        # A side. "Nigeria NGA" or "Nigeria".
        parts = line.split()
        tag = ''
        if len(parts) > 1 and parts[-1].isupper() and len(parts[-1]) <= 6:
            tag = parts[-1]
            parts = parts[:-1]
        blocks.append({'name': ' '.join(parts), 'tag': tag, 'players': []})
    return blocks


def _read_player(line):
    """A player line into a handle and a display name, either of which may be
    absent. "@tobi Tobi Adeyemi", "@tobi", "Tobi Adeyemi"."""
    line = line.strip()
    match = re.match(r'^@([\w.\-]+)\s*(.*)$', line)
    if match:
        return {'handle': match.group(1), 'name': match.group(2).strip()}
    return {'handle': '', 'name': line}


class Command(BaseCommand):
    help = 'Fill a tournament\'s sides and seats from a roster file.'

    def add_arguments(self, parser):
        parser.add_argument('--tournament', required=True,
                            help='slug or id')
        parser.add_argument('--roster', required=True,
                            help='path to the roster file, or - for stdin')
        parser.add_argument('--dry-run', action='store_true',
                            help='say what would happen and change nothing')
        parser.add_argument('--create-missing', action='store_true',
                            help='make an account for a handle that has none')
        parser.add_argument('--generate-fixtures', action='store_true',
                            help='build the round robin once the sides are in')

    def handle(self, *args, **options):
        tournament = self._tournament(options['tournament'])
        squads = self._roster(options['roster'])
        if not squads:
            raise CommandError('That roster has no sides in it.')

        self.stdout.write('%s: %s' % (tournament.slug or tournament.pk,
                                      tournament.tournament_title))
        for squad in squads:
            self.stdout.write('  %s%s  %s player(s)' % (
                squad['name'],
                ' [%s]' % squad['tag'] if squad['tag'] else '',
                len(squad['players'])))

        people, missing = self._resolve(squads, options['create_missing'],
                                        options['dry_run'])
        if missing:
            self.stdout.write('')
            self.stdout.write('No account for:')
            for handle in missing:
                self.stdout.write('  @%s' % handle)
            raise CommandError(
                'Nothing was changed. Give these people accounts, correct the '
                'handles, or pass --create-missing to make them here.')

        if options['dry_run']:
            self.stdout.write('')
            self.stdout.write('Dry run. Nothing was written.')
            return

        with transaction.atomic():
            made = self._apply(tournament, squads, people)

        for line in made:
            self.stdout.write(line)

        if options['generate_fixtures']:
            # As many seats as the roster gives each side. A Rivalry fixture is
            # two, and the number belongs to the roster rather than to this file.
            seats = max((len(s['players']) for s in squads), default=2) or 2
            self._fixtures(tournament, seats)

    # ------------------------------------------------------------------ bits

    def _tournament(self, ref):
        found = None
        if str(ref).isdigit():
            found = Tournament.objects.filter(tournament_id=int(ref)).first()
        if found is None:
            found = Tournament.objects.filter(slug=str(ref)).first()
        if found is None:
            raise CommandError('No tournament with that slug or id.')
        return found

    def _roster(self, path):
        if path == '-':
            import sys
            return parse_roster(sys.stdin.read())
        with open(path, encoding='utf-8') as handle:
            return parse_roster(handle.read())

    def _resolve(self, squads, create_missing, dry_run):
        """Every player line to a `Users` row, or a list of what is missing."""
        people = {}
        missing = []
        for squad in squads:
            for player in squad['players']:
                key = (squad['name'], player['handle'], player['name'])
                user = None
                if player['handle']:
                    user = Users.objects.filter(
                        username__iexact=player['handle']).first()
                elif player['name']:
                    # A name with no handle: only accepted when exactly one
                    # account carries it. Two people called the same thing is
                    # the case this must not guess at.
                    matches = list(Users.objects.filter(
                        full_name__iexact=player['name'])[:2])
                    user = matches[0] if len(matches) == 1 else None

                if user is None:
                    handle = player['handle'] or _handle_from(player['name'])
                    if not create_missing:
                        missing.append(handle)
                        continue
                    if dry_run:
                        self.stdout.write('  would create @%s (%s)'
                                          % (handle, player['name'] or handle))
                        continue
                    user = self._make(handle, player['name'])
                    self.stdout.write('  created @%s' % user.username)
                people[key] = user
        return people, missing

    def _make(self, handle, name):
        """An account for somebody who is on a graphic tonight and has never
        signed in. No usable password: it exists to be named, and to be claimed
        properly later."""
        user = Users.objects.create(
            username=handle,
            full_name=name or handle,
            email='',
            is_active=True,
        )
        user.set_unusable_password()
        user.save(update_fields=['password'])
        return user

    def _apply(self, tournament, squads, people):
        out = []
        for squad in squads:
            row, made = TournamentSquad.objects.get_or_create(
                tournament=tournament, name=squad['name'],
                defaults={'tag': squad['tag']})
            if not made and squad['tag'] and row.tag != squad['tag']:
                row.tag = squad['tag']
                row.save(update_fields=['tag'])
            out.append('%s %s' % ('added ' if made else 'kept  ', row.name))

            # Seat order is the order in the file. Seat 1 is the captain,
            # because that is what `_seat_players` reads first.
            for seat, player in enumerate(squad['players'], start=1):
                user = people.get((squad['name'], player['handle'], player['name']))
                if user is None:
                    continue
                member, member_made = SquadMember.objects.get_or_create(
                    squad=row, user=user,
                    defaults={'is_captain': seat == 1,
                              'represents_name': player['name'] or ''})
                if not member_made and member.is_captain != (seat == 1):
                    member.is_captain = (seat == 1)
                    member.save(update_fields=['is_captain'])
                out.append('    seat %s  @%s%s' % (
                    seat, user.username, '  (added)' if member_made else ''))

            registration, reg_made = TournamentRegistration.objects.get_or_create(
                tournament=tournament, squad=row,
                defaults={'status': 'confirmed'})
            if not reg_made and registration.status != 'confirmed':
                registration.status = 'confirmed'
                registration.save(update_fields=['status'])
            out.append('    entered%s' % ('' if reg_made else ' already'))
        return out

    def _fixtures(self, tournament, seats=2):
        """The round robin, through the service that already builds one. The
        studio does no arithmetic and neither does this."""
        from vent_tournament.services import schedule
        made = schedule.build_league(tournament, players_per_team=seats)
        self.stdout.write('%s tie(s) scheduled.' % len(made))


def _handle_from(name):
    return re.sub(r'[^a-z0-9]+', '', (name or '').lower())[:20] or 'player'
