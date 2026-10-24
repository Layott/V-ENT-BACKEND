"""Plans written by a function (P3 W1, design section 11).

CEO, 16 October 2026, inbox 484: "i also need to implement new brackets,
formats and modes", approved with the design, whose rule for new shapes is "a
new preset is a function, not a new engine". A function here returns a whole
stage plan in the shape `stages.plan` and `progressions.clean` check, which the
stage planner loads as a draft for the organiser to change and save. Nothing is
stored by asking.

Labels are left to the screen, which names each stage in the organiser's
language from `kind` and `round`: a sentence built here could not be
translated.
"""
import math

from . import br_engine, stage_settings


class PresetError(ValueError):
    def __init__(self, code, field):
        super().__init__(code)
        self.code = code
        self.field = field


def _whole(raw, field, low, high, default=None):
    if raw in (None, '') and default is not None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise PresetError('NOT_A_NUMBER', field)
    if value < low or value > high:
        raise PresetError('OUT_OF_RANGE', field)
    return value


def _sent(entrants, lobbies, through):
    """How many a round of lobbies sends on: the top `through` of each lobby,
    the lobbies dealt by snake (sizes differ by one at most)."""
    small, extra = divmod(entrants, lobbies)
    sizes = [small + 1] * extra + [small] * (lobbies - extra)
    return sum(min(through, size) for size in sizes)


def ffa_bracket(entrants, lobby_size, through, maps=4, final_maps=None, game_title=None):
    """A free for all bracket: rounds of lobbies, the top `through` of each
    lobby going on, down to one final lobby (toornament's FFA bracket,
    research section 6: "match_size players a match, match_winners go
    through").

    Each round is a battle royale stage that sends on by lobby, rank by rank
    across lobbies (1A, 1B, 2A, 2B), and the next stage seats them by snake,
    so the winners of two lobbies land in different lobbies. Returns
    {stages, lines, rounds}: stages in the shape `stages.plan` checks, lines
    in the shape `progressions.clean` checks, and per round how many enter
    and how many lobbies they fill.
    """
    entrants = _whole(entrants, 'entrants', 2, 5000)
    lobby_size = _whole(lobby_size, 'lobby_size', 2, 100)
    through = _whole(through, 'through', 1, lobby_size - 1)
    maps = _whole(maps, 'maps', 1, 30, 4)
    final_maps = _whole(final_maps, 'final_maps', 1, 30, maps)
    base = stage_settings.br_game_defaults(game_title)
    base.pop('lobby_size', None)

    rounds = []
    n = entrants
    while True:
        lobbies = br_engine.lobby_count(n, lobby_size)
        rounds.append({'entrants': n, 'lobbies': lobbies})
        if lobbies == 1:
            break
        sent = _sent(n, lobbies, through)
        if sent >= n:
            raise PresetError('FFA_NEVER_NARROWS', 'through')
        n = sent

    out_stages = []
    for index, rnd in enumerate(rounds):
        final = index == len(rounds) - 1
        settings = dict(base, maps=final_maps if final else maps,
                        # The final seats everybody left in one lobby.
                        lobby_size=rnd['entrants'] if final else lobby_size)
        if not final:
            settings['advance_by'] = 'lobby'
        out_stages.append({
            'format': 'battle_royale',
            'kind': 'final' if final else 'round',
            'round': index + 1,
            'advances': 0 if final else through,
            'groups': 0,
            'settings': settings,
        })
    lines = [{'source': i, 'target': i + 1, 'group': None, 'place_from': 1,
              'place_to': through, 'side': 'any', 'carry_points': False,
              'fire': 'after_preview', 'order': i}
             for i in range(len(out_stages) - 1)]
    return {'stages': out_stages, 'lines': lines, 'rounds': rounds}


def split_league(entrants, legs, top, game_title=None):
    """A split league: everybody plays one league, then its top `top` places
    play on in a top league and the rest in a bottom league, each carrying the
    points it won in the first. The top league sits above the bottom whatever
    the points, which `progressions.placement_order` already gives.

    Each league is a round robin, not a league: the format catalogue lets a
    round robin feed another round robin, but a league feeds only knockouts,
    so a league could not carry places into a league. Returns {stages, lines,
    rounds} in the shape `ffa_bracket` returns them. `game_title` is taken so
    every preset is called the same way; a league has no game settings.
    """
    # Every league needs three to play, so each side of the split does too.
    entrants = _whole(entrants, 'entrants', 6, 5000)
    legs = _whole(legs, 'legs', 1, 2, 2)
    top = _whole(top, 'top', 3, entrants - 3)
    # The first league sends everybody on, the top `top` up and the rest down.
    out_stages = [
        {'format': 'round_robin', 'kind': 'round', 'round': 1, 'advances': entrants,
         'groups': 0, 'settings': {'legs': legs}},
        {'format': 'round_robin', 'kind': 'top', 'round': 2, 'advances': 0, 'groups': 0,
         'settings': {'legs': legs}},
        {'format': 'round_robin', 'kind': 'bottom', 'round': 2, 'advances': 0, 'groups': 0,
         'settings': {'legs': legs}},
    ]
    lines = [
        {'source': 0, 'target': 1, 'group': None, 'place_from': 1, 'place_to': top,
         'side': 'any', 'carry_points': True, 'fire': 'after_preview', 'order': 0},
        {'source': 0, 'target': 2, 'group': None, 'place_from': top + 1,
         'place_to': entrants, 'side': 'any', 'carry_points': True,
         'fire': 'after_preview', 'order': 1},
    ]
    rounds = [{'entrants': entrants}, {'entrants': top}, {'entrants': entrants - top}]
    return {'stages': out_stages, 'lines': lines, 'rounds': rounds}


def swiss_top_cut(entrants, rounds, cut, game_title=None):
    """A Swiss with a top cut: everybody plays a fixed number of Swiss rounds,
    then the top `cut` places play a single elimination bracket. Card games
    (Magic, Pokemon, Yu-Gi-Oh) run it this way.

    The Swiss has no early exit: a player is not sent out after a set number
    of losses, and nobody is sent through before the last round, so every
    entrant plays `rounds` rounds. `rounds` defaults to the fewest rounds that
    can separate the field down to one winner. `cut` is 4, 8, 16 or 32 and
    must be smaller than the field. Returns {stages, lines, rounds} in the
    shape `split_league` returns them. `game_title` is taken so every preset
    is called the same way; a Swiss has no game settings.
    """
    entrants = _whole(entrants, 'entrants', 8, 5000)
    cut = _whole(cut, 'cut', 4, 32, 8)
    if cut not in (4, 8, 16, 32) or cut >= entrants:
        raise PresetError('OUT_OF_RANGE', 'cut')
    rounds = _whole(rounds, 'rounds', 3, 15, math.ceil(math.log2(entrants)))
    # The Swiss sends the top `cut` up and nobody else, the bracket plays them.
    out_stages = [
        {'format': 'swiss', 'kind': 'round', 'round': 1, 'advances': cut,
         'groups': 0, 'settings': {'rounds': rounds},
         # Card games break a tie on opponents' match win percentage, then
         # their opponents' (research catalogue 4.5).
         'rules': {'tiebreakers': ['opponents_win_pct', 'opp_opp_win_pct']}},
        {'format': 'single_elimination', 'kind': 'top', 'round': 2, 'advances': 0,
         'groups': 0, 'settings': {}},
    ]
    lines = [
        {'source': 0, 'target': 1, 'group': None, 'place_from': 1, 'place_to': cut,
         'side': 'any', 'carry_points': False, 'fire': 'after_preview', 'order': 0},
    ]
    rounds_out = [{'entrants': entrants}, {'entrants': cut}]
    return {'stages': out_stages, 'lines': lines, 'rounds': rounds_out}


PRESETS = {'ffa_bracket': ffa_bracket, 'split_league': split_league,
           'swiss_top_cut': swiss_top_cut}
# The query parameters each preset reads, in the order its function takes them.
PARAMS = {'ffa_bracket': ('entrants', 'lobby_size', 'through', 'maps', 'final_maps'),
          'split_league': ('entrants', 'legs', 'top'),
          'swiss_top_cut': ('entrants', 'rounds', 'cut')}
