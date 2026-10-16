"""Find results that cannot be true. Run by deploy/verify-live.sh on every deploy.

    cd /srv/vent/backend && ./venv/bin/python manage.py check_results

Two shapes, both of which reached real data before the doors that wrote them
were guarded (inbox 488, 16 October 2026):

  * a winner who is not one of the two players: the admin score override took
    any registration id (27 August), and a round one result changed after the
    semi-final was played put the new winner in a seat the old one had won
    from (28 September);
  * a seat or a winner holding a registration from another tournament.

Exits 1 and names each match, so a deploy stops on it. Reads only.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q

from vent_tournament.models import BracketMatch


def problems():
    """[(match id, tournament id, what is wrong)]"""
    found = []
    not_a_player = BracketMatch.objects.filter(winner__isnull=False).exclude(
        Q(winner_id=F('participant_1_id')) | Q(winner_id=F('participant_2_id')))
    for pk, tid in not_a_player.values_list('id', 'tournament_id'):
        found.append((pk, tid, 'the winner is not one of the two players'))
    elsewhere = BracketMatch.objects.filter(
        Q(participant_1__isnull=False) & ~Q(participant_1__tournament_id=F('tournament_id'))
        | Q(participant_2__isnull=False) & ~Q(participant_2__tournament_id=F('tournament_id'))
        | Q(winner__isnull=False) & ~Q(winner__tournament_id=F('tournament_id')))
    for pk, tid in elsewhere.values_list('id', 'tournament_id'):
        found.append((pk, tid, 'it holds a registration from another tournament'))
    return sorted(set(found))


class Command(BaseCommand):
    help = 'Fail on a match whose result cannot be true.'

    def handle(self, *args, **options):
        found = problems()
        if found:
            raise CommandError('%d problems: %s' % (len(found), '; '.join(
                'match %s (tournament %s): %s' % row for row in found)))
        self.stdout.write('check_results: 0 problems in %d matches'
                          % BracketMatch.objects.count())
