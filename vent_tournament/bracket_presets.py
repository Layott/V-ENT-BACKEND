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


def repechage(entrants):
    """Olympic wrestling's repechage (research catalogue 2.3): gold and silver
    by single elimination; in each half, whoever lost to that half's finalist
    is pulled back in, earliest round first, each match's winner meeting the
    next, the last of them the beaten semi-finalist for one of two bronzes.
    The seats wait for the finalists (`X:F:side:round`)."""
    size = max(8, next_power_of_2(entrants))
    rounds = size.bit_length() - 1
    matches = []
    sources = [_seed(p) for p in standard_seed_positions(size)]
    for rnd in range(1, rounds + 1):
        ids = []
        for i in range(len(sources) // 2):
            ident = 'F' if rnd == rounds else 'M%d_%d' % (rnd, i + 1)
            matches.append(_match(ident, rnd, sources[2 * i], sources[2 * i + 1],
                                  final=rnd == rounds))
            ids.append(ident)
        sources = [_w(i) for i in ids]
    for side in (1, 2):
        lost = lambda rnd: {'lost_to': 'F', 'side': side, 'round': rnd}  # noqa: E731
        current, at = lost(1), rounds
        for rnd in range(2, rounds):
            last = rnd == rounds - 1
            ident = 'BR%d' % side if last else 'RP%d_%d' % (side, rnd)
            matches.append(_match(ident, at, current, lost(rnd), side='placement',
                                  branch='repechage', places=(3, 5) if last else (None, None)))
            current, at = _w(ident), at + 1
    return {'matches': matches}


def _feed(groups, prefix, side, branch, matches, avail):
    """A bracket fed in waves (a losers bracket, the third bracket of a
    triple elimination, a consolation): `groups` are the waves of arrivals in
    order, each a list of sources. Survivors meet the next wave one to one;
    while there are more survivors than arrivals the survivors play each other
    first, and while there are more arrivals they do. Returns the source of
    the champion. `avail` says after which round each source is known."""
    count = [0]

    def play(a, b):
        count[0] += 1
        ident = '%s%d' % (prefix, count[0])
        rnd = max(avail[_key(a)], avail[_key(b)]) + 1
        matches.append(_match(ident, rnd, a, b, side=side, branch=branch))
        avail['W:' + ident] = rnd
        avail['L:' + ident] = rnd
        return _w(ident)

    def pair(items):
        out = [play(items[2 * i], items[2 * i + 1]) for i in range(len(items) // 2)]
        return out + ([items[-1]] if len(items) % 2 else [])

    survivors = []
    for wave in groups:
        arrivals = list(wave)
        if not survivors:
            survivors = arrivals
            continue
        while len(survivors) > len(arrivals) and len(survivors) > 1:
            survivors = pair(survivors)
        while len(arrivals) > len(survivors) and len(arrivals) > 1:
            arrivals = pair(arrivals)
        survivors = [play(a, b) for a, b in zip(survivors, arrivals)]
    while len(survivors) > 1:
        survivors = pair(survivors)
    return survivors[0]


def _key(source):
    if 'seed' in source:
        return 'S:%d' % source['seed']
    if 'winner' in source:
        return 'W:' + source['winner']
    return 'L:' + source['loser']


def triple_elimination(entrants):
    """Triple elimination (research catalogue 2.2): a first loss drops you
    to the second bracket, a second to the third, a third puts you out. The
    third bracket's champion must beat the second's twice (the replay `R:`
    is played only if the third bracket's champion wins the first match);
    the survivor meets the first bracket's champion in a grand final with one
    replay, as a double elimination's."""
    size = max(4, next_power_of_2(entrants))
    rounds = size.bit_length() - 1
    matches, avail = [], {}
    sources = [_seed(p) for p in standard_seed_positions(size)]
    for source in sources:
        avail[_key(source)] = 0
    upper_losers = []
    for rnd in range(1, rounds + 1):
        ids = []
        for i in range(len(sources) // 2):
            ident = 'U%d_%d' % (rnd, i + 1)
            matches.append(_match(ident, rnd, sources[2 * i], sources[2 * i + 1]))
            avail['W:' + ident] = avail['L:' + ident] = rnd
            ids.append(ident)
        upper_losers.append([_l(i) for i in ids])
        sources = [_w(i) for i in ids]
    upper_champion = sources[0]

    second_from = len(matches)
    second_champion = _feed(upper_losers, 'S', 'losers', 'second', matches, avail)
    waves = {}
    for m in matches[second_from:]:
        waves.setdefault(m['round'], []).append(_l(m['id']))
    third_champion = _feed([waves[r] for r in sorted(waves)], 'T', 'losers', 'third', matches, avail)

    ladder_at = max(avail[_key(second_champion)], avail[_key(third_champion)]) + 1
    matches.append(_match('LAD1', ladder_at, second_champion, third_champion, side='grand_final',
                          places=(None, 3)))
    matches.append(_match('LAD2', ladder_at + 1, {'reset': 'LAD1'}, {'reset': 'LAD1'},
                          side='grand_final', places=(None, 3)))
    final_at = max(avail[_key(upper_champion)], ladder_at + 1) + 1
    matches.append(_match('GF1', final_at, upper_champion, _w('LAD2'), side='grand_final',
                          places=(None, 2)))
    matches.append(_match('GF2', final_at + 1, {'reset': 'GF1'}, {'reset': 'GF1'},
                          side='grand_final', final=True))
    return {'matches': matches}


PRESETS = {
    'shaughnessy': shaughnessy,
    'afl_final_eight': afl_final_eight,
    'compass': compass,
    'feed_in_consolation': feed_in_consolation,
    'repechage': repechage,
    'triple_elimination': triple_elimination,
}

# The field sizes each preset is proven on (tests_p3_schema), and offered for.
SIZES = {
    'shaughnessy': (2, 3, 4),
    'afl_final_eight': (6, 7, 8),
    'compass': (4, 5, 8, 12, 16, 32, 64),
    'feed_in_consolation': (8, 12, 16, 24, 32, 64),
    'repechage': (8, 12, 16, 32, 64),
    'triple_elimination': (4, 6, 8, 12, 16, 32),
}
