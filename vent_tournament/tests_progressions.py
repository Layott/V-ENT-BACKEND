"""Stages connect by progression lines (P1b).

CEO, 16 October 2026: "i aprove the design". Section 6 of the bracket structure
design: a Progression says "places a to b of stage S (each group, or one group)
go to stage T, this side", so a tournament can chain, split, split a league with
its points, merge pools and send qualifiers into the lower bracket, each line
going when the organiser chooses. Every tournament planned before keeps the
chain its order and advances describe.
"""
from django.db import transaction
from django.test import TestCase

from . import progressions, stage_engine
from .models import Progression
from .tests_stage_engine import add_stage, draw, field, play_out


class ChainTests(TestCase):
    def test_chain_of_three(self):
        stages = [
            {'format': 'round_robin', 'advances': 2, 'groups': 2, 'settings': {}},
            {'format': 'single_elimination', 'advances': 2, 'groups': 0, 'settings': {}},
            {'format': 'single_elimination', 'advances': 0, 'groups': 0, 'settings': {}},
        ]
        lines = progressions.chain(stages)
        self.assertEqual([(l['source'], l['target'], l['place_from'], l['place_to'], l['group'])
                          for l in lines], [(0, 1, 1, 2, None), (1, 2, 1, 2, None)])
        self.assertTrue(all(l['fire'] == 'after_preview' and l['side'] == 'any' for l in lines))


class ConversionTests(TestCase):
    def test_conversion_writes_the_chain_and_what_was_sent(self):
        t, creator, _regs = field(8, 'round_robin')
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        playoff = add_stage(t, 1, 'single_elimination')
        draw(groups, creator)
        play_out(groups)
        with transaction.atomic():
            stage_engine.advance(groups, creator, draw_next=False)
        groups.refresh_from_db()
        Progression.objects.filter(source__tournament=t).delete()

        self.assertEqual(progressions.convert(t), 1)
        line = Progression.objects.get(source=groups)
        self.assertEqual(line.target_id, playoff.id)
        self.assertEqual((line.place_from, line.place_to, line.group), (1, 2, None))
        self.assertEqual([r['registration_id'] for r in line.sent],
                         [row['registration_id'] for row in groups.advanced])
        self.assertEqual(line.fired_at, groups.completed_at)
        self.assertEqual(progressions.convert(t), 0)

    def test_a_single_stage_has_no_lines(self):
        t, _creator, _regs = field(4, 'single_elimination')
        add_stage(t, 0, 'single_elimination')
        self.assertEqual(progressions.convert(t), 0)
