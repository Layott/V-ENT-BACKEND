"""Swiss engines and the tie-break catalogue of P3 W1 (design sections 9 and 11).

CEO, 16 October 2026, inbox 484, approved with the design. Section 11: "Swiss |
yes, one pairing method ... | P3 W1 engines Dutch, Monrad, Valve and the tie-break
catalogue" and "Valve Major Swiss | partly | P3 W1 preset with decisive matches
best of 3". The rules come from the research catalogue (sections 4.2 to 4.5):
FIDE's Dutch system pairs the top half of a score group against its bottom
half, Monrad pairs 1 v 2, 3 v 4 (what V-ENT has drawn so far), Valve pairs the
highest against the lowest of a record; FIDE's tie-breaks (Buchholz with its
cut and median, Sonneborn-Berger, progressive score, Koya) and the card game
opponents' win percentage with its floor.
"""
import itertools
from types import SimpleNamespace

from django.test import TestCase

from . import ranking, stage_engine, stage_settings
from .services import bracket as bracket_service
from .tests_stage_engine import add_stage, draw, field, play, play_out


def P(i):
    return SimpleNamespace(id=i)


def chalk_after_round_one(method):
    """Eight entrants after round one went to the better seed (1v5 ... 4v8):
    the round-two pairing, as seed pairs."""
    players = [P(i) for i in (1, 2, 3, 4, 5, 6, 7, 8)]
    record = {i: ((1, 0) if i <= 4 else (0, 1)) for i in range(1, 9)}
    played = {frozenset((i, i + 4)) for i in range(1, 5)}
    pairs, bye = bracket_service.pair_swiss(players, record, played, set(), method=method)
    return sorted(tuple(sorted((a.id, b.id))) for a, b in pairs), bye


class PairingEngineTests(TestCase):
    def test_monrad_pairs_one_against_two(self):
        self.assertEqual(chalk_after_round_one('monrad')[0], [(1, 2), (3, 4), (5, 6), (7, 8)])

    def test_dutch_pairs_the_top_half_against_the_bottom_half(self):
        self.assertEqual(chalk_after_round_one('dutch')[0], [(1, 3), (2, 4), (5, 7), (6, 8)])

    def test_valve_pairs_the_highest_against_the_lowest(self):
        self.assertEqual(chalk_after_round_one('valve')[0], [(1, 4), (2, 3), (5, 8), (6, 7)])

    def test_monrad_is_what_was_drawn_before(self):
        players = [P(i) for i in range(1, 9)]
        record = {i: ((1, 0) if i <= 4 else (0, 1)) for i in range(1, 9)}
        played = {frozenset((i, i + 4)) for i in range(1, 5)}
        self.assertEqual(
            [(a.id, b.id) for a, b in bracket_service.pair_swiss(players, record, played, set())[0]],
            [(a.id, b.id) for a, b in bracket_service.pair_swiss(players, record, played, set(),
                                                                method='monrad')[0]])

    def test_the_setting(self):
        self.assertEqual(stage_settings.clean('swiss', {})['pairing'], 'monrad')
        self.assertEqual(stage_settings.clean('swiss', {'pairing': 'Dutch'})['pairing'], 'dutch')
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('swiss', {'pairing': 'burstein'})
        self.assertEqual((caught.exception.code, caught.exception.field), ('UNKNOWN_CHOICE', 'pairing'))

    def test_a_stage_pairs_with_its_engine(self):
        t, creator, _regs = field(8)
        swiss = add_stage(t, 0, 'swiss', settings={'rounds': 3, 'pairing': 'dutch'})
        draw(swiss, creator)
        for m in swiss.matches.filter(round_number=1):
            low = min(m.participant_1.seed, m.participant_2.seed)
            play(m, 2, 0) if m.participant_1.seed == low else play(m, 0, 2)
        second = sorted(tuple(sorted((m.participant_1.seed, m.participant_2.seed)))
                        for m in swiss.matches.filter(round_number=2))
        self.assertEqual(second, [(1, 3), (2, 4), (5, 7), (6, 8)])


def _perfect_free_matching_exists(ids, played):
    if not ids:
        return True
    first, rest = ids[0], ids[1:]
    return any(frozenset((first, o)) not in played
               and _perfect_free_matching_exists([x for x in rest if x != o], played)
               for o in rest)


class NoNeedlessRematchTests(TestCase):
    """Design section 17: "Swiss repeats a pairing only when no other pairing
    exists". Every result pattern of the first two rounds, for six and eight
    entrants, under all three engines."""

    def simulate(self, n, method):
        checked = 0
        matches_per_round = n // 2
        for outcomes in itertools.product((0, 1), repeat=matches_per_round * 2):
            wins = {i: 0 for i in range(1, n + 1)}
            losses = dict(wins)
            played = set()
            order = list(range(1, n + 1))
            half = n // 2
            pairs = [(order[i], order[i + half]) for i in range(half)]
            for rnd in range(3):
                for pair in pairs:
                    played.add(frozenset(pair))
                if rnd == 2:
                    break
                for k, (a, b) in enumerate(pairs):
                    w, l = (a, b) if outcomes[rnd * matches_per_round + k] == 0 else (b, a)
                    wins[w] += 1
                    losses[l] += 1
                ranked = sorted(order, key=lambda i: (-wins[i], losses[i], i))
                before = set(played)
                got, _bye = bracket_service.pair_swiss(
                    [P(i) for i in ranked], {i: (wins[i], losses[i]) for i in order},
                    before, set(), method=method)
                pairs = [(a.id, b.id) for a, b in got]
                rematch = any(frozenset(p) in before for p in pairs)
                if rematch:
                    self.assertFalse(_perfect_free_matching_exists(ranked, before),
                                     '%s drew a rematch with a free pairing available' % method)
                checked += 1
        return checked

    def test_every_engine(self):
        for method in ('monrad', 'dutch', 'valve'):
            for n in (6, 8):
                with self.subTest(method=method, n=n):
                    self.assertGreater(self.simulate(n, method), 0)


# ---------------------------------------------------------------------------
# The tie-break catalogue, on a round robin of four whose results are known:
#   round 1: A-D draw, B beats C   round 2: A beats C, B beats D
#   round 3: A beats B, C beats D
# Points 3/1/0: A 7, B 6, C 3, D 1.
# ---------------------------------------------------------------------------

def four_played(rules=None):
    t, creator, regs = field(4, 'round_robin')
    stage = add_stage(t, 0, 'round_robin')
    if rules:
        stage.rules = rules
        stage.save(update_fields=['rules'])
    draw(stage, creator)
    a, b, c, d = regs
    want = {frozenset((a.id, d.id)): None, frozenset((b.id, c.id)): b, frozenset((a.id, c.id)): a,
            frozenset((b.id, d.id)): b, frozenset((a.id, b.id)): a, frozenset((c.id, d.id)): c}
    for m in stage.matches.all():
        winner = want[frozenset((m.participant_1_id, m.participant_2_id))]
        if winner is None:
            play(m, 1, 1)
        elif winner.id == m.participant_1_id:
            play(m, 2, 0)
        else:
            play(m, 0, 2)
    rows = {r['registration_id']: r for r in stage_engine.standings(stage)}
    return stage, [rows[r.id] for r in regs]


class TieBreakCatalogueTests(TestCase):
    def test_the_points_are_what_the_comment_says(self):
        _stage, rows = four_played()
        self.assertEqual([r['points'] for r in rows], [7, 6, 3, 1])

    def test_sonneborn_berger(self):
        _stage, rows = four_played()
        self.assertEqual([r['sonneborn_berger'] for r in rows], [9.5, 4, 1, 3.5])

    def test_koya(self):
        _stage, rows = four_played()
        self.assertEqual([r['koya'] for r in rows], [3, 0, 0, 1])

    def test_progressive(self):
        _stage, rows = four_played()
        self.assertEqual([r['progressive'] for r in rows], [12, 15, 3, 3])

    def test_opponents_win_percentage_with_its_floor(self):
        _stage, rows = four_played()
        self.assertEqual([r['opponents_win_pct'] for r in rows], [41.67, 41.67, 52.78, 55.56])
        self.assertEqual(rows[0]['opp_opp_win_pct'], 50.0)

    def test_buchholz_and_its_cut_and_median(self):
        _stage, rows = four_played({'buchholz': 'points'})
        a = rows[0]
        self.assertEqual((a['buchholz'], a['buchholz_cut1'], a['buchholz_median1']), (10, 9, 3))

    def test_buchholz_as_wins_minus_losses(self):
        _stage, rows = four_played({'buchholz': 'wins_minus_losses'})
        self.assertEqual(rows[0]['buchholz'], -2)

    def test_a_tie_break_is_applied_when_chosen(self):
        # B and C level on points would be split by Sonneborn-Berger; here
        # the key only has to be accepted and read.
        rules = ranking.clean_rules('swiss', {'tiebreakers': ['sonneborn_berger', 'progressive',
                                                              'opponents_win_pct', 'buchholz_cut1']})
        self.assertEqual(rules['tiebreakers'],
                         ['sonneborn_berger', 'progressive', 'opponents_win_pct', 'buchholz_cut1'])
        self.assertIn('koya', ranking.offered('round_robin'))
        self.assertNotIn('koya', ranking.offered('swiss'))

    def test_the_floor_and_the_buchholz_form_are_rules(self):
        rules = ranking.clean_rules('swiss', {'buchholz': 'wins_minus_losses', 'owp_floor': 33})
        self.assertEqual((rules['buchholz'], rules['owp_floor']), ('wins_minus_losses', 33))
        with self.assertRaises(ranking.RulesError):
            ranking.clean_rules('swiss', {'owp_floor': 80})


# ---------------------------------------------------------------------------
# Decisive matches and the Valve preset
# ---------------------------------------------------------------------------

class DecisiveMatchTests(TestCase):
    def test_a_match_that_can_qualify_or_eliminate_is_best_of_three(self):
        t, creator, _regs = field(8)
        swiss = add_stage(t, 0, 'swiss', settings={'rounds': 5, 'win_target': 3, 'loss_limit': 3,
                                                   'best_of': 1, 'decisive_best_of': 3})
        draw(swiss, creator)
        self.assertEqual({m.best_of for m in swiss.matches.filter(round_number=1)}, {1})
        for rnd in (1, 2):
            for m in swiss.matches.filter(round_number=rnd):
                low = min(m.participant_1.seed, m.participant_2.seed)
                play(m, 2, 0) if m.participant_1.seed == low else play(m, 0, 2)
        rows = {r['registration_id']: r for r in stage_engine.standings(swiss)}
        for m in swiss.matches.filter(round_number=3):
            record = (rows[m.participant_1_id]['wins'], rows[m.participant_1_id]['losses'])
            expected = 1 if record == (1, 1) else 3
            self.assertEqual(m.best_of, expected, record)

    def test_valve_preset(self):
        preset = stage_settings.FORMAT_PRESETS['valve_major']
        settings = stage_settings.clean('swiss', preset['settings'])
        self.assertEqual((settings['pairing'], settings['win_target'], settings['loss_limit'],
                          settings['decisive_best_of'], settings['best_of']), ('valve', 3, 3, 3, 1))
        rules = ranking.clean_rules('swiss', preset['rules'])
        self.assertEqual((rules['buchholz'], rules['tiebreakers'][0]), ('wins_minus_losses', 'buchholz'))

    def test_a_valve_stage_plays_out(self):
        t, creator, _regs = field(16)
        preset = stage_settings.FORMAT_PRESETS['valve_major']
        swiss = add_stage(t, 0, 'swiss', settings=preset['settings'], advances=8)
        swiss.rules = preset['rules']
        swiss.save(update_fields=['rules'])
        add_stage(t, 1, 'single_elimination')
        draw(swiss, creator)
        play_out(swiss)
        rows = stage_engine.standings(swiss)
        self.assertEqual(sum(1 for r in rows if r['status'] == 'qualified'), 8)
        pairs = [frozenset((m.participant_1_id, m.participant_2_id)) for m in swiss.matches.all()
                 if m.participant_2_id]
        self.assertEqual(len(pairs), len(set(pairs)), 'a rematch was drawn')
