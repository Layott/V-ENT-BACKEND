"""Repechage and triple elimination, the two W2 presets that need more than
winner and loser lines (P3 W2b, design section 11; research catalogue 2.2, 2.3).

- **Repechage** (Olympic wrestling): "Any wrestler who loses to one of the two
  finalists gets pulled into the repechage"; in each half the earliest such
  loser meets the next, the winner meets the beaten semi-finalist for bronze,
  and two bronze medals are awarded. The entrants are only known once the
  finalists are, so a seat names its source as "lost to the finalist in slot 1
  of the final, in round 2" (`{"lost_to": "F", "side": 1, "round": 2}`) and is
  filled when that finalist is known.
- **Triple elimination**: lose twice and you fall into a third bracket, a
  third loss eliminates. The champions of the three brackets finish with a
  ladder: the third bracket's against the second's, the winner against the
  first's.
"""
from django.test import SimpleTestCase, TestCase

from . import bracket_presets, bracket_schema, stage_engine
from .bracket_schema import SchemaError
from .tests_p3_schema import preset_stage, ranks
from .tests_stage_engine import play_out


def losses(stage):
    out = {}
    for m in stage.matches.filter(status='completed'):
        loser = m.participant_2_id if m.winner_id == m.participant_1_id else m.participant_1_id
        out[loser] = out.get(loser, 0) + 1
    return out


class LostToSourceTests(SimpleTestCase):
    def test_the_line_language_writes_it(self):
        got = bracket_schema.parse('A=[1,2]\nB=[3,4]\nF=[W:A,W:B]!\nR=[X:F:1:1,X:F:2:1] >3,4')
        self.assertEqual(got['matches'][3]['slots'], [{'lost_to': 'F', 'side': 1, 'round': 1},
                                                      {'lost_to': 'F', 'side': 2, 'round': 1}])

    def test_it_must_name_a_match_and_an_earlier_round(self):
        bad = bracket_schema.parse('A=[1,2]\nB=[3,4]\nF=[W:A,W:B]!\nR=[X:F:1:1,X:F:2:1] >3,4')
        bad['matches'][3]['slots'][0]['round'] = 2           # the final's own round
        with self.assertRaises(SchemaError) as caught:
            bracket_schema.check(bad)
        self.assertEqual(caught.exception.code, 'SCHEMA_ROUND_ORDER')
        bad['matches'][3]['slots'][0] = {'lost_to': 'Z', 'side': 1, 'round': 1}
        with self.assertRaises(SchemaError) as caught:
            bracket_schema.check(bad)
        self.assertEqual(caught.exception.code, 'SCHEMA_UNKNOWN_MATCH')


class RepechageTests(TestCase):
    def test_the_losers_to_each_finalist_play_for_two_bronzes(self):
        stage, regs = preset_stage(16, 'repechage')
        self.assertEqual(stage.matches.count(), 19)
        play_out(stage)
        got = ranks(stage)
        # Seed 1's path: 16, 8, 4; seed 2's: 15, 7, 3. Chalk: 8 beats 16 then
        # loses to 4; 7 beats 15 then loses to 3. Two bronzes, two fifths.
        self.assertEqual((got[1], got[2], got[3], got[4], got[7], got[8]), (1, 2, 3, 3, 5, 5))
        self.assertTrue(stage_engine.stage_finished(stage))

    def test_the_repechage_seats_wait_until_the_finalists_are_known(self):
        stage, _regs = preset_stage(16, 'repechage')
        waiting = stage.matches.filter(slots__kind='lost_to').distinct()
        self.assertTrue(waiting.exists())
        self.assertFalse(waiting.filter(participant_1__isnull=False).exists())


class TripleEliminationTests(TestCase):
    def test_out_only_on_a_third_loss(self):
        """Everybody placed third or below lost exactly three times. The
        grand final keeps one replay, as a double elimination's does, so its
        loser may leave with two (the preset's docstring says so)."""
        for n in (4, 8, 16):
            with self.subTest(n=n):
                stage, regs = preset_stage(n, 'triple_elimination')
                play_out(stage)
                self.assertTrue(stage_engine.stage_finished(stage))
                lost = losses(stage)
                got = ranks(stage)
                for r in regs:
                    if got[r.seed] == 1:
                        self.assertLessEqual(lost.get(r.id, 0), 2)
                    elif got[r.seed] == 2:
                        self.assertIn(lost.get(r.id, 0), (2, 3))
                    else:
                        self.assertEqual(lost.get(r.id, 0), 3, 'seed %s' % r.seed)
                self.assertEqual(sorted(got.values())[:3], [1, 2, 3])


class SizesTests(SimpleTestCase):
    def test_every_size_offered_checks(self):
        for key in ('repechage', 'triple_elimination'):
            for n in bracket_presets.SIZES[key]:
                with self.subTest(preset=key, n=n):
                    self.assertGreaterEqual(bracket_schema.check(bracket_presets.PRESETS[key](n)), n)
