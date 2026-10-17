"""A circuit's ranking, worked out in one place (P5).

The points engine (design section 13; start.gg leagues, S7): each tournament
in the circuit gives its entrants the points their final place is worth in the
tournament's tier table (the circuit's default table when it has no tier, or
the tier has none), adjustments are added, and the list is ranked by
`ranking.order`, the one function every table on the site is ranked by.

What counts, read live so a corrected result corrects the ranking:

- a tournament of the circuit's game that has final places
  (`TournamentRegistration.final_position`), not cancelled, not deleted;
- every entrant placed in it except one who withdrew or was disqualified.

Shared places split equally (tables.shared): two entrants level on 3rd took
3rd and 4th between them and each gets the mean, as the prize split does.

Who earns them:

- a team circuit counts clubs. A squad put together for one tournament is not
  a club and earns no club points, the rule the rankings page already keeps;
- a player circuit counts each person: a lone player is themselves, and a team
  or squad entry gives every player on it the place's points. Who that was is
  frozen on the circuit's link the first time it counts the tournament, so a
  player who leaves the club keeps what they earned and one who joins later
  earns nothing from before they came.

Rows rank on points, then the best single place, then the number of events
won, and every row says what put it below the row above (`decided_by`).
"""
from collections import Counter
from decimal import Decimal

from vent_tournament import ranking

from . import tables

#: Registrations whose place earns nothing.
NOT_COUNTED = ('withdrawn', 'disqualified')
#: After points: the best single place, then events won.
TIEBREAKERS = ['best_place', 'wins']
_UNPLACED = 10 ** 6


def counted_links(circuit):
    """The links whose tournaments can give points, earliest event first."""
    links = (circuit.links.select_related('tournament', 'tier')
             .filter(tournament__deleted_at__isnull=True)
             .exclude(tournament__status='cancelled')
             .order_by('tournament__start_date_and_time', 'id'))
    if circuit.game_id:
        links = links.filter(tournament__tournament_game_id=circuit.game_id)
    return list(links)


def table_for(circuit, link):
    """The point table a link's tournament scores by."""
    if link.tier is not None and link.tier.table:
        return link.tier.table
    return circuit.default_table or {}


def _placed(link):
    from vent_tournament.models import TournamentRegistration
    return list(TournamentRegistration.objects
                .filter(tournament_id=link.tournament_id, final_position__isnull=False)
                .exclude(status__in=NOT_COUNTED)
                .select_related('user', 'team', 'squad'))


def _entrants(circuit, link, reg, frozen):
    """[(kind, id)] that `reg`'s place counts for in this circuit. Writes a
    team or squad's players into `frozen` the first time it is counted."""
    if circuit.entrant == 'team':
        return [('team', reg.team_id)] if reg.team_id and not reg.squad_id else []
    if reg.user_id and not reg.team_id and not reg.squad_id:
        return [('user', reg.user_id)]
    if not (reg.team_id or reg.squad_id):
        return []
    key = str(reg.id)
    if key not in link.roster:
        frozen[key] = sorted(p.user_id for p in reg.people)
    return [('user', uid) for uid in frozen.get(key, link.roster.get(key, []))]


def _blank(kind, ident):
    return {'entrant': {'type': kind, 'id': ident, 'name': None, 'handle': None, 'slug': None,
                        'avatar_path': None},
            'points': Decimal('0'), 'events': [], 'adjustments': [],
            'played': 0, 'best_place': None, 'wins': 0}


def _name_rows(rows):
    """Fill in who each entrant is: a person by their full name with the
    @username beside it (match_shape.names' rule), a club by its name."""
    from vent_auth.models import Teams, UserProfile, Users
    users = [r for r in rows if r['entrant']['type'] == 'user']
    teams = [r for r in rows if r['entrant']['type'] == 'team']
    if users:
        ids = [r['entrant']['id'] for r in users]
        people = {u.user_id: u for u in Users.objects.filter(user_id__in=ids)}
        pictures = {}
        for profile in UserProfile.objects.filter(user_id__in=ids).exclude(profile_picture=''):
            if profile.profile_picture and profile.user_id not in pictures:
                pictures[profile.user_id] = profile.profile_picture.url
        for r in users:
            user = people.get(r['entrant']['id'])
            if user is None:
                continue
            r['entrant'].update(name=user.full_name or user.username, handle=user.username,
                                slug=user.username, avatar_path=pictures.get(user.user_id))
    if teams:
        clubs = {t.team_id: t for t in Teams.objects.filter(team_id__in=[r['entrant']['id'] for r in teams])}
        for r in teams:
            team = clubs.get(r['entrant']['id'])
            if team is None:
                continue
            r['entrant'].update(name=team.team_name, slug=team.slug,
                                avatar_path=team.team_logo.url if team.team_logo else None)
    for r in rows:
        # ranking.order breaks a full tie on the name, so it must be there.
        r['name'] = r['entrant']['name'] or ''


def _value(row, key):
    if key == 'best_place':
        return -(row['best_place'] or _UNPLACED)
    if key == 'wins':
        return row['wins']
    return None


def points_table(circuit):
    """The circuit's ranking by points, best first. Each row:
    {entrant {type, id, name, handle, slug, avatar_path}, points, events
    [{tournament {slug, title}, tier, place, shared_with, points}],
    adjustments [{points, reason}], played, best_place, wins, rank, position,
    tied, decided_by}."""
    rows = {}

    def row(kind, ident):
        if (kind, ident) not in rows:
            rows[(kind, ident)] = _blank(kind, ident)
        return rows[(kind, ident)]

    for link in counted_links(circuit):
        placed = _placed(link)
        if not placed:
            continue
        table = table_for(circuit, link)
        sharing = Counter(reg.final_position for reg in placed)
        frozen = {}
        for reg in placed:
            place = reg.final_position
            worth = tables.shared(table, place, sharing[place])
            for kind, ident in _entrants(circuit, link, reg, frozen):
                r = row(kind, ident)
                r['points'] += worth
                r['played'] += 1
                r['wins'] += 1 if place == 1 else 0
                r['best_place'] = place if r['best_place'] is None else min(r['best_place'], place)
                r['events'].append({
                    'tournament': {'slug': link.tournament.slug, 'title': link.tournament.tournament_title},
                    'tier': link.tier.name if link.tier is not None else None,
                    'place': place, 'shared_with': sharing[place] - 1, 'points': worth})
        if frozen:
            link.roster = dict(link.roster or {}, **frozen)
            link.save(update_fields=['roster'])

    kind_wanted = 'team' if circuit.entrant == 'team' else 'user'
    for adj in circuit.adjustments.all().order_by('at', 'id'):
        kind, ident = ('team', adj.team_id) if adj.team_id else ('user', adj.user_id)
        if kind != kind_wanted or not ident:
            continue
        r = row(kind, ident)
        r['points'] += adj.points
        r['adjustments'].append({'points': adj.points, 'reason': adj.reason})

    out = list(rows.values())
    _name_rows(out)
    return ranking.order(out, TIEBREAKERS, value=_value)
