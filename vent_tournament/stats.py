"""A tournament's statistics, derived from the results and nothing else.

CEO, 28 September 2026 (inbox 306): "We might need like a metrics model for
tournaments, that were stats like h2h, most kills, most wins and other
possibilities depending on the game and what info in results was inputted,
can come from." Built 29 September.

**Nothing here is stored or typed.** Every number is read from what an
organiser or the players already entered: the head-to-head matches, the games
inside a series, the player lines of a team tie, the per-player numbers the
game counts (`MatchPlayerStat`: kills, goals, assists, whatever the tournament
chose), and the battle royale results. A stat somebody can edit is a stat that
disagrees with the results; this is the league table's rule applied to the
rest of the tournament.

**What the game records decides what is shown.** A football tournament has
goals and clean sheets and no kills; a battle royale has kills, first places and
match MVPs and no head-to-head score. A board appears only when something
behind it was entered, so an empty board never claims a leader.

Decisions worth stating:

* A cancelled match and a bye count for nothing anywhere.
* A walkover is a win and a loss, and counts as played, as the league table
  counts it by default; it adds nothing to scored or conceded, because nobody
  scored.
* A knockout level after play and won on penalties is a WIN for the side that
  went through, never a draw: the match had a winner.
* A rate (win rate, conceded per match) needs a minimum number of matches, or
  one win from one match leads every board.
* Ties share a rank. The board lists the first five places, not five names,
  and says how many are level rather than picking one by database order.
"""
from collections import defaultdict

from . import match_shape
from . import metrics as catalogue

COUNTED = ('completed', 'walkover_p1', 'walkover_p2')
TOP = 5
FOOTBALL = ('fc', 'fifa', 'efootball', 'pes', 'football', 'soccer')


def _rate_minimum(played_counts):
    most = max(played_counts, default=0)
    return 3 if most >= 3 else 1


def _outcome(m):
    """(winner_id, loser_id, draw, walkover) for a counted match, else None."""
    if m.status not in COUNTED or not m.participant_1_id or not m.participant_2_id:
        return None
    a, b = m.participant_1_id, m.participant_2_id
    if m.status == 'walkover_p1':
        return a, b, False, True
    if m.status == 'walkover_p2':
        return b, a, False, True
    if m.winner_id in (a, b):
        return m.winner_id, (b if m.winner_id == a else a), False, False
    if m.score_p1 > m.score_p2:
        return a, b, False, False
    if m.score_p2 > m.score_p1:
        return b, a, False, False
    return None, None, True, False


def _is_football(tournament):
    game = getattr(tournament, 'tournament_game', None)
    title = (getattr(game, 'game_title', '') or '').lower()
    words = title.replace('-', ' ').split()
    return any(word in words for word in FOOTBALL) or 'football' in title or 'fifa' in title


def _matches(tournament):
    return list(
        tournament.bracket_matches
        .select_related('participant_1__user', 'participant_2__user',
                        'participant_1__team', 'participant_2__team',
                        'participant_1__squad', 'participant_2__squad')
        .order_by('completed_at', 'round_number', 'match_number', 'id')
    )


def _blank(reg):
    return {
        'entrant': match_shape.entrant(reg),
        'played': 0, 'wins': 0, 'draws': 0, 'losses': 0,
        'walkover_wins': 0, 'walkover_losses': 0,
        'scored': 0, 'conceded': 0, 'clean_sheets': 0,
        'games_won': 0, 'games_lost': 0,
        'longest_win_streak': 0, '_streak': 0,
        'form': [],
    }


def entrant_records(tournament, matches=None):
    """One row per entrant who has a counted result, keyed by registration id."""
    matches = matches if matches is not None else _matches(tournament)
    rows = {}
    for m in matches:
        out = _outcome(m)
        if out is None:
            continue
        winner, loser, draw, walkover = out
        sides = ((m.participant_1_id, m.participant_1, m.score_p1, m.score_p2, 'p1', 'p2'),
                 (m.participant_2_id, m.participant_2, m.score_p2, m.score_p1, 'p2', 'p1'))
        for reg_id, reg, mine, theirs, me, them in sides:
            row = rows.get(reg_id)
            if row is None:
                row = rows[reg_id] = _blank(reg)
            row['played'] += 1
            if draw:
                row['draws'] += 1
                row['_streak'] = 0
                row['form'].append('D')
            elif winner == reg_id:
                row['wins'] += 1
                row['walkover_wins'] += 1 if walkover else 0
                row['_streak'] += 1
                row['longest_win_streak'] = max(row['longest_win_streak'], row['_streak'])
                row['form'].append('W')
            else:
                row['losses'] += 1
                row['walkover_losses'] += 1 if walkover else 0
                row['_streak'] = 0
                row['form'].append('L')
            if not walkover:
                row['scored'] += mine
                row['conceded'] += theirs
                if theirs == 0:
                    row['clean_sheets'] += 1
                for game in (m.games or []):
                    if not isinstance(game, dict):
                        continue
                    g_me, g_them = game.get(me), game.get(them)
                    if g_me is None or g_them is None:
                        continue
                    if g_me > g_them:
                        row['games_won'] += 1
                    elif g_them > g_me:
                        row['games_lost'] += 1
    for row in rows.values():
        row.pop('_streak')
        row['form'] = row['form'][-5:]
        decided = row['played']
        row['win_rate'] = round(100.0 * row['wins'] / decided, 1) if decided else 0.0
        real = decided - row['walkover_wins'] - row['walkover_losses']
        row['conceded_per_match'] = round(row['conceded'] / real, 2) if real else None
        row['difference'] = row['scored'] - row['conceded']
    return rows


def _board(key, subject, rows, value, *, reverse=True, minimum=None, extra=None):
    """The first TOP places on one measure. Ties share a place."""
    usable = [r for r in rows if value(r) is not None and (minimum is None or minimum(r))]
    if reverse:
        usable = [r for r in usable if value(r) > 0]
    if not usable:
        return None
    usable.sort(key=lambda r: value(r), reverse=reverse)
    out, place, last = [], 0, object()
    for i, r in enumerate(usable):
        v = value(r)
        if v != last:
            place = i + 1
            last = v
        if place > TOP:
            break
        line = {'place': place, 'value': v}
        line.update(extra(r) if extra else {})
        out.append(line)
    return {'key': key, 'subject': subject, 'rows': out}


def _person(user):
    from vent_auth.views_community import _person as described
    return described(None, user)


def player_metric_boards(tournament):
    """Totals of every per-player number the game counts, one board each."""
    from .models import MatchPlayerStat
    totals = defaultdict(lambda: defaultdict(float))
    matches = defaultdict(lambda: defaultdict(set))
    people = {}
    for stat in (MatchPlayerStat.objects
                 .filter(match__tournament=tournament)
                 .exclude(match__status__in=('cancelled', 'bye'))
                 .select_related('player')):
        totals[stat.key][stat.player_id] += stat.value
        matches[stat.key][stat.player_id].add(stat.match_id)
        people[stat.player_id] = stat.player
    boards = []
    for key in sorted(totals, key=lambda k: (catalogue.get(k) is None, k)):
        metric = catalogue.get(key)
        lower_wins = metric is not None and not metric.higher_is_better
        rows = [{'player_id': pid, 'value': round(v, metric.decimals if metric else 2),
                 'matches': len(matches[key][pid])}
                for pid, v in totals[key].items()]
        board = _board(
            'metric:%s' % key, 'player', rows, lambda r: r['value'],
            reverse=not lower_wins,
            extra=lambda r: {'person': _person(people[r['player_id']]), 'matches': r['matches']},
        )
        if board:
            board['metric'] = {'key': key, 'label': metric.label if metric else key,
                               'higher_is_better': not lower_wins}
            boards.append(board)
    return boards


def tie_game_boards(tournament):
    """The player-versus-player games inside team ties (the 2v2 aggregate)."""
    from .models import TieFixture
    rows = defaultdict(lambda: {'played': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'goals': 0})
    people = {}
    for f in (TieFixture.objects.filter(tie__tournament=tournament, status='completed')
              .select_related('player_1', 'player_2')):
        for me, them, mine, theirs in ((f.player_1, f.player_2, f.goals_1, f.goals_2),
                                       (f.player_2, f.player_1, f.goals_2, f.goals_1)):
            if me is None:
                continue
            people[me.user_id] = me
            r = rows[me.user_id]
            r['played'] += 1
            r['goals'] += mine
            if mine > theirs:
                r['wins'] += 1
            elif mine < theirs:
                r['losses'] += 1
            else:
                r['draws'] += 1
    flat = [dict(v, player_id=k) for k, v in rows.items()]
    extra = lambda r: {'person': _person(people[r['player_id']]), 'matches': r['played']}
    return [b for b in (
        _board('tie_wins', 'player', flat, lambda r: r['wins'], extra=extra),
        _board('tie_goals', 'player', flat, lambda r: r['goals'], extra=extra),
    ) if b]


def battle_royale(tournament):
    """Kills, first places, damage and match MVPs across every BR stage."""
    from . import br_engine
    from .models import BRResult
    results = list(
        BRResult.objects.filter(map__lobby__stage__tournament=tournament, played=True)
        .select_related('registration__user', 'registration__team', 'registration__squad',
                        'map__lobby__stage')
        .prefetch_related('players__user')
    )
    if not results:
        return None
    sides = defaultdict(lambda: {'maps': 0, 'first_places': 0, 'kills': 0, 'placements': []})
    regs = {}
    players = defaultdict(lambda: {'kills': 0, 'damage': 0, 'assists': 0, 'maps': 0, 'mvps': 0})
    ident = {}
    maps = {}
    for r in results:
        regs[r.registration_id] = r.registration
        s = sides[r.registration_id]
        s['maps'] += 1
        s['kills'] += r.kills
        if r.placement:
            s['placements'].append(r.placement)
            if r.placement == 1:
                s['first_places'] += 1
        maps.setdefault(r.map_id, r.map)
        for line in r.players.all():
            key = br_engine._player_key(line, r.registration_id)
            p = players[key]
            p['kills'] += line.kills
            p['damage'] += line.damage
            p['assists'] += line.assists
            p['maps'] += 1
            ident.setdefault(key, {
                'person': _person(line.user) if line.user_id else None,
                'name': line.name or (line.user.username if line.user_id else ''),
                'registration_id': r.registration_id,
            })
    for br_map in maps.values():
        mvp = br_engine.map_mvp(br_map, br_engine.settings_of(br_map.lobby.stage))
        if mvp and mvp['key'] in players:
            players[mvp['key']]['mvps'] += 1

    side_rows = [dict(v, registration_id=k,
                      best_placement=min(v['placements']) if v['placements'] else None,
                      average_placement=round(sum(v['placements']) / len(v['placements']), 1)
                      if v['placements'] else None)
                 for k, v in sides.items()]
    side_extra = lambda r: {'entrant': match_shape.entrant(regs[r['registration_id']]),
                            'matches': r['maps']}
    player_rows = [dict(v, key=k) for k, v in players.items()]
    player_extra = lambda r: {**ident[r['key']], 'matches': r['maps']}
    boards = [b for b in (
        _board('br_first_places', 'entrant', side_rows, lambda r: r['first_places'], extra=side_extra),
        _board('br_team_kills', 'entrant', side_rows, lambda r: r['kills'], extra=side_extra),
        _board('br_average_placement', 'entrant', side_rows, lambda r: r['average_placement'],
               reverse=False, extra=side_extra),
        _board('br_kills', 'player', player_rows, lambda r: r['kills'], extra=player_extra),
        _board('br_damage', 'player', player_rows, lambda r: r['damage'], extra=player_extra),
        _board('br_assists', 'player', player_rows, lambda r: r['assists'], extra=player_extra),
        _board('br_mvps', 'player', player_rows, lambda r: r['mvps'], extra=player_extra),
    ) if b]
    return {'boards': boards, 'maps_played': len(maps)}


def records(tournament, matches):
    """The biggest win and the highest-scoring match."""
    best_margin = best_total = None
    for m in matches:
        out = _outcome(m)
        if out is None or out[3]:
            continue
        margin = abs(m.score_p1 - m.score_p2)
        total = m.score_p1 + m.score_p2
        row = {'match_id': m.id, 'round_number': m.round_number, 'stage_id': m.stage_id,
               'participant_1': match_shape.entrant(m.participant_1),
               'participant_2': match_shape.entrant(m.participant_2),
               'score_p1': m.score_p1, 'score_p2': m.score_p2,
               'margin': margin, 'total': total}
        if margin and (best_margin is None or margin > best_margin['margin']):
            best_margin = row
        if total and (best_total is None or total > best_total['total']):
            best_total = row
    return {'biggest_win': best_margin, 'highest_scoring': best_total}


def _suggested_pair(tournament, matches):
    """Two entrants who have actually met, for the head-to-head picker to open
    on: the latest head-to-head result (usually the final), else the top two of
    the latest battle royale match. None when nobody has met anybody.

    Walk, 29 September 2026: the picker opened on the first two registrations,
    who had never played each other, and read as a feature with nothing in it."""
    for m in reversed(matches):
        if _outcome(m) is not None:
            return [m.participant_1_id, m.participant_2_id]
    from .models import BRResult
    latest = (BRResult.objects.filter(map__lobby__stage__tournament=tournament, played=True)
              .exclude(placement=None).order_by('-map__entered_at', '-map_id', 'placement')
              .values_list('map_id', 'registration_id'))
    pair = []
    for map_id, reg_id in latest:
        if pair and map_id != pair[0][0]:
            break
        pair.append((map_id, reg_id))
        if len(pair) == 2:
            return [pair[0][1], pair[1][1]]
    return None


def compute(tournament):
    """Everything the Stats tab shows, in one read."""
    matches = _matches(tournament)
    table = entrant_records(tournament, matches)
    rows = list(table.values())
    minimum_played = _rate_minimum([r['played'] for r in rows])
    football = _is_football(tournament)
    entrant_extra = lambda r: {'entrant': r['entrant'], 'matches': r['played']}

    boards = [b for b in (
        _board('wins', 'entrant', rows, lambda r: r['wins'], extra=entrant_extra),
        _board('win_rate', 'entrant', rows, lambda r: r['win_rate'],
               minimum=lambda r: r['played'] >= minimum_played, extra=entrant_extra),
        _board('longest_win_streak', 'entrant', rows, lambda r: r['longest_win_streak'],
               extra=entrant_extra),
        _board('scored', 'entrant', rows, lambda r: r['scored'], extra=entrant_extra),
        _board('conceded_per_match', 'entrant', rows, lambda r: r['conceded_per_match'],
               reverse=False, minimum=lambda r: r['played'] >= minimum_played,
               extra=entrant_extra),
        _board('clean_sheets', 'entrant', rows, lambda r: r['clean_sheets'], extra=entrant_extra)
        if football else None,
        _board('games_won', 'entrant', rows, lambda r: r['games_won'], extra=entrant_extra),
    ) if b]
    boards += tie_game_boards(tournament)
    boards += player_metric_boards(tournament)
    br = battle_royale(tournament)
    if br:
        boards += br['boards']

    ordered = sorted(rows, key=lambda r: (-r['wins'], -r['difference'], -r['scored'],
                                          (r['entrant'] or {}).get('name') or ''))
    # Everybody who can be compared, for the head-to-head picker. The table
    # above holds only entrants with a head-to-head result, which in a battle
    # royale is nobody (walk, 29 September 2026: the picker was empty).
    field = [match_shape.entrant(reg) for reg in (
        tournament.registrations.filter(status__in=('confirmed', 'pending', 'disqualified'))
        .select_related('user', 'team', 'squad').order_by('seed', 'id'))]
    return {
        'field': field,
        'suggested_pair': _suggested_pair(tournament, matches),
        'score_unit': 'goals' if football else 'score',
        'matches_counted': sum(1 for m in matches if _outcome(m) is not None),
        'maps_played': br['maps_played'] if br else 0,
        'minimum_for_rates': minimum_played,
        'boards': boards,
        'entrants': ordered,
        'records': records(tournament, matches),
        'has_results': bool(rows or br or any(b['subject'] == 'player' for b in boards)),
    }


def head_to_head(tournament, first_id, second_id):
    """Every meeting between two entrants of this tournament, across every stage.

    Head-to-head matches give wins, draws and scores; battle royale matches
    both played give who finished ahead, and the kills each took.
    """
    from .models import BRResult
    pair = {first_id, second_id}
    meetings = []
    summary = {'played': 0, 'first_wins': 0, 'second_wins': 0, 'draws': 0,
               'first_scored': 0, 'second_scored': 0}
    for m in _matches(tournament):
        if {m.participant_1_id, m.participant_2_id} != pair:
            continue
        out = _outcome(m)
        if out is None:
            continue
        winner, _loser, draw, walkover = out
        first_is_p1 = m.participant_1_id == first_id
        mine = m.score_p1 if first_is_p1 else m.score_p2
        theirs = m.score_p2 if first_is_p1 else m.score_p1
        summary['played'] += 1
        if draw:
            summary['draws'] += 1
            result = 'draw'
        elif winner == first_id:
            summary['first_wins'] += 1
            result = 'first'
        else:
            summary['second_wins'] += 1
            result = 'second'
        if not walkover:
            summary['first_scored'] += mine
            summary['second_scored'] += theirs
        meetings.append({
            'match_id': m.id, 'stage_id': m.stage_id, 'round_number': m.round_number,
            'bracket_side': m.bracket_side, 'is_final': m.is_final,
            'first_score': None if walkover else mine,
            'second_score': None if walkover else theirs,
            'penalties': ([m.penalties_p1, m.penalties_p2] if first_is_p1
                          else [m.penalties_p2, m.penalties_p1])
            if m.penalties_p1 is not None else None,
            'walkover': walkover, 'result': result, 'completed_at': m.completed_at,
        })

    by_map = defaultdict(dict)
    for r in BRResult.objects.filter(map__lobby__stage__tournament=tournament, played=True,
                                     registration_id__in=pair):
        by_map[r.map_id][r.registration_id] = r
    br = {'maps': 0, 'first_ahead': 0, 'second_ahead': 0, 'first_kills': 0, 'second_kills': 0}
    for both in by_map.values():
        if len(both) != 2:
            continue
        a, b = both[first_id], both[second_id]
        br['maps'] += 1
        br['first_kills'] += a.kills
        br['second_kills'] += b.kills
        if a.placement and b.placement:
            if a.placement < b.placement:
                br['first_ahead'] += 1
            elif b.placement < a.placement:
                br['second_ahead'] += 1
    return {'summary': summary, 'meetings': meetings, 'battle_royale': br if br['maps'] else None}
