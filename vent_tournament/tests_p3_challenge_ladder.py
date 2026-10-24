"""The challenge ladder and the pyramid (P3 W3d, research catalogue 5.1).

CEO, 24 October 2026: "make all brackets and formats live today and function
properly". Every match on a ladder is made by a challenge; a challenger who
wins takes the place they challenged for.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import ladder, stage_engine, stage_settings
from .tests import client_for
from .tests_stage_engine import add_stage, draw, field, play


def a_ladder(n=6, **settings):
    t, creator, regs = field(n, 'challenge_ladder')
    stage = add_stage(t, 0, 'challenge_ladder', settings=settings)
    draw(stage, creator)
    stage.refresh_from_db()
    return t, creator, regs, stage


def places(stage):
    stage.refresh_from_db()
    return [r['seed'] for r in stage_engine.standings(stage)]


class DrawTests(TestCase):
    def test_drawn_in_seed_order_with_no_matches(self):
        t, creator, regs, stage = a_ladder()
        self.assertEqual(stage.matches.count(), 0)
        self.assertEqual(places(stage), [1, 2, 3, 4, 5, 6])
        self.assertFalse(stage_engine.stage_finished(stage))

    def test_settings_reach_and_shape(self):
        out = stage_settings.clean('challenge_ladder', {})
        self.assertEqual((out['reach'], out['shape']), (3, 'ladder'))
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('challenge_ladder', {'shape': 'circle'})
        self.assertEqual(caught.exception.field, 'shape')


class ChallengeTests(TestCase):
    def test_a_win_swaps_the_places_and_a_loss_leaves_them(self):
        t, creator, regs, stage = a_ladder()
        by_seed = {r.seed: r for r in regs}
        m = ladder.challenge(stage, by_seed[4], by_seed[2])
        self.assertEqual((m.participant_1_id, m.participant_2_id), (by_seed[4].id, by_seed[2].id))
        play(m, 2, 0)
        self.assertEqual(places(stage), [1, 4, 3, 2, 5, 6])
        m = ladder.challenge(stage, by_seed[6], by_seed[5])
        play(m, 0, 2)
        self.assertEqual(places(stage), [1, 4, 3, 2, 5, 6])

    def test_refusals_carry_a_code(self):
        t, creator, regs, stage = a_ladder(reach=2)
        s = {r.seed: r for r in regs}
        self.assertEqual(ladder.may_challenge(stage, s[2].id, s[4].id), 'LADDER_NOT_ABOVE')
        self.assertEqual(ladder.may_challenge(stage, s[5].id, s[2].id), 'LADDER_OUT_OF_REACH')
        ladder.challenge(stage, s[3], s[1])
        self.assertEqual(ladder.may_challenge(stage, s[2].id, s[1].id), 'LADDER_BUSY')

    def test_the_loser_cannot_challenge_the_same_player_straight_away(self):
        t, creator, regs, stage = a_ladder()
        s = {r.seed: r for r in regs}
        play(ladder.challenge(stage, s[3], s[2]), 0, 1)
        self.assertEqual(ladder.may_challenge(stage, s[3].id, s[2].id), 'LADDER_REMATCH')
        play(ladder.challenge(stage, s[3], s[1]), 0, 1)
        self.assertIsNone(ladder.may_challenge(stage, s[3].id, s[2].id))

    def test_a_pyramid_reaches_its_own_level_and_the_one_above(self):
        self.assertEqual([ladder.level_of(p) for p in range(1, 8)], [1, 2, 2, 3, 3, 3, 4])
        t, creator, regs, stage = a_ladder(n=7, shape='pyramid')
        s = {r.seed: r for r in regs}
        self.assertIsNone(ladder.may_challenge(stage, s[6].id, s[4].id))   # own level
        self.assertIsNone(ladder.may_challenge(stage, s[6].id, s[2].id))   # level above
        self.assertEqual(ladder.may_challenge(stage, s[6].id, s[1].id), 'LADDER_OUT_OF_REACH')
        rows = stage_engine.standings(stage)
        self.assertEqual([r['level'] for r in rows], [1, 2, 2, 3, 3, 3, 4])

    def test_the_ladder_closes_at_the_stage_end(self):
        t, creator, regs, stage = a_ladder()
        stage.ends_at = timezone.now() - timedelta(minutes=1)
        stage.save(update_fields=['ends_at'])
        self.assertTrue(stage_engine.stage_finished(stage))
        s = {r.seed: r for r in regs}
        self.assertEqual(ladder.may_challenge(stage, s[2].id, s[1].id), 'LADDER_CLOSED')


class DoorTests(TestCase):
    def test_a_player_challenges_from_their_own_place(self):
        t, creator, regs, stage = a_ladder()
        s = {r.seed: r for r in regs}
        res = client_for(s[3].user).post(
            f'/tournament/{t.slug}/stages/{stage.id}/challenge/',
            {'target_registration_id': s[1].id}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(stage.matches.count(), 1)

    def test_a_stranger_and_a_signed_out_visitor_are_refused(self):
        t, creator, regs, stage = a_ladder()
        s = {r.seed: r for r in regs}
        other, _c, _r = field(3, 'challenge_ladder')
        stranger = _r[0].user
        res = client_for(stranger).post(f'/tournament/{t.slug}/stages/{stage.id}/challenge/',
                                        {'target_registration_id': s[1].id}, format='json')
        self.assertEqual((res.status_code, res.data['code']), (403, 'NOT_IN_THIS_LADDER'))
        res = APIClient().post(f'/tournament/{t.slug}/stages/{stage.id}/challenge/',
                               {'target_registration_id': s[1].id}, format='json')
        self.assertIn(res.status_code, (401, 403))

    def test_an_out_of_reach_challenge_is_a_coded_conflict(self):
        t, creator, regs, stage = a_ladder(reach=1)
        s = {r.seed: r for r in regs}
        res = client_for(s[4].user).post(f'/tournament/{t.slug}/stages/{stage.id}/challenge/',
                                         {'target_registration_id': s[1].id}, format='json')
        self.assertEqual((res.status_code, res.data['code']), (409, 'LADDER_OUT_OF_REACH'))


class BracketPayloadTests(TestCase):
    def test_the_page_is_told_who_the_viewer_may_challenge(self):
        t, creator, regs, stage = a_ladder(reach=2)
        s = {r.seed: r for r in regs}
        body = client_for(s[4].user).get(
            f'/tournament/get-tournament-brackets/{t.slug}/?stage={stage.id}').json()['data']
        self.assertEqual(body['ladder']['mine'], s[4].id)
        self.assertEqual(sorted(body['ladder']['challengeable']), sorted([s[2].id, s[3].id]))
        body = APIClient().get(
            f'/tournament/get-tournament-brackets/{t.slug}/?stage={stage.id}').json()['data']
        self.assertEqual((body['ladder']['mine'], body['ladder']['challengeable']), (None, []))
