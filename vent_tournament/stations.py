"""Stations and waves, and the clash check (P4b part 4, design section 12).

"Stations and waves; a conflict check for players entered in linked
tournaments at the same time." A station is where a match is played ("PC 3",
"Table 2"), a wave the slot it belongs to ("Wave A"); both are labels staff set
on the match from the console.

A clash is one of two things inside one break of the match's start (the
tournament's match_interval_minutes, the same break the automatic times use):

  * a person due at another open match, in this tournament or in any
    tournament joined to it by a feed, either way, however far along the chain;
  * the same station holding another open match (compared without case, so
    "PC 3" and "pc 3" are one machine).

The place door and the time door both refuse a clash with MATCH_CLASH and the
list below unless the organiser says to keep it. A match with no time has
nothing to clash with yet; a finished one is never in the way.
"""
from datetime import timedelta

from django.db.models import Q

from . import options as tournament_options
from .models import BracketMatch, Progression

OPEN = ('scheduled', 'in_progress')
# A chain of feeds longer than this is not a real event, and the walk must end.
LINKED_AT_MOST = 50


def linked_tournament_ids(tournament_id):
    """This tournament and every one joined to it by feeds, in either direction."""
    seen, todo = {tournament_id}, [tournament_id]
    while todo and len(seen) < LINKED_AT_MOST:
        rows = (Progression.objects
                .filter(Q(source__tournament_id__in=todo) | Q(target__tournament_id__in=todo))
                .values_list('source__tournament_id', 'target__tournament_id'))
        todo = []
        for pair in rows:
            for other in pair:
                if other is not None and other not in seen:
                    seen.add(other)
                    todo.append(other)
    return seen


def _people(match):
    """{user_id: username} of everybody due at a match: every member of a club
    or squad, as reminders reach them (TournamentRegistration.people)."""
    out = {}
    for side in (match.participant_1, match.participant_2):
        if side is not None:
            for user in side.people:
                out[user.user_id] = user.username
    return out


def clashes(match, when=None, station=None):
    """What `match` clashes with if it starts at `when` on `station`.

    Either left out means the match's own. Each row names the other match, its
    tournament, its time and station, and what clashes: kind 'station', or kind
    'person' with that person's username (one row per person).
    """
    when = when or match.scheduled_at
    station = (match.station if station is None else station).strip().lower()
    if when is None:
        return []
    minutes = tournament_options.clean(match.tournament.options)['match_interval_minutes']
    window = timedelta(minutes=minutes)
    others = (BracketMatch.objects
              .filter(tournament_id__in=linked_tournament_ids(match.tournament_id),
                      tournament__deleted_at__isnull=True,
                      status__in=OPEN,
                      scheduled_at__gt=when - window,
                      scheduled_at__lt=when + window)
              .exclude(pk=match.pk)
              .select_related('tournament', 'participant_1', 'participant_2')
              .order_by('scheduled_at', 'id'))
    mine = _people(match)
    out = []
    for other in others:
        row = {
            'match_id': other.id,
            'scheduled_at': other.scheduled_at,
            'station': other.station,
            'wave': other.wave,
            'tournament': {'slug': other.tournament.slug,
                           'title': other.tournament.tournament_title},
        }
        if station and other.station.strip().lower() == station:
            out.append({**row, 'kind': 'station', 'person': ''})
        theirs = _people(other)
        for user_id in sorted(set(mine) & set(theirs)):
            out.append({**row, 'kind': 'person', 'person': mine[user_id]})
    return out
