"""Every match rule says where it comes from (P1c, design section 10).

CEO, 16 October 2026: "i aprove the design". "Best of, legs, draws, report
mode, check-in window and timers are set on the tournament and overridden on a
stage, group, round or match. The nearest setting wins and the screen says
where a value comes from. One resolver function answers it for every reader."
"""
from django.db import transaction
from django.test import TestCase

from . import bracket_edit, match_rules, match_shape, stage_engine
from .models import BracketMatch
from .tests import gen_bracket
from .tests_stage_engine import add_stage, field, play


def staged(n=8, settings=None):
    t, creator, _regs = field(n, 'single_elimination')
    stage = add_stage(t, 0, 'single_elimination', settings=settings or {'best_of': 3})
    with transaction.atomic():
        stage_engine.draw(stage, creator)
    return t, creator, stage


class MatchRulesTests(TestCase):
    def test_stage_default(self):
        _t, _creator, stage = staged()
        m = stage.matches.filter(round_number=1).first()
        got = match_rules.rules_of(m)
        self.assertEqual(got['best_of'], {'value': 3, 'from': 'stage', 'stage': stage.label})
        self.assertEqual(got['draw_allowed']['from'], 'stage')

    def test_round_override_after_the_draw(self):
        _t, creator, stage = staged()
        first = stage.matches.filter(round_number=1).order_by('match_number')
        play(first[0], 2, 0)
        play(first[1], 2, 0)
        second = list(stage.matches.filter(round_number=2).order_by('match_number'))
        play(BracketMatch.objects.get(pk=second[0].pk), 2, 1)
        unplayed = BracketMatch.objects.get(pk=second[1].pk)
        with transaction.atomic():
            bracket_edit.set_rules(unplayed, creator, best_of=5, whole_round=True)
        stage.refresh_from_db()
        self.assertEqual(stage.settings['round_best_of']['2'], 5)
        got = match_rules.rules_of(BracketMatch.objects.get(pk=unplayed.pk))
        self.assertEqual((got['best_of']['value'], got['best_of']['from']), (5, 'round'))
        played = match_rules.rules_of(BracketMatch.objects.get(pk=second[0].pk))
        self.assertEqual((played['best_of']['value'], played['best_of']['from']), (3, 'stage'))

    def test_match_override(self):
        _t, creator, stage = staged()
        a, b = stage.matches.filter(round_number=1).order_by('match_number')[:2]
        with transaction.atomic():
            bracket_edit.set_rules(a, creator, best_of=5)
        self.assertEqual(match_rules.rules_of(BracketMatch.objects.get(pk=a.pk))['best_of'],
                         {'value': 5, 'from': 'match', 'stage': stage.label})
        self.assertEqual(match_rules.rules_of(BracketMatch.objects.get(pk=b.pk))['best_of']['from'],
                         'stage')

    def test_one_format_reads_the_tournament(self):
        t, creator, _regs = field(4, 'single_elimination')
        gen_bracket(t, creator, 'registration')
        got = match_rules.rules_of(t.bracket_matches.first())
        self.assertEqual({v['from'] for v in got.values()}, {'tournament'})

    def test_payload_carries_rules(self):
        _t, _creator, stage = staged()
        row = match_shape.match_row(stage.matches.first())
        self.assertEqual(row['rules']['best_of']['from'], 'stage')
        self.assertEqual(set(row['rules']), {'best_of', 'legs', 'draw_allowed', 'check_in_minutes'})
