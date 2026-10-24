"""A battle royale scored by time (P3 W3b): a speedrun or a race, where the
lowest total time across the matches wins.

CEO, 24 October 2026: "make all brackets and formats live today and function
properly". The duration table is the one format in the catalogue that ranks
by a measured time rather than points.
"""
from django.test import TestCase

from . import br_engine, stage_settings
from .models import BRMap, BRResult
from .tests_battle_royale import br_stage
from .tests_stage_engine import draw


def timed(br_map, times, user):
    """times: {registration: 'm:ss' or None for did not finish, or False for did not play}."""
    rows = []
    for reg, value in times.items():
        if value is False:
            rows.append({'registration_id': reg.id, 'played': False})
        else:
            rows.append({'registration_id': reg.id, 'time': value})
    return br_engine.enter_results(br_map, rows, user)


class TimeParsingTests(TestCase):
    def test_the_shapes_a_time_is_typed_in(self):
        for typed, ms in (('1:05', 65000), ('0:59.5', 59500), ('1:02:03', 3723000),
                          ('42', 42000), (90500, 90500)):
            self.assertEqual(br_engine._duration_ms(typed, 0), ms, typed)
        self.assertIsNone(br_engine._duration_ms('', 0))
        self.assertIsNone(br_engine._duration_ms(None, 0))

    def test_a_time_that_is_not_a_time_is_refused_with_a_code(self):
        for typed in ('abc', '1:75', '-1:00', '1:2:3:4', '25:00:00'):
            with self.assertRaises(br_engine.BRError) as caught:
                br_engine._duration_ms(typed, 3)
            self.assertEqual((caught.exception.code, caught.exception.field), ('NOT_A_TIME', 'time'),
                             typed)

    def test_score_by_is_points_unless_set_and_refuses_anything_else(self):
        self.assertEqual(stage_settings.clean('battle_royale', {})['score_by'], 'points')
        self.assertEqual(stage_settings.clean('battle_royale', {'score_by': 'Time'})['score_by'], 'time')
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('battle_royale', {'score_by': 'kills'})
        self.assertEqual((caught.exception.code, caught.exception.field), ('UNKNOWN_CHOICE', 'score_by'))


class DurationTableTests(TestCase):
    def setUp(self):
        self.t, self.creator, self.regs, self.stage = br_stage(
            4, {'lobby_size': 4, 'maps': 2, 'score_by': 'time'})
        draw(self.stage, self.creator)
        self.m1, self.m2 = BRMap.objects.filter(lobby__stage=self.stage).order_by('number')

    def table(self):
        return br_engine.standings(self.stage)

    def test_the_lowest_total_time_wins(self):
        a, b, c, d = self.regs
        timed(self.m1, {a: '1:10', b: '1:00', c: '1:30', d: '1:20'}, self.creator)
        timed(self.m2, {a: '1:00', b: '1:20', c: '1:05', d: '1:15'}, self.creator)
        rows = self.table()
        # a 2:10, b 2:20, then c and d level on 2:35.
        self.assertEqual([r['registration_id'] for r in rows[:2]], [a.id, b.id])
        self.assertEqual(rows[0]['time_ms'], 130000)
        self.assertEqual(rows[0]['score_by'], 'time')
        self.assertEqual(rows[1]['decided_by'], 'time')

    def test_placements_follow_from_the_times_when_left_out(self):
        a, b, c, d = self.regs
        timed(self.m1, {a: '1:10', b: '1:00', c: None, d: '1:20'}, self.creator)
        got = {r.registration_id: (r.placement, r.duration_ms)
               for r in BRResult.objects.filter(map=self.m1)}
        self.assertEqual(got, {b.id: (1, 60000), a.id: (2, 70000), d.id: (3, 80000), c.id: (4, None)})

    def test_a_squad_that_did_not_finish_ranks_below_every_finisher(self):
        a, b, c, d = self.regs
        # c is fastest on the one it finished, and still last overall.
        timed(self.m1, {a: '2:00', b: '2:10', c: '0:30', d: '2:20'}, self.creator)
        timed(self.m2, {a: '2:00', b: '2:10', c: None, d: '2:20'}, self.creator)
        rows = self.table()
        self.assertEqual([r['registration_id'] for r in rows], [a.id, b.id, d.id, c.id])
        self.assertEqual(rows[-1]['unfinished'], 1)
        self.assertEqual(rows[-1]['decided_by'], 'finished')

    def test_a_squad_that_did_not_play_counts_as_unfinished(self):
        a, b, c, d = self.regs
        timed(self.m1, {a: '1:00', b: '1:10', c: '1:20', d: False}, self.creator)
        rows = {r['registration_id']: r for r in self.table()}
        # Only matches already entered count: one so far.
        self.assertEqual(rows[d.id]['unfinished'], 1)
        self.assertEqual(rows[d.id]['rank'], 4)

    def test_match_point_never_crowns_a_time_table(self):
        self.stage.settings = stage_settings.clean(
            'battle_royale', {'lobby_size': 4, 'maps': 2, 'score_by': 'time', 'match_point': 1})
        self.stage.save(update_fields=['settings'])
        a, b, c, d = self.regs
        timed(self.m1, {a: '1:00', b: '1:10', c: '1:20', d: '1:30'}, self.creator)
        timed(self.m2, {a: '9:00', b: '1:10', c: '1:20', d: '1:30'}, self.creator)
        rows = self.table()
        self.assertFalse(any(r['champion'] for r in rows))
        self.assertEqual(rows[0]['registration_id'], b.id)

    def test_a_points_table_is_unchanged_by_times(self):
        t, creator, regs, stage = br_stage(4, {'lobby_size': 4, 'maps': 1, 'per_kill': 1})
        draw(stage, creator)
        m = BRMap.objects.filter(lobby__stage=stage).first()
        a, b, c, d = regs
        br_engine.enter_results(m, [
            {'registration_id': a.id, 'placement': 1, 'kills': 0, 'time': '9:00'},
            {'registration_id': b.id, 'placement': 2, 'kills': 0, 'time': '1:00'},
            {'registration_id': c.id, 'placement': 3, 'kills': 0},
            {'registration_id': d.id, 'placement': 4, 'kills': 0},
        ], creator)
        rows = br_engine.standings(stage)
        self.assertEqual(rows[0]['registration_id'], a.id)
        self.assertEqual(rows[0]['score_by'], 'points')
