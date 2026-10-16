"""One ranking function for every table (P0: F2, F5).

CEO, 16 October 2026, agreeing the wrong results go first: "these are fine."
Before this, a stage's group table ranked by a fixed list and ignored the
tie-breakers the organiser chose, a stage Swiss scored 3-1-0 whatever the
organiser set, and five more places in the code each sorted a table their own
way. `ranking.order` is now the only one; these pin what it promises.
"""
from django.db import transaction
from django.test import TestCase

from . import ranking, stage_engine
from .models import LeagueRules
from .tests_stage_engine import add_stage, draw, field, play


def row(rid, points, **fields):
    out = {'registration_id': rid, 'points': points, 'seed': rid, 'name': 'Entrant %d' % rid}
    out.update(fields)
    return out


def h2h_from(beat):
    return lambda ids: {i: len(beat.get(i, set()) & set(ids)) for i in ids}


class OrderTests(TestCase):
    def test_shared_rank_when_everything_is_equal(self):
        rows = ranking.order([row(2, 6, goal_difference=2), row(1, 6, goal_difference=2),
                              row(3, 9, goal_difference=0)], ['goal_difference'])
        self.assertEqual([r['registration_id'] for r in rows], [3, 1, 2])
        self.assertEqual([r['rank'] for r in rows], [1, 2, 2])
        self.assertEqual([r['position'] for r in rows], [1, 2, 3])
        self.assertEqual([r['tied'] for r in rows], [False, True, True])

    def test_decided_by_names_the_first_difference(self):
        rows = ranking.order([row(1, 6, goal_difference=2, goals_for=3),
                              row(2, 6, goal_difference=2, goals_for=5)],
                             ['goal_difference', 'goals_for'])
        self.assertEqual([r['registration_id'] for r in rows], [2, 1])
        self.assertEqual([r['decided_by'] for r in rows], [None, 'goals_for'])

    def test_points_decide_first(self):
        rows = ranking.order([row(1, 3), row(2, 6)], ['goal_difference'])
        self.assertEqual([r['decided_by'] for r in rows], [None, 'points'])

    def test_fewer_goals_against_is_better(self):
        rows = ranking.order([row(1, 6, goals_against=4), row(2, 6, goals_against=1)],
                             ['goals_against'])
        self.assertEqual([r['registration_id'] for r in rows], [2, 1])

    def test_head_to_head_inside_a_three_way_tie(self):
        beat = {1: {2, 3}, 2: {3}}
        rows = ranking.order([row(3, 6), row(2, 6), row(1, 6)], ['head_to_head'],
                             h2h=h2h_from(beat))
        self.assertEqual([r['registration_id'] for r in rows], [1, 2, 3])
        self.assertEqual([r['decided_by'] for r in rows], [None, 'head_to_head', 'head_to_head'])

    def test_a_cycle_falls_through(self):
        beat = {1: {2}, 2: {3}, 3: {1}}
        rows = ranking.order([row(1, 6, goal_difference=1), row(2, 6, goal_difference=3),
                              row(3, 6, goal_difference=2)],
                             ['head_to_head', 'goal_difference'], h2h=h2h_from(beat))
        self.assertEqual([r['registration_id'] for r in rows], [2, 3, 1])
        self.assertEqual([r['decided_by'] for r in rows], [None, 'goal_difference', 'goal_difference'])

    def test_head_to_head_is_applied_again_inside_the_smaller_group(self):
        # Among all four: 1 and 2 won twice, 3 and 4 once. Between 1 and 2,
        # 2 won; between 3 and 4, 4 won.
        beat = {1: {4, 3}, 2: {1, 4}, 3: {2}, 4: {3}}
        rows = ranking.order([row(i, 9) for i in (1, 2, 3, 4)], ['head_to_head'],
                             h2h=h2h_from(beat))
        self.assertEqual([r['registration_id'] for r in rows], [2, 1, 4, 3])

    def test_league_rows_count_won_as_wins(self):
        rows = ranking.order([{'registration_id': 1, 'points': 6, 'won': 1, 'name': 'a'},
                              {'registration_id': 2, 'points': 6, 'won': 2, 'name': 'b'}], ['wins'])
        self.assertEqual([r['registration_id'] for r in rows], [2, 1])

    def test_unknown_tie_breakers_are_ignored(self):
        rows = ranking.order([row(1, 6), row(2, 6)], ['no_such_rule'])
        self.assertEqual([r['rank'] for r in rows], [1, 1])

    def test_clean_rules_refuses_nonsense(self):
        with self.assertRaises(ranking.RulesError) as caught:
            ranking.clean_rules('round_robin', {'points_win': -1})
        self.assertEqual((caught.exception.code, caught.exception.field), ('RULES_INVALID', 'points_win'))
        with self.assertRaises(ranking.RulesError):
            ranking.clean_rules('round_robin', {'tiebreakers': 'goals_for'})
        self.assertIsNone(ranking.clean_rules('round_robin', None))
        clean = ranking.clean_rules('swiss', {'points_win': '2', 'points_bye': 1,
                                              'tiebreakers': ['buchholz', 'nonsense', 'seed']})
        self.assertEqual(clean['points_win'], 2)
        self.assertEqual(clean['tiebreakers'], ['buchholz', 'seed'])


def group_of_four(t):
    """Four entrants in one round robin stage, with results where head to head
    and goal difference disagree about who is third."""
    stage = add_stage(t, 0, 'round_robin')
    draw(stage, t.tournament_creator)
    seeds = {}
    for m in stage.matches.select_related('participant_1', 'participant_2'):
        seeds[(m.participant_1.seed, m.participant_2.seed)] = m
    results = {(1, 2): (1, 0), (1, 3): (0, 1), (1, 4): (5, 0),
               (2, 3): (3, 0), (2, 4): (1, 0), (3, 4): (0, 1)}
    for (a, b), (sa, sb) in results.items():
        m = seeds.get((a, b))
        if m is not None:
            play(m, sa, sb)
        else:
            play(seeds[(b, a)], sb, sa)
    return stage


class StageTableTests(TestCase):
    def test_nothing_changes_without_a_choice(self):
        t, _creator, _regs = field(4, 'round_robin')
        rows = stage_engine.standings(group_of_four(t))
        self.assertEqual([r['seed'] for r in rows], [1, 2, 4, 3])
        self.assertEqual([r['rank'] for r in rows], [1, 2, 3, 4])
        self.assertEqual([r['decided_by'] for r in rows], [None, 'head_to_head', 'points', 'head_to_head'])

    def test_the_organisers_order_is_used_by_a_group_stage(self):
        t, _creator, _regs = field(4, 'round_robin')
        LeagueRules.objects.update_or_create(tournament=t, defaults={'tiebreakers': ['goal_difference']})
        rows = stage_engine.standings(group_of_four(t))
        self.assertEqual([r['seed'] for r in rows], [1, 2, 3, 4])
        self.assertEqual(rows[2]['decided_by'], 'points')
        self.assertEqual(rows[3]['decided_by'], 'goal_difference')

    def test_a_stage_rule_beats_the_tournament_rule(self):
        t, _creator, _regs = field(4, 'round_robin')
        LeagueRules.objects.update_or_create(tournament=t, defaults={'tiebreakers': ['goal_difference']})
        stage = group_of_four(t)
        stage.rules = {'tiebreakers': ['head_to_head']}
        stage.save(update_fields=['rules'])
        self.assertEqual([r['seed'] for r in stage_engine.standings(stage)], [1, 2, 4, 3])

    def test_a_stage_win_is_worth_what_the_organiser_says(self):
        t, _creator, _regs = field(4, 'round_robin')
        stage = group_of_four(t)
        stage.rules = {'points_win': 2, 'points_draw': 1, 'points_loss': 0}
        stage.save(update_fields=['rules'])
        top = stage_engine.standings(stage)[0]
        self.assertEqual(top['points'], 4)


class SwissScoringTests(TestCase):
    def swiss(self, rules=None):
        t, creator, _regs = field(5, 'swiss')
        stage = add_stage(t, 0, 'swiss')
        if rules:
            stage.rules = rules
            stage.save(update_fields=['rules'])
        with transaction.atomic():
            stage_engine.draw(stage, creator)
        stage.refresh_from_db()
        bye = stage.matches.filter(status='bye').first()
        self.assertIsNotNone(bye, 'five entrants should give somebody a bye')
        rows = {r['registration_id']: r for r in stage_engine.standings(stage)}
        return rows[bye.winner_id]

    def test_a_bye_is_worth_a_win_by_default(self):
        self.assertEqual(self.swiss()['points'], 3)

    def test_swiss_scoring_is_the_organisers(self):
        self.assertEqual(self.swiss({'points_win': 2, 'points_bye': 1})['points'], 1)


class CatalogueTests(TestCase):
    def test_the_catalogue_offers_what_a_stage_can_save(self):
        from . import formats
        for entry in formats.catalogue():
            offered = [t['key'] for t in entry['scoring_tiebreakers']]
            ranks_a_table = formats.get(entry['key']).advancement in ('table', 'swiss')
            if not ranks_a_table:
                self.assertEqual(offered, [], entry['key'])
                continue
            self.assertIn('seed', offered, entry['key'])
            clean = ranking.clean_rules(entry['key'], {'tiebreakers': offered})
            self.assertEqual(clean['tiebreakers'], offered[:12], entry['key'])


class KnockoutScoringTests(TestCase):
    def test_a_stage_that_ranks_no_table_keeps_no_scoring(self):
        for key in ('single_elimination', 'double_elimination', 'gsl', 'battle_royale'):
            self.assertIsNone(ranking.clean_rules(key, {'points_win': 1}), key)
        self.assertEqual(ranking.clean_rules('league', {'points_win': 1}), {'points_win': 1})
