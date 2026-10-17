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


PRESETS = {'ffa_bracket': ffa_bracket}
