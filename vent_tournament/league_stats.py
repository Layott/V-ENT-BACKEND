"""The league table, computed from the matches and nothing else.

Built from `CADE ESPORTS DIVISION 2 CALCULATOR 2026`, a real league the CEO
runs: 19 matchdays, 12 players, 189 fixtures. Every column below is one
somebody already keeps by hand in a spreadsheet, which is the whole argument
for computing it here instead.

    CEO, 2 September 2026: "BUILD THEM COMPLETE AND MAKE SURE IT CALCULATES
    INSIDE THE TOURNAMENT BRACKETS AUTOMATICALLY, BUT AN ORGANIZER SHOULD HAVE
    A CHOICE TO DECIDE HOW SOME METRICS ARE CALCULATED, THE ONES THAT COULD
    HAVE SEVERAL WAYS IN WHICH IT COULD BE CALCULATED."

Both halves matter, and the second is the harder one.

**Nothing here is typed in.** Every number is derived from the fixtures. A
table an organiser can edit is a table that disagrees with the results, and
the argument that follows is unwinnable because both sides have a number.

**Where a metric has more than one defensible definition, the organiser
chooses.** Where it has one, it does not: offering a choice about something
with a single correct answer is noise, and noise in a settings screen is how
the real settings stop being read. Each choice below is one the spreadsheet
either leaves open or answers in a way another league would reasonably answer
differently.

---

## How a walkover works, which is most of the subtlety

The spreadsheet treats a walkover as a real result with notional goals. It
records the winner 3 and the loser 0 (both configurable), and because those
land in the effective-goals columns they flow into goals for, goals against,
goal difference, margins and clean sheets exactly as played goals do. The
match counts as played.

That is one defensible reading and it is CADE's. Another league counts the
points and refuses to let invented goals touch a goal difference that decides
the title. Both are right; only the organiser knows which league this is. So
`walkover_goals_count`, `walkover_counts_as_played` and
`clean_sheet_includes_walkover` are settings, defaulting to what CADE does.

## Adjustments

The Adjustments sheet is a per-player, per-metric correction with a reason:

    WOLEVATION   GF   -3   "Stood up mid match and quit, decided to leave."

A deduction is a decision somebody has to defend later, so the reason is
stored on the row rather than remembered. Adjustments reach MP, W, D, L, GF,
GA, GD and PTS, and adjusting W, D or L also moves the points those results
are worth, which is what the spreadsheet's TotalPts formula does.
"""
from collections import defaultdict


# What a match can be. `cancelled` counts for nothing anywhere: not played,
# no goals, no points. It is not a draw.
PLAYED = 'played'
WALKOVER_HOME = 'walkover_home'
WALKOVER_AWAY = 'walkover_away'
CANCELLED = 'cancelled'
STATUSES = (PLAYED, WALKOVER_HOME, WALKOVER_AWAY, CANCELLED)

# Metrics an adjustment may touch, matching the spreadsheet's Adj_ columns.
ADJUSTABLE = ('MP', 'W', 'D', 'L', 'GF', 'GA', 'GD', 'PTS')

WIN_RATE_WINS = 'wins'
WIN_RATE_WITH_DRAWS = 'wins_and_half_draws'
BIGGEST_BY_MARGIN = 'margin'
BIGGEST_BY_GOALS = 'goals_scored'


# The organiser's settings. Defaults are CADE's, so a league that changes
# nothing behaves exactly like the spreadsheet it came from.
DEFAULTS = {
    # Points. One correct answer per league, no ambiguity, but every league
    # picks its own numbers.
    'points_win': 3,
    'points_draw': 1,
    'points_loss': 0,

    # A walkover, which the spreadsheet scores separately from a win because
    # a league may want to reward turning up differently from winning.
    'walkover_points_winner': 3,
    'walkover_points_loser': 0,
    'walkover_goals_winner': 3,
    'walkover_goals_loser': 0,

    # --- the genuine choices -------------------------------------------
    #
    # Do those notional goals reach the goal columns? CADE says yes. A league
    # whose title is decided on goal difference may refuse to let a walkover
    # move it.
    'walkover_goals_count': True,

    # Does a walkover count as a match played? CADE says yes, which makes
    # averages and points-per-game include it.
    'walkover_counts_as_played': True,

    # Is a 3-0 walkover a clean sheet? It falls out of CADE's arithmetic, and
    # a league that thinks a clean sheet is something you earn on the pitch
    # will say no.
    'clean_sheet_includes_walkover': True,

    # Win rate. CADE uses wins over played. The other common definition gives
    # a draw half credit, which changes who leads a tight table.
    'win_rate_method': WIN_RATE_WINS,

    # Form. CADE looks at the last five and scores it out of 15, which is
    # five wins at three points - so the 15 is only right while a win is worth
    # three. The window is the setting; the maximum is derived from it, which
    # is a small correction to the sheet rather than a choice.
    'form_window': 5,

    # Biggest win. By margin, or by how many were scored. A 7-0 and a 9-2 are
    # a different answer depending which you mean.
    'biggest_win_method': BIGGEST_BY_MARGIN,
}


def clean_settings(raw):
    """The organiser's settings, with anything unknown or invalid refused.

    Returns `(settings, errors)`. Silently substituting a default for a value
    somebody deliberately chose is how a table ends up disagreeing with the
    rules the league was told.
    """
    settings = dict(DEFAULTS)
    errors = []
    raw = raw or {}

    for key, value in raw.items():
        if key not in DEFAULTS:
            errors.append('%s is not a setting.' % key)
            continue

        default = DEFAULTS[key]

        if isinstance(default, bool):
            if isinstance(value, bool):
                settings[key] = value
            elif str(value).strip().lower() in ('1', 'true', 'yes', 'on'):
                settings[key] = True
            elif str(value).strip().lower() in ('0', 'false', 'no', 'off'):
                settings[key] = False
            else:
                errors.append('%s is yes or no.' % key)
            continue

        if key == 'win_rate_method':
            if value in (WIN_RATE_WINS, WIN_RATE_WITH_DRAWS):
                settings[key] = value
            else:
                errors.append('%s is %s or %s.'
                              % (key, WIN_RATE_WINS, WIN_RATE_WITH_DRAWS))
            continue

        if key == 'biggest_win_method':
            if value in (BIGGEST_BY_MARGIN, BIGGEST_BY_GOALS):
                settings[key] = value
            else:
                errors.append('%s is %s or %s.'
                              % (key, BIGGEST_BY_MARGIN, BIGGEST_BY_GOALS))
            continue

        try:
            number = int(value)
        except (TypeError, ValueError):
            errors.append('%s has to be a whole number.' % key)
            continue

        if key == 'form_window' and number < 1:
            errors.append('form_window has to be at least 1.')
            continue
        if key.endswith('_goals_winner') or key.endswith('_goals_loser'):
            if number < 0:
                errors.append('%s cannot be negative.' % key)
                continue
        settings[key] = number

    return settings, errors


def _blank():
    return {
        'played': 0, 'wins': 0, 'draws': 0, 'losses': 0,
        'goals_for': 0, 'goals_against': 0,
        'points_from_matches': 0,
        'clean_sheets': 0,
        'walkovers_given': 0, 'walkovers_received': 0,
        'biggest_win': 0, 'biggest_loss': 0,
        'results': [],          # newest last: points earned, for form
    }


def effective(match, settings):
    """What the match is worth, after a walkover is turned into a scoreline.

    Returns `(home_goals, away_goals, counts_as_played, is_walkover)`, or
    `None` for a match that counts for nothing.
    """
    status = match.get('status') or PLAYED

    if status == CANCELLED:
        return None

    if status in (WALKOVER_HOME, WALKOVER_AWAY):
        winner_goals = settings['walkover_goals_winner']
        loser_goals = settings['walkover_goals_loser']
        if status == WALKOVER_HOME:
            home, away = winner_goals, loser_goals
        else:
            home, away = loser_goals, winner_goals
        if not settings['walkover_goals_count']:
            home = away = None
        return home, away, settings['walkover_counts_as_played'], True

    home = match.get('home_goals')
    away = match.get('away_goals')
    if home is None or away is None:
        # Not played yet. Not a draw, not a loss, simply not counted.
        return None
    return int(home), int(away), True, False


def table(matches, adjustments=None, settings=None, tiebreakers=None):
    """The standings, and every per-player number the spreadsheet keeps.

    `matches` are dicts of home, away, home_goals, away_goals, status. Order
    matters only for form, which reads the last N as given.
    """
    settings = settings if settings is not None else dict(DEFAULTS)
    rows = defaultdict(_blank)

    for match in matches:
        home_name = match.get('home')
        away_name = match.get('away')
        if not home_name or not away_name:
            continue

        # Everybody named appears in the table even before they play, so a
        # league does not look half empty on its first evening.
        rows[home_name]
        rows[away_name]

        result = effective(match, settings)
        if result is None:
            continue
        home_goals, away_goals, counts_as_played, is_walkover = result

        home = rows[home_name]
        away = rows[away_name]

        if is_walkover:
            winner, loser = ((home, away) if match['status'] == WALKOVER_HOME
                             else (away, home))
            winner['walkovers_received'] += 1
            loser['walkovers_given'] += 1
            winner_points = settings['walkover_points_winner']
            loser_points = settings['walkover_points_loser']
            winner['points_from_matches'] += winner_points
            loser['points_from_matches'] += loser_points
            if counts_as_played:
                winner['played'] += 1
                loser['played'] += 1
                winner['wins'] += 1
                loser['losses'] += 1
                winner['results'].append(winner_points)
                loser['results'].append(loser_points)
        else:
            home['played'] += 1
            away['played'] += 1
            if home_goals > away_goals:
                home['wins'] += 1
                away['losses'] += 1
                home_points = settings['points_win']
                away_points = settings['points_loss']
            elif home_goals < away_goals:
                away['wins'] += 1
                home['losses'] += 1
                home_points = settings['points_loss']
                away_points = settings['points_win']
            else:
                home['draws'] += 1
                away['draws'] += 1
                home_points = away_points = settings['points_draw']
            home['points_from_matches'] += home_points
            away['points_from_matches'] += away_points
            home['results'].append(home_points)
            away['results'].append(away_points)

        # Goals, margins and clean sheets, for any match that produced a
        # scoreline. A walkover with walkover_goals_count off produces none.
        if home_goals is None or away_goals is None:
            continue

        home['goals_for'] += home_goals
        home['goals_against'] += away_goals
        away['goals_for'] += away_goals
        away['goals_against'] += home_goals

        if not is_walkover or settings['clean_sheet_includes_walkover']:
            if away_goals == 0:
                home['clean_sheets'] += 1
            if home_goals == 0:
                away['clean_sheets'] += 1

        by_goals = settings['biggest_win_method'] == BIGGEST_BY_GOALS
        if home_goals > away_goals:
            home['biggest_win'] = max(
                home['biggest_win'],
                home_goals if by_goals else home_goals - away_goals)
            away['biggest_loss'] = max(
                away['biggest_loss'],
                home_goals if by_goals else home_goals - away_goals)
        elif away_goals > home_goals:
            away['biggest_win'] = max(
                away['biggest_win'],
                away_goals if by_goals else away_goals - home_goals)
            home['biggest_loss'] = max(
                home['biggest_loss'],
                away_goals if by_goals else away_goals - home_goals)

    # Adjustments, applied on top. Kept apart from the match totals all the way
    # through, so a table can always say which part of a score was earned and
    # which was awarded or deducted.
    corrections = defaultdict(lambda: {m: 0 for m in ADJUSTABLE})
    reasons = defaultdict(list)
    for row in (adjustments or []):
        player = row.get('player')
        metric = str(row.get('metric') or '').upper()
        if not player or metric not in ADJUSTABLE:
            continue
        try:
            value = int(row.get('value') or 0)
        except (TypeError, ValueError):
            continue
        rows[player]
        corrections[player][metric] += value
        reasons[player].append({
            'metric': metric, 'value': value,
            'reason': str(row.get('reason') or '').strip(),
        })

    out = []
    for name, row in rows.items():
        adj = corrections[name]

        played = row['played'] + adj['MP']
        wins = row['wins'] + adj['W']
        draws = row['draws'] + adj['D']
        losses = row['losses'] + adj['L']
        goals_for = row['goals_for'] + adj['GF']
        goals_against = row['goals_against'] + adj['GA']
        goal_difference = (goals_for - goals_against) + adj['GD']

        # The spreadsheet's TotalPts: points earned, plus what the adjusted
        # results are worth, plus a direct points adjustment.
        points = (row['points_from_matches']
                  + adj['W'] * settings['points_win']
                  + adj['D'] * settings['points_draw']
                  + adj['L'] * settings['points_loss']
                  + adj['PTS'])

        window = settings['form_window']
        recent = row['results'][-window:]
        # Out of what a perfect run in that window is worth, rather than the
        # sheet's hardcoded 15, which is only right while a win is 3.
        best_possible = window * settings['points_win']
        last_points = sum(recent)

        if settings['win_rate_method'] == WIN_RATE_WITH_DRAWS:
            earned = wins + (draws / 2)
        else:
            earned = wins

        out.append({
            'player': name,
            'played': played,
            'wins': wins,
            'draws': draws,
            'losses': losses,
            'goals_for': goals_for,
            'goals_against': goals_against,
            'goal_difference': goal_difference,
            'points_from_matches': row['points_from_matches'],
            'points_adjustment': points - row['points_from_matches'],
            'points': points,
            'clean_sheets': row['clean_sheets'],
            'average_goals_for': (goals_for / played) if played else None,
            'average_goals_against': (goals_against / played) if played else None,
            'win_rate': (earned / played) if played else None,
            'biggest_win': row['biggest_win'],
            'biggest_loss': row['biggest_loss'],
            'walkovers_given': row['walkovers_given'],
            'walkovers_received': row['walkovers_received'],
            'points_per_game': (points / played) if played else None,
            'form_points': last_points,
            'form_score': (round(100 * last_points / best_possible, 2)
                           if best_possible else None),
            'form': recent,
            'adjustments': reasons.get(name, []),
        })

    return order(out, tiebreakers)


def order(rows, tiebreakers=None):
    """Sort, and stamp each row with its position.

    Default order is the spreadsheet's: points, goal difference, goals for,
    then the name so that two players who are genuinely level do not swap
    places every time the page is loaded.
    """
    keys = list(tiebreakers or ('goal_difference', 'goals_for'))

    def sort_key(row):
        parts = [-row['points']]
        for key in keys:
            value = row.get(key)
            if key == 'head_to_head':
                # Not decidable from the table alone; it needs the fixtures
                # between the two, so it is handled by the caller that has
                # them and skipped here rather than silently ignored.
                continue
            parts.append(-(value if isinstance(value, (int, float)) else 0))
        parts.append(str(row['player']).lower())
        return parts

    ordered = sorted(rows, key=sort_key)
    for index, row in enumerate(ordered, 1):
        row['position'] = index
    return ordered


def head_to_head(matches, first, second, settings=None):
    """The record between two players, as the sheet's Head2Head does."""
    settings = settings if settings is not None else dict(DEFAULTS)
    between = [
        m for m in matches
        if {m.get('home'), m.get('away')} == {first, second}
    ]
    rows = table(between, settings=settings)
    by_name = {row['player']: row for row in rows}
    return {
        'matches': len(between),
        first: by_name.get(first),
        second: by_name.get(second),
    }
