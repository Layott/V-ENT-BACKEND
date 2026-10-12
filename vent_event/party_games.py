"""The party games an organiser can put on at an event (inbox 434, 439).

CEO, 12 October 2026: "research the most popular ... party games ... most
especially those popular and common in africa and then uk and then we can go
worldwide ... i want each to have an explainers and like an animation, that
can be easily understod by organizers or users". Approved as the mockup
V-ENT-FRONTEND/docs/mockups/party-games.html the same evening.

This module holds what the SERVER needs to know about a game: its key, where it
comes from, what kind it is, how many play, how long it takes, any age, whether
it is played in teams. Codes and numbers only. Every word (name, explainer,
steps, how to win, what you need) and every animation lives in the frontend
catalogue, src/lib/partyGames/catalogue.js, translated, because the reader may
be French. tools/check-party-games.py fails if the two lists of keys differ.

`None` for a maximum means "or more"; `None` for minutes means it runs as long
as the night does. An age of None is "set by the game being played" (a
fighting game's own rating).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PartyGame:
    key: str
    region: str          # africa | uk | world
    kind: str            # cards | board | moving | music | quiz | words | skill | video | social | numbers | acting | drawing | hands
    players_min: int
    players_max: int = None
    minutes_min: int = None
    minutes_max: int = None
    age: int = 0         # 0 all ages, else the youngest, None set by the game
    teams: bool = False


REGIONS = ('africa', 'uk', 'world')
KINDS = ('cards', 'board', 'moving', 'music', 'quiz', 'words', 'skill', 'video',
         'social', 'numbers', 'acting', 'drawing', 'hands')

G = PartyGame
GAMES = [
    # Africa (20)
    G('whot', 'africa', 'cards', 2, 8, 10, 20),
    G('ludo', 'africa', 'board', 2, 4, 20, 40),
    G('ayo', 'africa', 'board', 2, 2, 15, 30),
    G('draughts', 'africa', 'board', 2, 2, 10, 30),
    G('ten-ten', 'africa', 'moving', 2, None, 5, 15),
    G('ampe', 'africa', 'moving', 4, 20, 10, 20, teams=True),
    G('musical-chairs', 'africa', 'music', 6, 30, 10, 15),
    G('afro-dance-off', 'africa', 'music', 4, 30, 15, 30),
    G('finish-the-lyrics', 'africa', 'quiz', 4, 40, 15, 30, teams=True),
    G('guess-the-slang', 'africa', 'words', 4, 30, 15, 20, teams=True),
    G('name-animal-place-thing', 'africa', 'words', 2, 20, 10, 20),
    G('lemon', 'africa', 'words', 5, 20, 10, 10),
    G('fire-on-the-mountain', 'africa', 'moving', 10, 40, 10, 10),
    G('pilolo', 'africa', 'moving', 6, 30, 15, 15),
    G('morabaraba', 'africa', 'board', 2, 2, 15, 30),
    G('thirty-seconds', 'africa', 'words', 4, 16, 30, 60, teams=True),
    G('diketo', 'africa', 'skill', 2, 6, 10, 10),
    G('kgati', 'africa', 'moving', 3, None, 10, 10),
    G('winner-stays-on', 'africa', 'video', 4, 30),
    G('king-of-the-hill', 'africa', 'video', 4, 30, age=None),
    # United Kingdom (12)
    G('pass-the-parcel', 'uk', 'music', 6, 30, 10, 10),
    G('musical-statues', 'uk', 'music', 5, 40, 10, 10),
    G('wink-murder', 'uk', 'social', 8, 25, 15, 15),
    G('sardines', 'uk', 'moving', 6, 20, 15, 15),
    G('quiz-night', 'uk', 'quiz', 4, 120, 60, 90, teams=True),
    G('articulate', 'uk', 'words', 4, 20, 30, 60, teams=True),
    G('herd-mentality', 'uk', 'social', 4, 20, 20, 20),
    G('egg-and-spoon', 'uk', 'moving', 4, 20, 5, 5),
    G('sack-race', 'uk', 'moving', 4, 20, 5, 5),
    G('bingo', 'uk', 'numbers', 10, 500, 20, 40),
    G('simon-says', 'uk', 'moving', 5, 50, 10, 10),
    G('charades', 'uk', 'acting', 4, 30, 20, 40, teams=True),
    # Worldwide (18)
    G('uno', 'world', 'cards', 2, 10, 15, 30),
    G('jenga', 'world', 'skill', 2, 8, 10, 20),
    G('pictionary', 'world', 'drawing', 4, 20, 20, 40, teams=True),
    G('taboo', 'world', 'words', 4, 20, 20, 40, teams=True),
    G('codenames', 'world', 'words', 4, 8, 20, 20, teams=True),
    G('mafia', 'world', 'social', 7, 20, 20, 40, age=13),
    G('two-truths', 'world', 'social', 3, 20, 10, 20),
    G('never-have-i-ever', 'world', 'social', 4, 20, 15, 15, age=16),
    G('truth-or-dare', 'world', 'social', 4, 15, 20, 20, age=16),
    G('tug-of-war', 'world', 'moving', 8, 20, 5, 5, teams=True),
    G('twister', 'world', 'moving', 2, 4, 10, 10),
    G('karaoke', 'world', 'music', 4, 30, 30, 60),
    G('limbo', 'world', 'moving', 5, 30, 10, 10),
    G('rock-paper-scissors', 'world', 'hands', 8, 128, 15, 30),
    G('red-light-green-light', 'world', 'moving', 6, 50, 10, 10),
    G('cup-pong', 'world', 'skill', 2, 4, 15, 15, teams=True),
    G('just-dance', 'world', 'video', 1, 6, 15, 30),
    G('mario-kart', 'world', 'video', 2, 4, 10, 20),
]
del G

BY_KEY = {g.key: g for g in GAMES}


def get(key):
    """The catalogue game for `key`, or None."""
    return BY_KEY.get(str(key or '').strip().lower())


def as_json(game):
    return {
        'key': game.key,
        'region': game.region,
        'kind': game.kind,
        'players_min': game.players_min,
        'players_max': game.players_max,
        'minutes_min': game.minutes_min,
        'minutes_max': game.minutes_max,
        'age': game.age,
        'teams': game.teams,
    }


def catalogue():
    return [as_json(g) for g in GAMES]
