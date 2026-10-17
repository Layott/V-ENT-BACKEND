"""Brackets written by a function for the field in front of them (P3 W2).

CEO, 16 October 2026, inbox 484, approved with the design: "The presets are
schemas produced by a function for N entrants, so a new preset is a function,
not a new engine." Each function here takes the number of entrants and returns
a schema in the shape `bracket_schema.check` accepts; the one compiler draws it,
and seeds above the field are byes. Sources: research catalogue sections 2.4
(feed-in consolation), 2.5 (compass draw), 2.6 (AFL final eight), 2.7
(Shaughnessy).
"""
from .services.bracket import next_power_of_2, standard_seed_positions

COMPASS = ('east', 'northeast', 'north', 'northwest', 'west', 'southwest', 'south', 'southeast')


def _match(ident, rnd, a, b, side='winners', branch='', final=False, places=(None, None)):
    return {'id': ident, 'round': rnd, 'side': side, 'branch': branch, 'final': final,
            'winner_place': places[0], 'loser_place': places[1], 'slots': [a, b]}


def _seed(n):
    return {'seed': n}


def _w(ident):
    return {'winner': ident}


def _l(ident):
    return {'loser': ident}


def shaughnessy(entrants):
    """The top four of a table: 1 v 4, 2 v 3, then a final (the NHL's
    Original Six era, Super League). Four seats; fewer entrants leave byes."""
    return {'matches': [
        _match('A', 1, _seed(1), _seed(4)),
        _match('B', 1, _seed(2), _seed(3)),
        _match('F', 2, _w('A'), _w('B'), final=True),
    ]}


def afl_final_eight(entrants):
    """The AFL's final eight (since 2000): qualifying finals 1 v 4 and 2 v 3,
    whose losers get a second chance; elimination finals 5 v 8 and 6 v 7,
    whose losers are out; semi-finals for the qualifying final losers against
    the elimination final winners; preliminary finals; the grand final. The
    2026 wildcard round is not part of it."""
    return {'matches': [
        _match('QF1', 1, _seed(1), _seed(4)),
        _match('QF2', 1, _seed(2), _seed(3)),
        _match('EF1', 1, _seed(5), _seed(8), side='losers'),
        _match('EF2', 1, _seed(6), _seed(7), side='losers'),
        _match('SF1', 2, _l('QF1'), _w('EF1'), side='losers'),
        _match('SF2', 2, _l('QF2'), _w('EF2'), side='losers'),
        _match('PF1', 3, _w('QF1'), _w('SF2')),
        _match('PF2', 3, _w('QF2'), _w('SF1')),
        _match('GF', 4, _w('PF1'), _w('PF2'), final=True),
    ]}


def compass(entrants):
    """A compass draw (USTA): everybody starts in the east; round one's
    winners stay east and its losers go west; every round splits each
    bracket again, so after the last round each of the eight directions has
    a winner and everybody has a place. Every entrant plays every round. The
    branch of a match is the direction of the places it is played for."""
    size = max(4, next_power_of_2(entrants))
    rounds = size.bit_length() - 1
    eighth = max(1, size // 8)
    matches = []

    def build(width, first, rnd, sources):
        """`width` sides, from `sources`, play for places first..first+width-1."""
        branch = COMPASS[min((first - 1) // eighth, 7)]
        ids = []
        for i in range(width // 2):
            ident = 'R%dP%dM%d' % (rnd, first, i + 1)      # at most 8 characters up to 64
            last = width == 2
            matches.append(_match(ident, rnd, sources[2 * i], sources[2 * i + 1], branch=branch,
                                  side='winners' if first == 1 else 'placement',
                                  final=last and first == 1,
                                  places=(first, first + 1) if last else (None, None)))
            ids.append(ident)
        if width == 2:
            return
        build(width // 2, first, rnd + 1, [_w(i) for i in ids])
        build(width // 2, first + width // 2, rnd + 1, [_l(i) for i in ids])

    build(size, 1, 1, [_seed(p) for p in standard_seed_positions(size)])
    assert max(m['round'] for m in matches) == rounds
    return {'matches': matches}


def feed_in_consolation(entrants):
    """The main draw is a knockout with a match for third; the losers of each
    round up to the quarter-finals are fed into a consolation bracket, whose
    winner is fifth and whose final's loser is sixth (USTA tournament director
    guide). Survivors of the consolation meet each round's new losers; when
    there are more survivors than newcomers they play each other first."""
    size = max(8, next_power_of_2(entrants))
    rounds = size.bit_length() - 1
    matches = []
    main = []
    sources = [_seed(p) for p in standard_seed_positions(size)]
    for rnd in range(1, rounds + 1):
        ids = []
        for i in range(len(sources) // 2):
            ident = 'M%d_%d' % (rnd, i + 1)
            final = rnd == rounds
            matches.append(_match(ident, rnd, sources[2 * i], sources[2 * i + 1], final=final,
                                  places=(1, 2) if final else (None, None)))
            ids.append(ident)
        main.append(ids)
        sources = [_w(i) for i in ids]
    matches.append(_match('THIRD', rounds, _l(main[rounds - 2][0]), _l(main[rounds - 2][1]),
                          side='placement', places=(3, 4)))

    counter = [0]

    def consolation(a, b, rnd):
        counter[0] += 1
        ident = 'C%d' % counter[0]
        matches.append(_match(ident, rnd, a, b, side='placement', branch='consolation'))
        return ident

    survivors, last_round = [], 1
    for rnd in range(1, rounds - 1):           # round one up to the quarter-finals
        newcomers = [_l(i) for i in main[rnd - 1]]
        play_at = max(rnd, last_round) + 1
        while len(survivors) > len(newcomers):
            survivors = [_w(consolation(survivors[2 * i], survivors[2 * i + 1], play_at))
                         for i in range(len(survivors) // 2)]
            play_at += 1
        if not survivors:
            survivors = [_w(consolation(newcomers[2 * i], newcomers[2 * i + 1], play_at))
                         for i in range(len(newcomers) // 2)]
        else:
            survivors = [_w(consolation(survivors[i], newcomers[i], play_at))
                         for i in range(len(newcomers))]
        last_round = play_at
    play_at = last_round + 1
    while len(survivors) > 2:
        survivors = [_w(consolation(survivors[2 * i], survivors[2 * i + 1], play_at))
                     for i in range(len(survivors) // 2)]
        play_at += 1
    final = consolation(survivors[0], survivors[1], play_at)
    for m in matches:
        if m['id'] == final:
            m['winner_place'], m['loser_place'] = 5, 6
    return {'matches': matches}


PRESETS = {
    'shaughnessy': shaughnessy,
    'afl_final_eight': afl_final_eight,
    'compass': compass,
    'feed_in_consolation': feed_in_consolation,
}

# The field sizes each preset is proven on (tests_p3_schema), and offered for.
SIZES = {
    'shaughnessy': (2, 3, 4),
    'afl_final_eight': (6, 7, 8),
    'compass': (4, 5, 8, 12, 16, 32, 64),
    'feed_in_consolation': (8, 12, 16, 24, 32, 64),
}
