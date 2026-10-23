"""Leagues of division circuits, and the end of a season (P5d).

Design section 13: "Promotion and relegation move entrants between division
circuits across seasons" (the league systems of the bracket catalogue, 3.4: a
season's final standings in division N place entrants in division N plus or
minus 1 for the next season, by rule).

A league is a ladder of circuits, one per division (1 is the top), season
after season. Each division is an ordinary circuit: its tournaments, its point
tables and its adjustments rank its members (standings.py). Ending a season
copies every division into the next season and moves:

- the top `up` of every division but the top one up a division;
- the bottom `down` of every division but the bottom one down a division,
  never somebody already going up;
- everybody else stays.

Nothing changes until the organiser presses the button: `plan` works the moves
out from the rankings as they stand, `start_next` makes them. Two entrants
still level on everything at a line are named in the plan, because the
ranking's last tie-breaker (the name) is what puts one of them over it.
"""
from django.db import transaction

from . import standings
from .models import Circuit, CircuitMember, CircuitTier


class LeagueError(Exception):
    def __init__(self, code, field=None, **extra):
        super().__init__(code)
        self.code = code
        self.field = field
        self.extra = extra


def latest_season(league):
    """The number of the league's newest season, or None before it has one."""
    return (league.divisions.exclude(league_season=None)
            .order_by('-league_season').values_list('league_season', flat=True).first())


def seasons(league):
    """[{number, season (its label), starts_on, ends_on}], oldest first."""
    out = {}
    for c in league.divisions.exclude(league_season=None).order_by('league_season', 'division'):
        out.setdefault(c.league_season, {'number': c.league_season, 'season': c.season,
                                          'starts_on': c.starts_on, 'ends_on': c.ends_on})
    return list(out.values())


def divisions(league, number=None):
    """A season's division circuits, top first (the newest season by default)."""
    number = latest_season(league) if number is None else number
    if number is None:
        return []
    return list(league.divisions.filter(league_season=number)
                .select_related('game', 'owner', 'organization').order_by('division', 'id'))


def fits(league, circuit):
    """The code refusing `circuit` as a division of `league`, or None."""
    if circuit.league_id and circuit.league_id != league.pk:
        return 'CIRCUIT_IN_ANOTHER_LEAGUE'
    if league.game_id and circuit.game_id != league.game_id:
        return 'CIRCUIT_GAME_MISMATCH'
    if circuit.entrant != league.entrant:
        return 'CIRCUIT_ENTRANT_MISMATCH'
    return None


def add_division(league, circuit):
    """Put `circuit` in as the bottom division of the newest season."""
    code = fits(league, circuit)
    if code:
        raise LeagueError(code, 'circuit')
    if circuit.league_id == league.pk:
        raise LeagueError('ALREADY_A_DIVISION', 'circuit')
    number = latest_season(league) or 1
    if Circuit.objects.filter(league=league, league_season=number + 1).exists():
        raise LeagueError('SEASON_OVER', 'circuit')
    bottom = len(divisions(league, number))
    circuit.league = league
    circuit.league_season = number
    circuit.division = bottom + 1
    circuit.save()
    return circuit


def reorder(league, slugs):
    """Number the newest season's divisions in the order given, top first."""
    current = {c.slug: c for c in divisions(league)}
    if sorted(slugs) != sorted(current):
        raise LeagueError('DIVISIONS_ORDER', 'order')
    for number, slug in enumerate(slugs, start=1):
        c = current[slug]
        if c.division != number:
            c.division = number
            c.save(update_fields=['division'])


def remove_division(league, circuit):
    """Take a division out of the league. Only from the newest season, and
    only while no season has followed it: an ended season is history."""
    if circuit.league_id != league.pk:
        raise LeagueError('NOT_A_DIVISION', 'circuit')
    if Circuit.objects.filter(previous=circuit).exists() or circuit.league_season != latest_season(league):
        raise LeagueError('SEASON_OVER', 'circuit')
    number = circuit.league_season
    circuit.league, circuit.league_season, circuit.division = None, None, None
    circuit.save()
    for index, c in enumerate(divisions(league, number), start=1):
        if c.division != index:
            c.division = index
            c.save(update_fields=['division'])


def member_division(league, number, key):
    """The division circuit of season `number` that `key` is in, or None."""
    kind, ident = key
    rows = CircuitMember.objects.filter(circuit__league=league, circuit__league_season=number)
    rows = rows.filter(team_id=ident) if kind == 'team' else rows.filter(user_id=ident)
    found = rows.select_related('circuit').first()
    return found.circuit if found else None


def add_member(circuit, key):
    """Put somebody in a division. One division per season."""
    if not circuit.league_id:
        raise LeagueError('CIRCUIT_NOT_IN_LEAGUE')
    kind, ident = key
    if kind != ('team' if circuit.entrant == 'team' else 'user'):
        raise LeagueError('CIRCUIT_ENTRANT_MISMATCH', 'entrant')
    elsewhere = member_division(circuit.league, circuit.league_season, key)
    if elsewhere is not None:
        raise LeagueError('ALREADY_IN_DIVISION', 'entrant', division=elsewhere.name)
    return CircuitMember.objects.create(circuit=circuit, came='start',
                                        **({'team_id': ident} if kind == 'team' else {'user_id': ident}))


def add_everyone_ranked(circuit):
    """Put in everybody the circuit's tournaments rank who is in no division
    of this season yet. Returns how many were added."""
    if not circuit.league_id:
        raise LeagueError('CIRCUIT_NOT_IN_LEAGUE')
    added = 0
    for row in standings.table(circuit, everyone=True):
        key = (row['entrant']['type'], row['entrant']['id'])
        if member_division(circuit.league, circuit.league_season, key) is None:
            add_member(circuit, key)
            added += 1
    return added


def zones(league, circuit, count):
    """Which of a division's `count` rows go up and down if the season ended
    now: (set of up positions, set of down positions), 0-based."""
    tops = divisions(league, circuit.league_season)
    if not tops:
        return set(), set()
    first, last = tops[0].division, tops[-1].division
    up = set(range(min(league.up, count))) if circuit.division != first else set()
    down = set()
    if circuit.division != last:
        down = {i for i in range(max(count - league.down, 0), count) if i not in up}
    return up, down


def _key(row):
    return (row['entrant']['type'], row['entrant']['id'])


def plan(league):
    """What ending the newest season would do, worked out from the rankings
    as they stand. [{circuit, division, up [rows], stay [rows], down [rows],
    level [{line, names}]}], top division first."""
    current = divisions(league)
    if not current:
        raise LeagueError('LEAGUE_NO_DIVISIONS')
    out = []
    for index, circuit in enumerate(current):
        rows = standings.table(circuit)
        up, down = zones(league, circuit, len(rows))
        moves = {'circuit': circuit, 'division': circuit.division,
                 'above': current[index - 1] if index > 0 else None,
                 'below': current[index + 1] if index + 1 < len(current) else None,
                 'up': [r for i, r in enumerate(rows) if i in up],
                 'down': [r for i, r in enumerate(rows) if i in down],
                 'stay': [r for i, r in enumerate(rows) if i not in up and i not in down],
                 'level': []}
        # Level on everything across a line: the name decided it.
        for line, cut in (('up', len(moves['up'])), ('down', len(rows) - len(moves['down']))):
            if not (0 < cut < len(rows)) or (line == 'up' and not up) or (line == 'down' and not down):
                continue
            a, b = rows[cut - 1], rows[cut]
            if a.get('rank') == b.get('rank'):
                moves['level'].append({'line': line, 'names': [a['entrant']['name'], b['entrant']['name']]})
        out.append(moves)
    return out


def _copy(circuit, number, label, starts_on, ends_on):
    """The next season of one division: the same circuit, no tournaments."""
    tiers = list(circuit.tiers.all())
    following = Circuit.objects.create(
        name=circuit.name, description=circuit.description, game=circuit.game, entrant=circuit.entrant,
        season=label, region=circuit.region, starts_on=starts_on, ends_on=ends_on, engine=circuit.engine,
        settings=dict(circuit.settings or {}), default_table=dict(circuit.default_table or {}),
        owner=circuit.owner, organization=circuit.organization, is_published=circuit.is_published,
        league=circuit.league, division=circuit.division, league_season=number, previous=circuit)
    CircuitTier.objects.bulk_create([CircuitTier(circuit=following, name=t.name, order=t.order,
                                                 table=dict(t.table or {})) for t in tiers])
    return following


def start_next(league, label, starts_on=None, ends_on=None):
    """End the newest season: copy every division, move everybody as the plan
    says, tell the people moved. Returns (new divisions, moved [(row, came,
    from circuit, to circuit)])."""
    label = (label or '').strip()
    if not label:
        raise LeagueError('SEASON_REQUIRED', 'season')
    current = divisions(league)
    if not current:
        raise LeagueError('LEAGUE_NO_DIVISIONS')
    if any(c.season == label for c in current):
        raise LeagueError('SEASON_SAME_NAME', 'season')
    if starts_on and ends_on and ends_on < starts_on:
        raise LeagueError('CIRCUIT_DATES_ORDER', 'ends_on')
    moves = plan(league)
    number = current[0].league_season + 1
    moved = []
    with transaction.atomic():
        following = {c.pk: _copy(c, number, label, starts_on, ends_on) for c in current}
        for m in moves:
            here = following[m['circuit'].pk]
            above = following[m['above'].pk] if m['above'] else None
            below = following[m['below'].pk] if m['below'] else None
            for group, came, to in (('up', 'up', above), ('down', 'down', below), ('stay', 'stayed', here)):
                for row in m[group]:
                    kind, ident = _key(row)
                    CircuitMember.objects.create(
                        circuit=to, came=came, came_from=m['circuit'],
                        **({'team_id': ident} if kind == 'team' else {'user_id': ident}))
                    if came != 'stayed':
                        moved.append((row, came, m['circuit'], to))
    for row, came, before, to in moved:
        _tell(league, row, came, to)
    return [following[c.pk] for c in current], moved


def _tell(league, row, came, to):
    from vent_auth.models import Teams
    from vent_auth.views_notifications import create_notification
    kind, ident = _key(row)
    if kind == 'team':
        team = Teams.objects.filter(team_id=ident).first()
        recipient = team.team_owner if team else None
    else:
        recipient = ident
    if recipient is None:
        return
    title = ('Promoted to %s' if came == 'up' else 'Relegated to %s') % to.name
    create_notification(
        recipient, 'tournament', title,
        '%s, %s.' % (league.name, to.season),
        link='/circuits/%s' % to.slug,
        metadata={'circuit_league': league.slug, 'circuit': to.slug, 'came': came})
