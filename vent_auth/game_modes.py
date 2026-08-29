"""What each game is actually played as, and what a match of it looks like.

CEO, 29 August 2026, on the scrim form: "should be able to create solo
challenges also and depending on the game other options specific to that game
should pop up, like if its freefire thats selected, option of the mode like if
it'll be clash squad or battle royale, or craftland, then the format based off
the mode they picked etc, do detailed research for each game to find out how
these work."

The form offered Bo1/Bo3/Bo5 for every game on the platform. That is wrong
almost everywhere it is used:

- A Free Fire Battle Royale scrim is not "best of three". Twelve squads drop
  together and the result is points across a number of matches, so a "Bo3"
  option asks a question the mode cannot answer.
- Clash Squad IS round based, and its real shape is first-to-N rounds, not a
  best-of series of maps.
- Lone Wolf is fixed at first to 5 rounds out of 9 by the game itself. Offering
  Bo1 there invents a format Free Fire does not have.
- An EA FC tie between two people is two legs and an aggregate score, which is
  the format V-ENT already runs its EAFC league on.

So: one catalogue, per game, of the modes people actually compete in and the
formats that mode can be played to. Read from here by the scrim form, and
available to anything else that needs to know what a game looks like.

WHERE THIS COMES FROM

Researched on 29 August 2026 rather than recalled, because getting a mode name
wrong in a picker is the kind of error that makes a platform look like it was
built by somebody who does not play the game:

- Free Fire: Garena's 2026 esports roadmap (a standalone Clash Squad circuit
  arrives in March 2026, separate from FFWS), Garena's own Craftland site, and
  the Lone Wolf rules (9 rounds, first to 5, Iron Cage, 1v1 or 2v2). Craftland
  is Free Fire MAX only and maps are shared as a numeric map code, which is why
  that mode asks for one.
- PUBG Mobile: the competitive mode is Battle Royale, played as a number of
  matches with points carried across them. PMWC 2026 groups play 12 matches.
- Call of Duty: Mobile: 5v5, and the World Championship plays best-of-5 series
  with a pick-and-ban veto over a map and mode pool; Search and Destroy,
  Hardpoint and Domination are the competitive modes.
- EA SPORTS FC: FC Pro competition is 1v1 in Ultimate Team.
- Mobile Legends: 5v5 Draft Pick with a banning phase, played as a best-of
  series.

Anything not researched to that level is marked GENERIC below and gets the
plain best-of ladder, which is honest rather than invented.
"""

# Formats, defined once so the same words mean the same thing everywhere.
BEST_OF = ['Bo1', 'Bo3', 'Bo5', 'Bo7']

#: A battle royale is not a series. The result is points across N matches, so
#: the choice is how many matches are played, not how many wins ends it.
BR_MATCHES = ['1 match', '2 matches', '3 matches', '4 matches', '6 matches']

#: Clash Squad and Lone Wolf are decided by rounds inside one match.
ROUNDS_TO = ['First to 4 rounds', 'First to 5 rounds', 'First to 7 rounds']

#: Two legs and an aggregate, which is how V-ENT already runs EA FC ties.
FOOTBALL = ['Single match', 'Two legs, aggregate', 'Bo3']


def _mode(mode_id, label, sizes, formats, blurb='', asks=None):
    """One way a game is played.

    `sizes` is the number of players per side that the mode actually supports.
    It is what decides whether a solo challenge is possible: a mode with 1 in
    its sizes can be played alone, and one without it cannot, which is why the
    scrim form can stop offering a solo Clash Squad rather than accepting one
    and failing later.
    """
    return {
        'id': mode_id,
        'label': label,
        'sizes': sizes,
        'formats': formats,
        'blurb': blurb,
        # Extra things this mode needs before it can be played, asked for on
        # the form rather than left to the notes field.
        'asks': asks or [],
    }


GAME_MODES = {
    # ------------------------------------------------------------ Free Fire
    'Free Fire': [
        _mode('battle_royale', 'Battle Royale', [1, 2, 4], BR_MATCHES,
              'Squads drop on the same map. Placement and kills both score, '
              'and the result is the points across every match played.'),
        _mode('clash_squad', 'Clash Squad', [1, 2, 4], ROUNDS_TO,
              'Round based, buy your loadout each round. Whoever takes the '
              'agreed number of rounds first wins.'),
        _mode('lone_wolf', 'Lone Wolf', [1, 2], ['First to 5 rounds'],
              'The 1v1 arena on Iron Cage. Nine rounds, first to five, and '
              'the game fixes that, so there is nothing else to agree.'),
        _mode('craftland', 'Craftland', [1, 2, 4], ROUNDS_TO + BEST_OF,
              'A custom map. Free Fire MAX only, and whoever posts the scrim '
              'shares the map code.',
              asks=['map_code']),
    ],

    # --------------------------------------------------------- PUBG Mobile
    'PUBG Mobile': [
        _mode('battle_royale', 'Battle Royale', [1, 2, 4], BR_MATCHES,
              'The competitive mode. Points across the matches played, not a '
              'best-of series.'),
        _mode('tdm', 'Team Deathmatch', [4], ROUNDS_TO,
              'Warehouse. Quick, and useful for practising fights rather than '
              'rotations.'),
    ],

    # ------------------------------------------------- Call of Duty: Mobile
    'Call of Duty: Mobile': [
        _mode('search_and_destroy', 'Search and Destroy', [1, 5], ROUNDS_TO,
              'One side plants, the other defends, no respawns.'),
        _mode('hardpoint', 'Hardpoint', [5], BEST_OF,
              'Hold the moving objective.'),
        _mode('domination', 'Domination', [5], BEST_OF,
              'Capture and hold three points.'),
        _mode('tdm', 'Team Deathmatch', [1, 5], BEST_OF,
              'Straight kills.'),
    ],

    # ------------------------------------------------------------- Football
    # EA FC ships as a new title every year and the catalogue carries several.
    # The competitive shape does not change between them, so they share it.
    'EA FC': [
        _mode('ultimate_team', 'Ultimate Team (FC Pro)', [1], FOOTBALL,
              'One against one in Ultimate Team, which is what FC Pro plays.'),
        _mode('friendly', 'Friendly match', [1], FOOTBALL,
              'Any squad, agreed between the two of you.'),
        _mode('clubs', 'Clubs', [11], FOOTBALL,
              'Eleven a side, one player each.'),
    ],
    'eFootball': [
        _mode('online_match', 'Online match', [1], FOOTBALL, 'One against one.'),
    ],

    # ---------------------------------------------------------------- MOBAs
    'Mobile Legends: Bang Bang': [
        _mode('draft_pick', 'Draft Pick', [5], BEST_OF,
              'Bans then picks, which is how every tournament is played.'),
    ],

    # -------------------------------------------------------------- Fighting
    # One player each, and a set is counted in rounds and then in games.
    'Tekken 8': [
        _mode('versus', 'Versus', [1], BEST_OF, 'First to three rounds a game.'),
    ],
    'Street Fighter 6': [
        _mode('versus', 'Versus', [1], BEST_OF, 'First to two rounds a game.'),
    ],
}

#: Games in the catalogue with no researched mode list yet. They get the plain
#: best-of ladder and a single unnamed mode, which is honest: inventing mode
#: names for a game nobody here plays competitively is worse than saying
#: nothing, because a wrong name in a picker is read as a fact.
GENERIC = _mode('standard', 'Standard match', [1, 2, 3, 4, 5], BEST_OF, '')


def modes_for(game_title):
    """Every mode this game can be scrimmed in.

    Matched loosely on the title, because the catalogue carries `EA FC`,
    `EA FC 24` and `EA FC 25` as separate rows for the same game and they are
    all played the same way.
    """
    if not game_title:
        return [GENERIC]
    title = str(game_title).strip()
    if title in GAME_MODES:
        return GAME_MODES[title]
    for known, modes in GAME_MODES.items():
        if title.lower().startswith(known.lower()):
            return modes
    return [GENERIC]


def mode_for(game_title, mode_id):
    """One mode, or None when the id does not belong to this game.

    Used to refuse a mode that was not offered: a form can be edited before it
    is sent, and a scrim saved with a mode its game does not have is a scrim
    nobody can play.
    """
    for mode in modes_for(game_title):
        if mode['id'] == mode_id:
            return mode
    return None


def catalogue():
    """The whole thing, in the shape the form reads it."""
    out = {}
    for title in GAME_MODES:
        out[title] = modes_for(title)
    out['*'] = [GENERIC]
    return out
