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


def S(fmt, groups=0, direct=None):
    """A cleaned stage as `stages.plan` returns it, enough for the line checks."""
    return {'format': fmt, 'groups': groups, 'advances': 0, 'direct_entrants': direct or [],
            'settings': {}}


def L(source, target, place_to, place_from=1, group=None, side='any', fire='after_preview',
      carry=False):
    return {'source': source, 'target': target, 'group': group, 'place_from': place_from,
            'place_to': place_to, 'side': side, 'fire': fire, 'carry_points': carry}


class LineCheckTests(TestCase):
    def refused(self, lines, stages, code, participants=16):
        with self.assertRaises(progressions.LineError) as caught:
            progressions.clean(lines, stages, participants=participants)
        self.assertEqual(caught.exception.code, code)

    def test_same_stage(self):
        self.refused([L(0, 0, 2)], [S('round_robin')], 'PROGRESSION_SAME_STAGE')

    def test_unknown_stage(self):
        self.refused([L(0, 5, 2)], [S('round_robin'), S('single_elimination')],
                     'PROGRESSION_STAGE_UNKNOWN')

    def test_cycle(self):
        stages = [S('round_robin'), S('round_robin'), S('single_elimination')]
        self.refused([L(0, 1, 2), L(1, 0, 2), L(1, 2, 2)], stages, 'PROGRESSION_CYCLE')

    def test_places(self):
        stages = [S('round_robin'), S('single_elimination')]
        self.refused([L(0, 1, 2, place_from=3)], stages, 'PROGRESSION_PLACES')
        self.refused([L(0, 1, 2, place_from=0)], stages, 'PROGRESSION_PLACES')

    def test_more_than_a_group_holds(self):
        # 16 entrants in 8 groups is 2 a group: a third place does not exist.
        self.refused([L(0, 1, 3)], [S('round_robin', groups=8), S('single_elimination')],
                     'PROGRESSION_MORE_THAN_A_GROUP_HOLDS')

    def test_place_twice(self):
        stages = [S('round_robin', groups=2), S('double_elimination'), S('single_elimination')]
        self.refused([L(0, 1, 2), L(0, 2, 3, place_from=2)], stages, 'PROGRESSION_PLACE_TWICE')

    def test_format_cannot_follow(self):
        stages = [S('single_elimination'), S('round_robin')]
        self.refused([L(0, 1, 2)], stages, 'PROGRESSION_FORMAT_CANNOT_FOLLOW')

    def test_side_needs_double_elimination(self):
        stages = [S('round_robin', groups=2), S('single_elimination')]
        self.refused([L(0, 1, 2, side='losers')], stages,
                     'PROGRESSION_LOSERS_SIDE_NEEDS_DOUBLE_ELIMINATION')

    def test_target_too_few_and_too_many(self):
        self.refused([L(0, 1, 1)], [S('round_robin'), S('swiss')], 'PROGRESSION_TARGET_TOO_FEW')
        self.refused([L(0, 1, 5)], [S('round_robin'), S('page_playoff')],
                     'PROGRESSION_TARGET_TOO_MANY')

    def test_group_unknown(self):
        self.refused([L(0, 1, 2, group=3)], [S('round_robin', groups=2), S('single_elimination')],
                     'PROGRESSION_GROUP_UNKNOWN')

    def test_split_upper_and_lower_is_clean(self):
        stages = [S('round_robin', groups=2), S('double_elimination')]
        lines = progressions.clean([L(0, 1, 2, side='winners'), L(0, 1, 3, place_from=3, side='losers')],
                                   stages, participants=8)
        self.assertEqual(progressions.inflow(lines, stages, 1), 6)


class SavePlanLineTests(TestCase):
    def plan(self, t, creator, stages, lines=None):
        from . import bracket_edit
        with transaction.atomic():
            return bracket_edit.save_plan(t, stages, creator, participants=8, raw_lines=lines)

    def test_old_planner_still_saves(self):
        t, creator, _regs = field(8, 'round_robin')
        rows = self.plan(t, creator, [
            {'format': 'round_robin', 'groups': 2, 'advances': 2},
            {'format': 'single_elimination'}])
        line = Progression.objects.get(source=rows[0])
        self.assertEqual((line.target_id, line.place_to), (rows[1].id, 2))

    def test_lines_replace_the_chain(self):
        t, creator, _regs = field(8, 'round_robin')
        rows = self.plan(t, creator, [
            {'format': 'round_robin', 'groups': 2},
            {'format': 'double_elimination'}],
            lines=[L(0, 1, 2, side='winners'), L(0, 1, 3, place_from=3, side='losers')])
        got = list(Progression.objects.filter(source=rows[0]).values_list(
            'place_from', 'place_to', 'side'))
        self.assertEqual(sorted(got), [(1, 2, 'winners'), (3, 3, 'losers')])

    def test_fired_line_is_locked(self):
        from . import bracket_edit
        t, creator, _regs = field(8, 'round_robin')
        rows = self.plan(t, creator, [
            {'format': 'round_robin', 'groups': 2, 'advances': 2},
            {'format': 'single_elimination'}])
        Progression.objects.filter(source=rows[0]).update(fired_at='2026-10-17T10:00:00Z')
        with self.assertRaises(bracket_edit.EditError) as caught:
            self.plan(t, creator, [
                {'id': rows[0].id, 'format': 'round_robin', 'groups': 2},
                {'id': rows[1].id, 'format': 'single_elimination'}],
                lines=[L(0, 1, 1)])
        self.assertEqual(caught.exception.code, 'PROGRESSION_FIRED_LOCKED')


class EndpointTests(TestCase):
    def test_get_and_put_lines(self):
        from .tests import client_for
        t, creator, _regs = field(8, 'round_robin')
        api = client_for(creator)
        base = '/tournament/%s/' % (t.slug or t.tournament_id)
        res = api.put(base + 'stages/set/', {
            'stages': [{'format': 'round_robin', 'groups': 2}, {'format': 'double_elimination'}],
            'progressions': [L(0, 1, 2, side='winners'), L(0, 1, 3, place_from=3, side='losers')],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        got = res.json()['data']['progressions']
        self.assertEqual(sorted((l['place_from'], l['place_to'], l['side']) for l in got),
                         [(1, 2, 'winners'), (3, 3, 'losers')])
        listed = api.get(base + 'stages/').json()['data']['progressions']
        self.assertEqual(len(listed), 2)
        refused = api.put(base + 'stages/set/', {
            'stages': [{'format': 'round_robin', 'groups': 2}, {'format': 'single_elimination'}],
            'progressions': [L(0, 1, 2, side='losers')],
        }, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()['code'], 'PROGRESSION_LOSERS_SIDE_NEEDS_DOUBLE_ELIMINATION')

    def test_stages_without_lines_show_the_chain(self):
        from .tests import client_for
        t, creator, _regs = field(8, 'round_robin')
        a = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        b = add_stage(t, 1, 'single_elimination')
        listed = client_for(creator).get('/tournament/%s/stages/' % t.slug).json()['data']['progressions']
        self.assertEqual([(l['id'], l['source'], l['target'], l['place_to']) for l in listed],
                         [(None, a.id, b.id, 2)])


def lines(t, spec):
    """Replace a tournament's lines: spec is [(source stage, target stage, place_to, kw)]."""
    Progression.objects.filter(source__tournament=t).delete()
    for order, (source, target, place_to, kw) in enumerate(spec):
        Progression.objects.create(source=source, target=target, place_to=place_to,
                                   order=order, **kw)


def advance(stage, user, **kw):
    with transaction.atomic():
        return stage_engine.advance(stage, user, **kw)


class EngineTests(TestCase):
    def test_converted_chain_advances_as_before(self):
        from .tests_golden_draws import drawn_staged, snapshot
        t = drawn_staged(8, 2)
        groups = t.stages.get(order=0)
        self.assertEqual(Progression.objects.filter(source=groups).count(), 1)
        play_out(groups)
        result = advance(groups, t.tournament_creator)
        playoff = t.stages.get(order=1)
        self.assertEqual(result['next_stage_id'], playoff.id)
        self.assertEqual(len(result['advanced']), 4)
        self.assertEqual(playoff.matches.count(), 3)
        line = Progression.objects.get(source=groups)
        self.assertEqual([r['registration_id'] for r in line.sent],
                         [r['registration_id'] for r in result['advanced']])

    def test_split_upper_and_lower(self):
        t, creator, _regs = field(8, 'round_robin')
        groups = add_stage(t, 0, 'round_robin', advances=3, groups=2)
        de = add_stage(t, 1, 'double_elimination')
        lines(t, [(groups, de, 2, {'side': 'winners'}),
                  (groups, de, 3, {'place_from': 3, 'side': 'losers'})])
        draw(groups, creator)
        play_out(groups)
        advance(groups, creator)
        de.refresh_from_db()
        self.assertIsNotNone(de.drawn_at)
        upper_one = de.matches.filter(bracket_side='winners', round_number=1)
        self.assertEqual(sum(1 for m in upper_one for p in (m.participant_1_id, m.participant_2_id) if p), 4)
        lower_seats = [p for m in de.matches.filter(bracket_side='losers', round_number=1)
                       for p in (m.participant_1_id, m.participant_2_id) if p]
        self.assertEqual(len(lower_seats), 2)
        play_out(de)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        played = {p for m in de.matches.exclude(status='bye')
                  for p in (m.participant_1_id, m.participant_2_id) if p}
        self.assertEqual(len(played), 6)

    def test_merge_waits_for_both_pools(self):
        t, creator, regs = field(8, 'round_robin')
        ids = [r.id for r in regs]
        pool_a = add_stage(t, 0, 'round_robin', direct=ids[:4])
        pool_b = add_stage(t, 1, 'round_robin', direct=ids[4:])
        ko = add_stage(t, 2, 'single_elimination')
        lines(t, [(pool_a, ko, 2, {}), (pool_b, ko, 2, {})])
        draw(pool_a, creator)
        draw(pool_b, creator)
        self.assertEqual(sorted(pool_a.entrants), sorted(ids[:4]))
        play_out(pool_a)
        result = advance(pool_a, creator)
        ko.refresh_from_db()
        self.assertIsNone(ko.drawn_at)
        self.assertEqual(result['waiting'], [ko.id])
        play_out(pool_b)
        advance(pool_b, creator)
        ko.refresh_from_db()
        self.assertIsNotNone(ko.drawn_at)
        self.assertEqual(len(ko.entrants), 4)

    def test_split_league_carries_points(self):
        t, creator, _regs = field(6, 'round_robin')
        table = add_stage(t, 0, 'round_robin', advances=6)
        top = add_stage(t, 1, 'round_robin')
        bottom = add_stage(t, 2, 'round_robin')
        lines(t, [(table, top, 3, {'carry_points': True}),
                  (table, bottom, 6, {'place_from': 4, 'carry_points': True})])
        draw(table, creator)
        play_out(table)
        before = {r['registration_id']: r['points'] for r in stage_engine.standings(table)}
        advance(table, creator)
        for stage in (top, bottom):
            stage.refresh_from_db()
            rows = stage_engine.standings(stage)
            self.assertEqual(len(rows), 3)
            for r in rows:
                self.assertEqual(r['points'], before[r['registration_id']])

    def test_on_close_fires_by_itself(self):
        t, creator, _regs = field(8, 'round_robin')
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        ko = add_stage(t, 1, 'single_elimination')
        lines(t, [(groups, ko, 2, {'fire': 'on_close'})])
        draw(groups, creator)
        play_out(groups)
        ko.refresh_from_db()
        self.assertIsNotNone(ko.drawn_at)
        self.assertIsNotNone(Progression.objects.get(source=groups).fired_at)

    def test_manual_waits_for_pull(self):
        t, creator, _regs = field(8, 'round_robin')
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        ko = add_stage(t, 1, 'single_elimination')
        lines(t, [(groups, ko, 2, {'fire': 'manual'})])
        draw(groups, creator)
        play_out(groups)
        advance(groups, creator)
        ko.refresh_from_db()
        self.assertIsNone(ko.drawn_at)
        line = Progression.objects.get(source=groups)
        with transaction.atomic():
            progressions.pull(line, creator)
        ko.refresh_from_db()
        self.assertIsNotNone(ko.drawn_at)

    def test_unadvance_clears_the_line(self):
        from . import bracket_edit
        t, creator, _regs = field(8, 'round_robin')
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        add_stage(t, 1, 'single_elimination')
        draw(groups, creator)
        play_out(groups)
        advance(groups, creator, draw_next=False)
        groups.refresh_from_db()
        with transaction.atomic():
            bracket_edit.unadvance(groups, creator)
        line = Progression.objects.get(source=groups)
        self.assertEqual((line.sent, line.fired_at), ([], None))
        groups.refresh_from_db()
        advance(groups, creator, draw_next=False)
        self.assertIsNotNone(Progression.objects.get(source=groups).fired_at)


class FinalPlaceTests(TestCase):
    def test_split_league_places(self):
        t, creator, regs = field(6, 'round_robin')
        table = add_stage(t, 0, 'round_robin', advances=6)
        top = add_stage(t, 1, 'round_robin')
        bottom = add_stage(t, 2, 'round_robin')
        lines(t, [(table, top, 3, {'carry_points': True}),
                  (table, bottom, 6, {'place_from': 4, 'carry_points': True})])
        draw(table, creator)
        play_out(table)
        advance(table, creator)
        top.refresh_from_db()
        bottom.refresh_from_db()
        play_out(top)
        play_out(bottom)
        placed = stage_engine.assign_final_positions(t)
        top_ids = set(top.entrants)
        self.assertEqual(sorted(placed[i] for i in top_ids), [1, 2, 3])
        self.assertEqual(sorted(placed[i] for i in bottom.entrants), [4, 5, 6])

    def test_parallel_pools_share_places(self):
        t, creator, regs = field(8, 'round_robin')
        ids = [r.id for r in regs]
        pool_a = add_stage(t, 0, 'round_robin', direct=ids[:4])
        pool_b = add_stage(t, 1, 'round_robin', direct=ids[4:])
        ko = add_stage(t, 2, 'single_elimination')
        lines(t, [(pool_a, ko, 2, {}), (pool_b, ko, 2, {})])
        for pool in (pool_a, pool_b):
            draw(pool, creator)
            play_out(pool)
            advance(pool, creator)
        ko.refresh_from_db()
        play_out(ko)
        placed = stage_engine.assign_final_positions(t)
        thirds = [r['registration_id'] for pool in (pool_a, pool_b)
                  for r in stage_engine.standings(pool) if r['rank'] == 3]
        self.assertEqual(len(thirds), 2)
        self.assertEqual(placed[thirds[0]], placed[thirds[1]])
        self.assertEqual(placed[thirds[0]], 5)
