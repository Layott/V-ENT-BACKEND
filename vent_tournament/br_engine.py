"""Battle royale, end to end: lobbies, matches, results, the table, MVPs.

CEO, 28 September 2026: "For battle royale, build it end to end you can use a
similar system used in AFC, including how they calculate and how point
systems, mvps, tie breaker and all of that is set".

AFC's rules, read from its code the same day and written again here:

  * A squad's points on one match: placement points from the organiser's
    table, plus kills times points a kill, plus (when set) assists and damage,
    plus a bonus, minus a penalty. A squad that did not play gets nothing for
    placement. Worked out when the result is saved, never typed.
  * The table: total first, then the organiser's ordered tiebreakers (booyahs,
    kills, placement in the last match by default), then seed, then name.
    Every row says which one separated it from the row above.
  * MVP per match by ordered criteria (kills, then damage, then assists), from
    every player or from the winning squad only. The stage MVP is whoever was
    match MVP most often, ties by the same criteria on totals.
  * Match point: once a squad is at or over the threshold, the first match it
    wins takes the lobby.
  * Carry-over: a head start into the next stage by finishing place.

How many squads share a lobby is the organiser's number (`lobby_size`), never
this file's.

Every refusal is a `BRError` with a code the screen translates.
"""
from django.db import transaction
from django.utils import timezone

from . import formats, stage_settings


class BRError(ValueError):
    def __init__(self, code, field=None, **extra):
        super().__init__(code)
        self.code = code
        self.field = field
        self.extra = extra


def is_br(stage):
    fmt = formats.get(stage.format) if stage is not None else None
    return bool(fmt and fmt.key == 'battle_royale')


def settings_of(stage):
    raw = stage.settings or {}
    if 'placement_points' in raw and 'lobby_size' in raw:
        return raw
    return stage_settings.clean_battle_royale(raw)


# ---------------------------------------------------------------------------
# The stage a one-format battle royale runs in
# ---------------------------------------------------------------------------

def _game_title(tournament):
    game = getattr(tournament, 'tournament_game', None)
    return getattr(game, 'game_title', '') if game else ''


def settings_from_ruleset(tournament, base=None):
    """The stage settings a tournament's own rules describe.

    The rules screen has carried a battle royale placement table and points a
    kill since before stages existed. It stays the place a one-format
    tournament's organiser edits them, so the stage takes its numbers from it
    rather than keeping a second copy that drifts.
    """
    from .models import TournamentRuleset

    raw = dict(base or stage_settings.br_game_defaults(_game_title(tournament)))
    row = TournamentRuleset.objects.filter(tournament=tournament).first()
    data = row.data if row is not None and isinstance(row.data, dict) else {}
    if data.get('placement_points'):
        table = {str(k): v for k, v in data['placement_points'].items()}
        # A table that is one of the presets keeps the preset's name, so the
        # stage says "Free Fire" rather than "my own table" for the default.
        named = next((name for name, preset in stage_settings.BR_PLACEMENT_PRESETS.items()
                      if {str(k): v for k, v in preset.items()} == table), None)
        raw['placement_preset'] = named or 'custom'
        raw['placement_points'] = data['placement_points']
    if data.get('points_per_kill') is not None:
        raw['per_kill'] = data['points_per_kill']
    if data.get('matches'):
        raw['maps'] = data['matches']
    wanted = [t for t in (data.get('tiebreakers') or [])
              if t in stage_settings.BR_TIEBREAKERS]
    if wanted:
        raw['tiebreakers'] = wanted
    return stage_settings.clean_battle_royale(raw)


def sync_to_ruleset(stage, user=None):
    """Write a one-format tournament's stage scoring back into its rules, so
    the rules screen shows what the table is actually scored by."""
    from .models import TournamentRuleset
    from . import rules as rules_mod

    tournament = stage.tournament
    if tournament.stages.count() != 1:
        return
    settings = settings_of(stage)
    row = TournamentRuleset.objects.filter(tournament=tournament).first()
    data = dict(row.data) if row is not None and isinstance(row.data, dict) and row.data \
        else rules_mod.preset_for('battle_royale', _game_title(tournament))
    data['format'] = 'battle_royale'
    data['scoring'] = 'battle_royale'
    data['placement_points'] = {int(k): v for k, v in settings['placement_points'].items()}
    data['points_per_kill'] = settings['per_kill']
    data['matches'] = settings['maps']
    data['tiebreakers'] = list(settings['tiebreakers'])
    if row is None:
        TournamentRuleset.objects.create(tournament=tournament, data=data, updated_by=user)
    else:
        row.data = data
        row.updated_by = user
        row.save(update_fields=['data', 'updated_by', 'updated_at'])


def sync_from_ruleset(tournament):
    """The rules screen changed a one-format battle royale's numbers: carry them
    onto its stage and rescore anything already entered."""
    stage = tournament.stages.first()
    if stage is None or tournament.stages.count() != 1 or not is_br(stage):
        return
    if stage.status == 'complete':
        return
    current = settings_of(stage)
    stage.settings = settings_from_ruleset(tournament, base=current)
    stage.save(update_fields=['settings'])
    rescore(stage)


def ensure_stage(tournament):
    """The stage a battle royale tournament's lobbies live in.

    A tournament built in stages already has them. One that runs as a single
    battle royale gets its one stage the first time anybody asks, seeded from
    its rules, so there is one place lobbies, matches and settings hang from.
    """
    from .models import TournamentStage
    from .services.bracket import normalize_bracket_type

    stages = list(tournament.stages.all().order_by('order'))
    if stages:
        return next((s for s in stages if is_br(s)), None)
    if normalize_bracket_type(tournament.bracket_type) != 'battle_royale':
        return None
    stage, _created = TournamentStage.objects.get_or_create(
        tournament=tournament, order=0,
        defaults={'label': 'Battle royale', 'format': 'battle_royale',
                  'settings': settings_from_ruleset(tournament)})
    return stage


# ---------------------------------------------------------------------------
# Scoring one result
# ---------------------------------------------------------------------------

def _r2(value):
    value = round(float(value), 2)
    return int(value) if value == int(value) else value


def score_result(result, settings):
    """Fill the point columns of one result from the stage's settings."""
    table = settings.get('placement_points') or {}
    if result.played and result.placement:
        placement_points = float(table.get(str(result.placement), 0) or 0)
    else:
        placement_points = 0.0
    kill_points = (result.kills * float(settings.get('per_kill') or 0)
                   + result.assists * float(settings.get('per_assist') or 0)
                   + result.damage / 1000.0 * float(settings.get('per_1000_damage') or 0))
    result.placement_points = _r2(placement_points)
    result.kill_points = _r2(kill_points)
    result.total = _r2(placement_points + kill_points
                       + float(result.bonus or 0) - float(result.penalty or 0))
    return result


def rescore(stage):
    """Recompute every result in the stage, after its scoring changed."""
    from .models import BRResult

    settings = settings_of(stage)
    changed = 0
    for result in BRResult.objects.filter(map__lobby__stage=stage):
        before = (result.placement_points, result.kill_points, result.total)
        score_result(result, settings)
        if before != (result.placement_points, result.kill_points, result.total):
            result.save(update_fields=['placement_points', 'kill_points', 'total'])
            changed += 1
    return changed


def has_results(stage):
    from .models import BRMap
    return BRMap.objects.filter(lobby__stage=stage, status='entered').exists()


# ---------------------------------------------------------------------------
# Drawing lobbies
# ---------------------------------------------------------------------------

def lobby_count(entrants, lobby_size):
    lobby_size = max(1, int(lobby_size or 1))
    return max(1, -(-entrants // lobby_size))


def snake(ordered, lobbies):
    """Spread seeds across lobbies 1, 2, 3, 3, 2, 1, 1, 2 ... so every lobby
    gets a fair share of the strong seeds."""
    out = [[] for _ in range(lobbies)]
    for i, item in enumerate(ordered):
        lap, pos = divmod(i, lobbies)
        out[pos if lap % 2 == 0 else lobbies - 1 - pos].append(item)
    return out


def draw(stage, ordered, carried=None):
    """Seat `ordered` (best seed first) into lobbies and give each lobby its
    matches. `carried` maps registration id to a head start."""
    from .models import BRLobby, BRLobbySeat, BRMap

    if stage.br_lobbies.exists():
        raise BRError('STAGE_ALREADY_DRAWN')
    settings = settings_of(stage)
    count = lobby_count(len(ordered), settings['lobby_size'])
    carried = carried or {}
    groups = snake(ordered, count)
    for number, members in enumerate(groups, start=1):
        lobby = BRLobby.objects.create(stage=stage, number=number)
        for seat, reg in enumerate(members, start=1):
            BRLobbySeat.objects.create(lobby=lobby, registration=reg, seat=seat,
                                       carried_points=float(carried.get(reg.id, 0) or 0))
        for map_no in range(1, settings['maps'] + 1):
            BRMap.objects.create(lobby=lobby, number=map_no)
    return {
        'lobbies': count,
        'matches_created': count * settings['maps'],
        'rounds_count': settings['maps'],
        'lobby_size': settings['lobby_size'],
    }


def move_seat(stage, registration_id, lobby_number):
    """Move one squad to another lobby, before either lobby has played."""
    from .models import BRLobbySeat

    seat = BRLobbySeat.objects.select_related('lobby').filter(
        lobby__stage=stage, registration_id=registration_id).first()
    if seat is None:
        raise BRError('NOT_IN_THIS_STAGE', 'registration_id')
    target = stage.br_lobbies.filter(number=lobby_number).first()
    if target is None:
        raise BRError('NO_SUCH_LOBBY', 'lobby')
    if target.pk == seat.lobby_id:
        return seat
    for lobby in (seat.lobby, target):
        if lobby.maps.filter(status='entered').exists():
            raise BRError('LOBBY_HAS_RESULTS', 'lobby')
    seat.lobby = target
    seat.seat = (target.seats.order_by('-seat').values_list('seat', flat=True).first() or 0) + 1
    seat.save(update_fields=['lobby', 'seat'])
    return seat


def add_map(lobby):
    from .models import BRMap
    if lobby.stage.status == 'complete':
        raise BRError('STAGE_CLOSED')
    number = (lobby.maps.order_by('-number').values_list('number', flat=True).first() or 0) + 1
    if number > 30:
        raise BRError('TOO_MANY_MATCHES')
    return BRMap.objects.create(lobby=lobby, number=number)


def remove_map(br_map):
    """Only the last match of a lobby, and only before it has results."""
    if br_map.status == 'entered':
        raise BRError('MATCH_HAS_RESULTS')
    last = br_map.lobby.maps.order_by('-number').first()
    if last.pk != br_map.pk:
        raise BRError('ONLY_THE_LAST_MATCH')
    if br_map.lobby.maps.count() <= 1:
        raise BRError('LOBBY_NEEDS_A_MATCH')
    br_map.delete()


# ---------------------------------------------------------------------------
# Entering results
# ---------------------------------------------------------------------------

def _whole(raw, field, low=0, high=100000):
    if raw in (None, ''):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise BRError('NOT_A_NUMBER', field)
    if value < low or value > high:
        raise BRError('OUT_OF_RANGE', field)
    return value


def _points(raw, field):
    if raw in (None, ''):
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise BRError('NOT_A_NUMBER', field)
    if value != value or value < 0 or value > 10000:
        raise BRError('OUT_OF_RANGE', field)
    return value


def _bool(raw, default=True):
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(raw)


def clean_rows(br_map, rows):
    """Check a whole match's results against the lobby. Returns cleaned rows,
    one per seated squad (a squad left out did not play)."""
    seats = {s.registration_id: s for s in br_map.lobby.seats.select_related(
        'registration', 'registration__team', 'registration__squad', 'registration__user')}
    if not isinstance(rows, list) or not rows:
        raise BRError('NO_RESULTS', 'rows')
    seen = set()
    placements = set()
    cleaned = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise BRError('NOT_A_ROW', 'rows', index=index)
        try:
            rid = int(raw.get('registration_id'))
        except (TypeError, ValueError):
            raise BRError('NOT_IN_THIS_LOBBY', 'registration_id', index=index)
        if rid not in seats:
            raise BRError('NOT_IN_THIS_LOBBY', 'registration_id', index=index)
        if rid in seen:
            raise BRError('SQUAD_TWICE', 'registration_id', index=index)
        seen.add(rid)
        played = _bool(raw.get('played'), True)
        placement = None
        if played:
            placement = _whole(raw.get('placement'), 'placement', 1, len(seats))
            if not placement:
                raise BRError('PLACEMENT_REQUIRED', 'placement', index=index)
            if placement in placements:
                raise BRError('PLACEMENT_TWICE', 'placement', index=index)
            placements.add(placement)

        members = {u.user_id: u for u in seats[rid].registration.people}
        players = []
        for p_raw in (raw.get('players') or []) if played else []:
            if not isinstance(p_raw, dict):
                raise BRError('NOT_A_ROW', 'players', index=index)
            user = None
            if p_raw.get('user_id') not in (None, ''):
                try:
                    uid = int(p_raw.get('user_id'))
                except (TypeError, ValueError):
                    raise BRError('PLAYER_NOT_IN_SQUAD', 'players', index=index)
                user = members.get(uid)
                if user is None:
                    raise BRError('PLAYER_NOT_IN_SQUAD', 'players', index=index)
            name = str(p_raw.get('name') or (user.username if user else '')).strip()[:60]
            if not name and user is None:
                continue
            players.append({
                'user': user, 'name': name,
                'kills': _whole(p_raw.get('kills'), 'kills', 0, 200),
                'assists': _whole(p_raw.get('assists'), 'assists', 0, 200),
                'damage': _whole(p_raw.get('damage'), 'damage', 0, 100000),
            })
        if len({p['user'].user_id for p in players if p['user']}) != \
                len([p for p in players if p['user']]):
            raise BRError('PLAYER_TWICE', 'players', index=index)

        kills = _whole(raw.get('kills'), 'kills', 0, 500) if played else 0
        if players:
            summed = sum(p['kills'] for p in players)
            # A squad's kills are its players' kills. Both given and different
            # is a typing mistake; saying so beats silently picking one.
            if raw.get('kills') not in (None, '') and kills != summed:
                raise BRError('KILLS_DO_NOT_ADD_UP', 'kills', index=index)
            kills = summed
        assists = _whole(raw.get('assists'), 'assists', 0, 500) if played else 0
        damage = _whole(raw.get('damage'), 'damage', 0, 1000000) if played else 0
        if players:
            if raw.get('assists') in (None, ''):
                assists = sum(p['assists'] for p in players)
            if raw.get('damage') in (None, ''):
                damage = sum(p['damage'] for p in players)
        cleaned[rid] = {
            'played': played, 'placement': placement, 'kills': kills,
            'assists': assists, 'damage': damage,
            'bonus': _points(raw.get('bonus'), 'bonus'),
            'penalty': _points(raw.get('penalty'), 'penalty'),
            'adjustment_note': str(raw.get('adjustment_note') or '').strip()[:200],
            'players': players,
        }
    for rid in seats:
        cleaned.setdefault(rid, {'played': False, 'placement': None, 'kills': 0,
                                 'assists': 0, 'damage': 0, 'bonus': 0.0,
                                 'penalty': 0.0, 'adjustment_note': '',
                                 'players': []})
    if not placements:
        raise BRError('NOBODY_PLAYED', 'rows')
    return cleaned


def enter_results(br_map, rows, user, via='manual'):
    """Replace a match's results. The one path typing and a screenshot read
    both come through."""
    from .models import BRPlayerLine, BRResult

    stage = br_map.lobby.stage
    if stage.status == 'complete':
        raise BRError('STAGE_CLOSED')
    settings = settings_of(stage)
    cleaned = clean_rows(br_map, rows)
    with transaction.atomic():
        br_map.results.all().delete()
        for rid, row in cleaned.items():
            result = BRResult(map=br_map, registration_id=rid,
                              played=row['played'], placement=row['placement'],
                              kills=row['kills'], assists=row['assists'],
                              damage=row['damage'], bonus=row['bonus'],
                              penalty=row['penalty'],
                              adjustment_note=row['adjustment_note'])
            score_result(result, settings)
            result.save()
            for p in row['players']:
                BRPlayerLine.objects.create(result=result, user=p['user'], name=p['name'],
                                            kills=p['kills'], assists=p['assists'],
                                            damage=p['damage'])
        br_map.status = 'entered'
        br_map.entered_via = via if via in ('manual', 'ocr') else 'manual'
        br_map.entered_by = user
        br_map.entered_at = timezone.now()
        br_map.save(update_fields=['status', 'entered_via', 'entered_by', 'entered_at'])
        if stage.status == 'pending':
            stage.status = 'running'
            stage.save(update_fields=['status'])
    return br_map


def clear_results(br_map):
    stage = br_map.lobby.stage
    if stage.status == 'complete':
        raise BRError('STAGE_CLOSED')
    with transaction.atomic():
        br_map.results.all().delete()
        br_map.status = 'pending'
        br_map.entered_via = ''
        br_map.entered_by = None
        br_map.entered_at = None
        br_map.save(update_fields=['status', 'entered_via', 'entered_by', 'entered_at'])


# ---------------------------------------------------------------------------
# MVPs
# ---------------------------------------------------------------------------

def _player_key(line, registration_id):
    if line.user_id:
        return 'u%s' % line.user_id
    return 'n%s:%s' % (registration_id, (line.name or '').strip().lower())


def _criteria_key(stats, criteria):
    return tuple(-(stats.get(c) or 0) for c in criteria)


def map_mvp(br_map, settings, results=None):
    """The best player on one match, by the organiser's ordered criteria."""
    criteria = settings.get('mvp_criteria') or list(stage_settings.BR_MVP_CRITERIA)
    results = results if results is not None else list(
        br_map.results.prefetch_related('players', 'players__user'))
    if settings.get('mvp_scope') == 'winning_team':
        results = [r for r in results if r.played and r.placement == 1]
    best = None
    for result in results:
        for line in result.players.all():
            stats = {'kills': line.kills, 'damage': line.damage, 'assists': line.assists}
            if not any(stats.values()):
                continue
            key = _criteria_key(stats, criteria)
            if best is None or key < best[0]:
                best = (key, line, result)
            elif key == best[0]:
                # Level on every criterion: nobody is MVP of this match, which
                # is more honest than whichever row the database returned first.
                best = (key, None, None)
    if best is None or best[1] is None:
        return None
    line, result = best[1], best[2]
    return {'key': _player_key(line, result.registration_id),
            'user': _person(line.user) if line.user_id else None,
            'user_id': line.user_id,
            'username': line.user.username if line.user_id else None,
            'name': line.name or (line.user.username if line.user_id else ''),
            'registration_id': result.registration_id,
            'kills': line.kills, 'damage': line.damage, 'assists': line.assists}


def _person(user):
    """An MVP is a person on the page: the one person builder, so the name
    carries its picture, its founder mark and its link like everywhere else
    (walk, 28 September 2026). A screen name nobody matched has no account and
    stays a plain `name`."""
    from vent_auth.views_community import _person as described
    return described(None, user)


def stage_mvp(stage, maps=None):
    """Most match MVPs; ties on the same criteria over the whole stage."""
    settings = settings_of(stage)
    criteria = settings.get('mvp_criteria') or list(stage_settings.BR_MVP_CRITERIA)
    maps = maps if maps is not None else _entered_maps(stage)
    counts = {}
    totals = {}
    ident = {}
    for br_map in maps:
        results = list(br_map.results.all())
        mvp = map_mvp(br_map, settings, results)
        for result in results:
            for line in result.players.all():
                key = _player_key(line, result.registration_id)
                t = totals.setdefault(key, {'kills': 0, 'damage': 0, 'assists': 0})
                t['kills'] += line.kills
                t['damage'] += line.damage
                t['assists'] += line.assists
                ident.setdefault(key, {
                    'user': _person(line.user) if line.user_id else None,
                    'user_id': line.user_id,
                    'username': line.user.username if line.user_id else None,
                    'name': line.name or (line.user.username if line.user_id else ''),
                    'registration_id': result.registration_id})
        if mvp:
            counts[mvp['key']] = counts.get(mvp['key'], 0) + 1
    if not counts:
        return None
    ranked = sorted(counts, key=lambda k: ((-counts[k],) + _criteria_key(totals[k], criteria)))
    top = ranked[0]
    out = dict(ident[top])
    out.update({'match_mvps': counts[top], **totals[top]})
    return out


def _entered_maps(stage):
    from .models import BRMap
    return list(BRMap.objects.filter(lobby__stage=stage, status='entered')
                .select_related('lobby')
                .prefetch_related('results', 'results__players', 'results__players__user')
                .order_by('lobby__number', 'number'))


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def _tiebreak_value(row, key):
    """Smaller sorts first."""
    return {
        'placement_count': -row['booyahs'],
        'total_kills': -row['kills'],
        'last_map_placement': row['last_placement'] if row['last_placement'] else 10 ** 4,
        'placement_points': -row['placement_points'],
        'kill_points': -row['kill_points'],
        'best_placement': row['best_placement'] if row['best_placement'] else 10 ** 4,
        'mvp_count': -row['mvp_count'],
        'bonus': -row['bonus'],
        'fewest_penalties': row['penalty'],
        'maps_played': row['maps_played'],
    }.get(key, 0)


def _rank(rows, tiebreakers):
    names = ['points'] + list(tiebreakers) + ['seed', 'name']

    def key(r):
        return ((0 if r['champion'] else 1, -r['points'])
                + tuple(_tiebreak_value(r, t) for t in tiebreakers)
                + (r['seed'] if r['seed'] is not None else 10 ** 6, r['name'].lower()))

    ranked = sorted(rows, key=key)
    for i, row in enumerate(ranked):
        row['decided_by'] = None
        if i:
            pk, rk = key(ranked[i - 1]), key(row)
            # Match point is the reason only when this squad would otherwise
            # be above: on fewer points it is simply points (walk, 28
            # September 2026: 39 behind 54 read "below the squad that won on
            # match point").
            if pk[0] != rk[0] and row['points'] >= ranked[i - 1]['points']:
                row['decided_by'] = 'match_point'
            else:
                for idx, name in enumerate(names):
                    if pk[idx + 1] != rk[idx + 1]:
                        row['decided_by'] = name
                        break
    return ranked


def standings(stage):
    """Every squad in the stage, ranked overall, each carrying its lobby and
    its rank inside that lobby. Shaped like the other formats' rows so
    advancing and final positions read it the same way."""
    from .models import BRLobbySeat
    from .match_shape import names

    settings = settings_of(stage)
    seats = list(BRLobbySeat.objects.filter(lobby__stage=stage)
                 .select_related('lobby', 'registration', 'registration__user',
                                 'registration__team', 'registration__squad'))
    maps = _entered_maps(stage)
    rows = {}
    for seat in seats:
        reg = seat.registration
        display, handle = names(reg)[0] or '', names(reg)[1]
        rows[reg.id] = {
            'registration_id': reg.id, 'name': display, 'handle': handle,
            'group': seat.lobby.number, 'lobby': seat.lobby.number,
            'lobby_name': seat.lobby.name,
            'seed': reg.seed, 'maps_played': 0, 'booyahs': 0, 'kills': 0,
            'assists': 0, 'damage': 0, 'placement_points': 0.0,
            'kill_points': 0.0, 'bonus': 0.0, 'penalty': 0.0,
            'carried': float(seat.carried_points or 0), 'points': 0.0,
            'mvp_count': 0, 'last_placement': None, 'best_placement': None,
            'placements': [], 'champion': False,
            # The keys the other formats' rows carry, so one reader serves all.
            'played': 0, 'wins': 0, 'draws': 0, 'losses': 0,
            'goals_for': 0, 'goals_against': 0, 'goal_difference': 0,
            'buchholz': 0, 'rank': None, 'lobby_rank': None,
            'status': 'playing', 'decided_by': None,
        }

    threshold = int(settings.get('match_point') or 0)
    champions = {}
    running = {rid: row['carried'] for rid, row in rows.items()}
    for br_map in maps:
        results = list(br_map.results.all())
        lobby_no = br_map.lobby.number
        for result in results:
            row = rows.get(result.registration_id)
            if row is None:
                continue
            if result.played:
                row['maps_played'] += 1
                row['placements'].append(result.placement)
                row['last_placement'] = result.placement
                if result.placement == 1:
                    row['booyahs'] += 1
                    # Match point: already at the threshold when this match
                    # began, and won it. First one per lobby takes it.
                    if threshold and lobby_no not in champions \
                            and running.get(result.registration_id, 0) >= threshold:
                        champions[lobby_no] = result.registration_id
            row['kills'] += result.kills
            row['assists'] += result.assists
            row['damage'] += result.damage
            row['placement_points'] += result.placement_points
            row['kill_points'] += result.kill_points
            row['bonus'] += result.bonus
            row['penalty'] += result.penalty
        for result in results:
            if result.registration_id in running:
                running[result.registration_id] += result.total
        mvp = map_mvp(br_map, settings, results)
        if mvp and mvp['registration_id'] in rows:
            rows[mvp['registration_id']]['mvp_count'] += 1

    for rid, row in rows.items():
        row['points'] = _r2(running[rid])
        for f in ('placement_points', 'kill_points', 'bonus', 'penalty', 'carried'):
            row[f] = _r2(row[f])
        row['best_placement'] = min(row['placements']) if row['placements'] else None
        row['played'] = row['maps_played']
        row['wins'] = row['booyahs']
        row['champion'] = rid in champions.values()
        if row['champion']:
            row['status'] = 'champion'
        del row['placements']

    tiebreakers = settings.get('tiebreakers') or stage_settings.BR_DEFAULT_TIEBREAKERS
    by_lobby = {}
    for row in rows.values():
        by_lobby.setdefault(row['lobby'], []).append(row)
    for lobby_rows in by_lobby.values():
        for i, row in enumerate(_rank(lobby_rows, tiebreakers)):
            row['lobby_rank'] = i + 1
    ranked = _rank(list(rows.values()), tiebreakers)
    for i, row in enumerate(ranked):
        row['rank'] = i + 1
    return ranked


def lobby_finished(lobby, champion_lobbies=()):
    if lobby.number in champion_lobbies:
        return True
    return not lobby.maps.exclude(status='entered').exists()


def finished(stage):
    if not stage.br_lobbies.exists():
        return False
    champion_lobbies = set()
    if int(settings_of(stage).get('match_point') or 0):
        champion_lobbies = {r['lobby'] for r in standings(stage) if r['champion']}
    return all(lobby_finished(l, champion_lobbies) for l in stage.br_lobbies.all())


def open_maps(stage):
    from .models import BRMap
    return BRMap.objects.filter(lobby__stage=stage).exclude(status='entered').count()


def advancing(stage):
    """Who goes through: the top N overall, or the top N of each lobby taken
    rank by rank across lobbies (1A, 1B, 2A, 2B), as the stage says. Each row
    carries its head start into the next stage."""
    settings = settings_of(stage)
    count = stage.advances or 0
    if count <= 0:
        return []
    rows = standings(stage)
    carry = settings.get('carry_over') or {}
    if settings.get('advance_by') == 'lobby':
        by_lobby = {}
        for r in sorted(rows, key=lambda r: (r['lobby'], r['lobby_rank'])):
            by_lobby.setdefault(r['lobby'], []).append(r)
        chosen = []
        for rank in range(count):
            for lobby in sorted(by_lobby):
                if rank < len(by_lobby[lobby]):
                    chosen.append(by_lobby[lobby][rank])
        for r in chosen:
            r['carry'] = carry.get(str(r['lobby_rank']), 0)
    else:
        chosen = rows[:count]
        for r in chosen:
            r['carry'] = carry.get(str(r['rank']), 0)
    return chosen


def summary(stage, viewer=None, may_run=False):
    """Everything a screen needs about a battle royale stage in one payload.

    Room codes are shown to the people running it and to the squads seated in
    that lobby, never to anybody else.
    """
    from .match_shape import entrant

    settings = settings_of(stage)
    table = standings(stage)
    by_reg = {r['registration_id']: r for r in table}
    mvp = stage_mvp(stage)
    lobbies = []
    mine = []
    for lobby in stage.br_lobbies.prefetch_related(
            'seats__registration__user', 'seats__registration__team',
            'seats__registration__squad', 'maps__results__players__user',
            'maps__entered_by'):
        seats = list(lobby.seats.all())
        own = [s.registration_id for s in seats if viewer and s.registration.plays_for(viewer)]
        mine.extend(own)
        seated_here = bool(own)
        show_room = may_run or seated_here
        maps = []
        for br_map in lobby.maps.all():
            results = sorted(br_map.results.all(),
                             key=lambda r: (not r.played, r.placement or 999))
            maps.append({
                'id': br_map.id, 'number': br_map.number,
                'map_name': br_map.map_name, 'scheduled_at': br_map.scheduled_at,
                'status': br_map.status, 'entered_via': br_map.entered_via,
                'entered_at': br_map.entered_at,
                'entered_by': br_map.entered_by.username if (may_run and br_map.entered_by_id) else None,
                'room_code': br_map.room_code if show_room else None,
                'room_password': br_map.room_password if show_room else None,
                'has_room': bool(br_map.room_code),
                'mvp': map_mvp(br_map, settings, results) if br_map.status == 'entered' else None,
                'results': [{
                    'registration_id': r.registration_id,
                    'name': by_reg.get(r.registration_id, {}).get('name', ''),
                    'played': r.played, 'placement': r.placement,
                    'kills': r.kills, 'assists': r.assists, 'damage': r.damage,
                    'bonus': r.bonus, 'penalty': r.penalty,
                    'adjustment_note': r.adjustment_note,
                    'placement_points': r.placement_points,
                    'kill_points': r.kill_points, 'total': r.total,
                    'players': [{'user_id': p.user_id,
                                 'username': p.user.username if p.user_id else None,
                                 'name': p.name, 'kills': p.kills,
                                 'assists': p.assists, 'damage': p.damage}
                                for p in r.players.all()],
                } for r in results],
            })
        lobbies.append({
            'id': lobby.id, 'number': lobby.number, 'name': lobby.name,
            'seated_here': seated_here,
            'seats': [dict(entrant(s.registration), seat=s.seat,
                           carried_points=_r2(s.carried_points),
                           roster=[{'user_id': u.user_id, 'username': u.username}
                                   for u in s.registration.people] if may_run else None)
                      for s in seats],
            'maps': maps,
            'standings': sorted([r for r in table if r['lobby'] == lobby.number],
                                key=lambda r: r['lobby_rank']),
            'finished': lobby_finished(lobby, {r['lobby'] for r in table if r['champion']}),
        })
    return {
        'stage_id': stage.id,
        'settings': settings,
        'drawn': bool(lobbies),
        'lobbies': lobbies,
        'standings': table,
        'mvp': mvp,
        'finished': finished(stage) if lobbies else False,
        'open_matches': open_maps(stage) if lobbies else 0,
        'has_results': has_results(stage),
        # The squads the reader plays for, so their row is marked as theirs.
        'my_registration_ids': mine,
    }
