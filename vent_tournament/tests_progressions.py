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
