"""Scoring a league of team ties, and the two tables it produces.

The format, in the CEO's words:

    10 total players and 5 teams, 2 players per team. If team A faces team B and
    player 1 in team A beats player 1 in team B 3-0, then player 2 in team A
    loses 0-2 to player 2 in team B, the overall score is Team A 3-2 Team B and
    team A wins.

The thing to hold on to is that **a tie is decided by total goals, not by games
won**. In that example each team won one game. Counting games would call it a
draw; the aggregate makes it a win for A. Every function here works from goals.

Two tables come out of the same fixtures, because a team competition is also an
individual one and the CEO asked for both:

    the team table    played, won, drawn, lost, goals for and against, points
    the player table  the same, for each person, from their own games

Points and tiebreakers are the organiser's, not ours. `LeagueRules` carries them
and the defaults are only defaults.
"""
from collections import defaultdict

from ..models import BracketMatch, LeagueRules, TieFixture


# ---------------------------------------------------------------------------
# One tie
# ---------------------------------------------------------------------------

def aggregate(tie):
    """(goals for participant 1, goals for participant 2) across the tie.

    Only completed fixtures count. A tie half played has a running aggregate,
    which is what a live table should show, but `settle` will not close it.
    """
    totals = TieFixture.objects.filter(tie=tie, status='completed')
    one = sum(f.goals_1 for f in totals)
    two = sum(f.goals_2 for f in totals)
    return one, two


def settle(tie):
    """Write the aggregate onto the tie and decide it. Returns the winner or None.

    None means a draw, which is a real result in a league and must not be
    turned into a coin toss. Knockout callers are the ones that need a decider,
    and that is their business rather than this function's.
    """
    fixtures = list(TieFixture.objects.filter(tie=tie))
    if not fixtures or any(f.status != 'completed' for f in fixtures):
        return None

    one, two = aggregate(tie)
    tie.score_p1 = one
    tie.score_p2 = two

    if one > two:
        tie.winner = tie.participant_1
    elif two > one:
        tie.winner = tie.participant_2
    else:
        tie.winner = None

    tie.status = 'completed'
    tie.save(update_fields=['score_p1', 'score_p2', 'winner', 'status'])
    return tie.winner


def rules_for(tournament):
    """The organiser's scoring, or the familiar defaults if they set none."""
    rules = LeagueRules.objects.filter(tournament=tournament).first()
    if rules:
        return rules
    # Unsaved, so reading a table never writes to the database as a side effect.
    return LeagueRules(tournament=tournament)


# ---------------------------------------------------------------------------
# The team table
# ---------------------------------------------------------------------------

def _blank_row():
    return {
        'played': 0, 'won': 0, 'drawn': 0, 'lost': 0,
        'goals_for': 0, 'goals_against': 0,
        'fixtures_won': 0,
    }


def _points_adjustment(tournament, name, rules):
    """What an organiser has added or taken away from this entrant.

    Kept here so points have ONE definition. Computing them in the table and
    then again in the extra columns is how a page ends up showing a total that
    does not match its own arithmetic.
    """
    total = 0
    for row in ((tournament.options or {}).get('league_adjustments') or []):
        if row.get('player') != name:
            continue
        try:
            value = int(row.get('value') or 0)
        except (TypeError, ValueError):
            continue
        metric = str(row.get('metric') or '').upper()
        if metric == 'PTS':
            total += value
        elif metric == 'W':
            total += value * rules.points_win
        elif metric == 'D':
            total += value * rules.points_draw
        elif metric == 'L':
            total += value * rules.points_loss
    return total

def team_table(tournament):
    """One row per registered team, ordered by the organiser's rules."""
    rules = rules_for(tournament)

    regs = list(tournament.registrations.filter(status='confirmed'))
    rows = {r.id: _blank_row() for r in regs}
    # Head to head is only answerable if we remember who beat whom.
    beat = defaultdict(set)

    # Completed ties AND walkovers. A walkover is a real result with a
    # configurable scoreline, and leaving it out here while the extra columns
    # counted it would have given one table two answers about the same tie.
    # Cancelled is excluded on purpose: it counts for nothing anywhere.
    ties = (BracketMatch.objects
            .filter(tournament=tournament,
                    status__in=('completed', 'walkover_p1', 'walkover_p2'))
            .prefetch_related('fixtures'))

    stats = stat_settings(tournament)

    for tie in ties:
        a, b = tie.participant_1_id, tie.participant_2_id
        if a not in rows or b not in rows:
            continue          # a bye, or an entrant since withdrawn

        walkover_to = (a if tie.status == 'walkover_p1'
                       else b if tie.status == 'walkover_p2' else None)

        if walkover_to is None:
            for_a, for_b = tie.score_p1, tie.score_p2
        else:
            # The notional scoreline the organiser chose, and only if they
            # want walkover goals reaching the goal columns at all.
            won = stats['walkover_goals_winner']
            lost = stats['walkover_goals_loser']
            if not stats['walkover_goals_count']:
                won = lost = 0
            for_a, for_b = ((won, lost) if walkover_to == a else (lost, won))

        if walkover_to is not None and not stats['walkover_counts_as_played']:
            # Points still awarded, but it is not a game either of them played.
            winner_row = rows[walkover_to]
            loser_row = rows[b if walkover_to == a else a]
            winner_row['walkover_points'] = (
                winner_row.get('walkover_points', 0)
                + stats['walkover_points_winner'])
            loser_row['walkover_points'] = (
                loser_row.get('walkover_points', 0)
                + stats['walkover_points_loser'])
            continue

        rows[a]['played'] += 1
        rows[b]['played'] += 1
        rows[a]['goals_for'] += for_a
        rows[a]['goals_against'] += for_b
        rows[b]['goals_for'] += for_b
        rows[b]['goals_against'] += for_a

        for f in tie.fixtures.all():
            if f.status != 'completed':
                continue
            if f.goals_1 > f.goals_2:
                rows[a]['fixtures_won'] += 1
            elif f.goals_2 > f.goals_1:
                rows[b]['fixtures_won'] += 1

        if walkover_to is not None:
            # A walkover is a win for the table, but it pays what the organiser
            # set for one rather than what beating somebody is worth. Not
            # counted as a head-to-head result either: nobody played it.
            loser = b if walkover_to == a else a
            rows[walkover_to]['won'] += 1
            rows[loser]['lost'] += 1
            rows[walkover_to]['walkover_points'] = (
                rows[walkover_to].get('walkover_points', 0)
                + stats['walkover_points_winner'] - rules.points_win)
            rows[loser]['walkover_points'] = (
                rows[loser].get('walkover_points', 0)
                + stats['walkover_points_loser'] - rules.points_loss)
        elif for_a > for_b:
            rows[a]['won'] += 1
            rows[b]['lost'] += 1
            beat[a].add(b)
        elif for_b > for_a:
            rows[b]['won'] += 1
            rows[a]['lost'] += 1
            beat[b].add(a)
        else:
            rows[a]['drawn'] += 1
            rows[b]['drawn'] += 1

    table = []
    by_reg = {r.id: r for r in regs}
    for reg_id, row in rows.items():
        reg = by_reg[reg_id]
        row = dict(row)
        row['registration_id'] = reg_id
        row['team_id'] = reg.team_id
        row['name'] = _entrant_name(reg)
        row['goal_difference'] = row['goals_for'] - row['goals_against']
        row['points'] = (row['won'] * rules.points_win
                         + row['drawn'] * rules.points_draw
                         + row['lost'] * rules.points_loss
                         # The difference between what a walkover pays and
                         # what the result it is recorded as would pay.
                         + row.get('walkover_points', 0)
                         + _points_adjustment(tournament, row['name'], rules))
        table.append(row)

    return _order(table, rules, beat)


def _entrant_name(reg):
    if reg.team_id:
        return getattr(reg.team, 'team_name', None) or f'Team {reg.team_id}'
    user = reg.user
    return getattr(user, 'full_name', None) or getattr(user, 'username', None) or f'Entrant {reg.id}'


def _order(table, rules, beat):
    """Sort by points, then by each tiebreaker in the organiser's order.

    Head to head is applied only between two teams that are otherwise level and
    have actually met. With three teams level it is undefined, so it is skipped
    rather than guessed at - a table that quietly invents an order is worse than
    one that falls through to the next tiebreaker.
    """
    order = rules.ordered_tiebreakers()

    def key(row):
        parts = [-row['points']]
        for name in order:
            if name == 'goal_difference':
                parts.append(-row['goal_difference'])
            elif name == 'goals_for':
                parts.append(-row['goals_for'])
            elif name == 'goals_against':
                parts.append(row['goals_against'])      # fewer is better
            elif name == 'wins':
                parts.append(-row['won'])
            elif name == 'fixtures_won':
                parts.append(-row['fixtures_won'])
            # head_to_head cannot be a sort key on one row; handled below.
        # Last resort so the order is stable rather than arbitrary run to run.
        parts.append(row['name'].lower())
        return parts

    table.sort(key=key)

    if 'head_to_head' in order:
        table = _apply_head_to_head(table, beat, order)

    for i, row in enumerate(table, start=1):
        row['position'] = i
    return table


def _apply_head_to_head(table, beat, order):
    """Swap two adjacent teams if one beat the other and nothing else separated them.

    Only for a pair. Three or more level on everything is left as the earlier
    tiebreakers ordered them, because "who beat whom" has no answer in a cycle.
    """
    where = order.index('head_to_head')
    # Everything ranked above head-to-head has already been applied, so two rows
    # are "otherwise level" when their points and those earlier keys all match.
    earlier = order[:where]

    def comparable(row):
        vals = [row['points']]
        for name in earlier:
            vals.append(row.get(name if name != 'wins' else 'won'))
        return tuple(vals)

    i = 0
    while i < len(table) - 1:
        a, b = table[i], table[i + 1]
        if comparable(a) == comparable(b):
            a_id, b_id = a['registration_id'], b['registration_id']
            if b_id in beat.get(a_id, set()) and a_id not in beat.get(b_id, set()):
                pass                       # a already ahead, correct
            elif a_id in beat.get(b_id, set()) and b_id not in beat.get(a_id, set()):
                table[i], table[i + 1] = b, a
                i += 1                     # do not re-compare the swapped pair
        i += 1
    return table


# ---------------------------------------------------------------------------
# The player table
# ---------------------------------------------------------------------------

def player_table(tournament):
    """One row per person, from their own games.

    Asked for directly: "for every tournament, there should always be two types
    of results if it's a team tournament, the team result/table, and then
    individual player results/table also."

    A player's points use the same rules as the team's, because an organiser who
    sets a win at 2 points means it everywhere in their competition.
    """
    rules = rules_for(tournament)
    rows = {}
    names = {}

    fixtures = (TieFixture.objects
                .filter(tie__tournament=tournament, status='completed')
                .select_related('player_1', 'player_2', 'tie'))

    for f in fixtures:
        for user, mine, theirs in (
            (f.player_1, f.goals_1, f.goals_2),
            (f.player_2, f.goals_2, f.goals_1),
        ):
            if user is None:
                continue          # a forfeited slot with nobody behind it
            row = rows.setdefault(user.pk, _blank_row())
            names[user.pk] = (getattr(user, 'full_name', None)
                              or getattr(user, 'username', None)
                              or f'Player {user.pk}')
            row['played'] += 1
            row['goals_for'] += mine
            row['goals_against'] += theirs
            if mine > theirs:
                row['won'] += 1
                row['fixtures_won'] += 1
            elif theirs > mine:
                row['lost'] += 1
            else:
                row['drawn'] += 1

    table = []
    for user_id, row in rows.items():
        row = dict(row)
        row['user_id'] = user_id
        row['name'] = names[user_id]
        row['goal_difference'] = row['goals_for'] - row['goals_against']
        row['points'] = (row['won'] * rules.points_win
                         + row['drawn'] * rules.points_draw
                         + row['lost'] * rules.points_loss)
        table.append(row)

    # No head to head between players: they may never have met, and the pairing
    # is by roster slot rather than by draw.
    return _order(table, rules, beat={})


# ---------------------------------------------------------------------------
# The rest of the numbers a real league keeps
# ---------------------------------------------------------------------------
#
# CEO, 2 September 2026, sending the CADE ESPORTS DIVISION 2 calculator: "these
# are basically the kind of stats any game about football should have like
# eafc, efootball, fcmobile etc ... BUILD THEM COMPLETE AND MAKE SURE IT
# CALCULATES INSIDE THE TOURNAMENT BRACKETS AUTOMATICALLY, BUT AN ORGANIZER
# SHOULD HAVE A CHOICE TO DECIDE HOW SOME METRICS ARE CALCULATED."
#
# The tables above already carry played, won, drawn, lost, goals and points.
# What a league actually keeps beyond that is clean sheets, averages, win rate,
# biggest win and loss, walkovers both ways, points per game and form - plus
# walkovers and cancellations as real match states, and deductions with the
# reason attached.
#
# Those are computed by `league_stats`, which has no Django in it and is
# checked column by column against the spreadsheet itself. They are MERGED onto
# the rows above rather than replacing them, so there is one table with more
# columns rather than two tables that can disagree - which is the fault this
# codebase keeps producing and the reason `tools/check-parity.py` exists.

from .. import league_stats as _stats


def stat_settings(tournament):
    """The organiser's answers, where a metric has more than one answer."""
    stored = (tournament.options or {}).get('league_stats') or {}
    settings, _errors = _stats.clean_settings(stored)
    # The points already set on the league win, so an organiser is never asked
    # the same question twice in two places.
    rules = rules_for(tournament)
    settings['points_win'] = rules.points_win
    settings['points_draw'] = rules.points_draw
    settings['points_loss'] = rules.points_loss
    return settings


_MATCH_STATE = {
    'completed': _stats.PLAYED,
    'walkover_p1': _stats.WALKOVER_HOME,
    'walkover_p2': _stats.WALKOVER_AWAY,
    'cancelled': _stats.CANCELLED,
}


def _team_matches(tournament):
    """The tie results, in the shape `league_stats` reads."""
    out = []
    ties = (BracketMatch.objects
            .filter(tournament=tournament)
            .select_related('participant_1__team', 'participant_1__user',
                            'participant_2__team', 'participant_2__user')
            .order_by('round_number', 'match_number'))
    for tie in ties:
        state = _MATCH_STATE.get(tie.status)
        if state is None:
            continue          # scheduled, disputed or a bye: not a result
        home = _entrant_name(tie.participant_1)
        away = _entrant_name(tie.participant_2)
        if not home or not away:
            continue
        out.append({'home': home, 'away': away,
                    'home_goals': tie.score_p1, 'away_goals': tie.score_p2,
                    'status': state})
    return out


def _player_matches(tournament):
    out = []
    fixtures = (TieFixture.objects
                .filter(tie__tournament=tournament, status='completed')
                .select_related('player_1', 'player_2', 'tie')
                .order_by('tie__round_number', 'slot'))
    for f in fixtures:
        if f.player_1 is None or f.player_2 is None:
            continue
        name = lambda u: (getattr(u, 'full_name', None)
                          or getattr(u, 'username', None) or 'Player %s' % u.pk)
        out.append({'home': name(f.player_1), 'away': name(f.player_2),
                    'home_goals': f.goals_1, 'away_goals': f.goals_2,
                    'status': _stats.PLAYED})
    return out


# Columns merged onto an existing row. Deliberately only the ones the tables
# above do NOT already compute, so nothing is calculated twice by two rules.
EXTRA = (
    'clean_sheets', 'average_goals_for', 'average_goals_against', 'win_rate',
    'biggest_win', 'biggest_loss', 'walkovers_given', 'walkovers_received',
    'points_per_game', 'form_points', 'form_score', 'form',
    'points_adjustment', 'adjustments',
)


def _merge(table, matches, tournament, key):
    settings = stat_settings(tournament)
    adjustments = (tournament.options or {}).get('league_adjustments') or []
    computed = {row['player']: row for row
                in _stats.table(matches, adjustments=adjustments,
                                settings=settings)}
    for row in table:
        extra = computed.get(row.get(key))
        if extra is None:
            continue
        for name in EXTRA:
            row[name] = extra[name]
    return table


def team_table_full(tournament):
    """The team table, with everything the spreadsheet keeps."""
    return _merge(team_table(tournament), _team_matches(tournament),
                  tournament, 'name')


def player_table_full(tournament):
    """The player table, with everything the spreadsheet keeps."""
    return _merge(player_table(tournament), _player_matches(tournament),
                  tournament, 'name')


def head_to_head(tournament, first, second):
    """The record between two entrants, as the sheet's Head2Head does."""
    return _stats.head_to_head(_team_matches(tournament) + _player_matches(tournament),
                               first, second, stat_settings(tournament))
