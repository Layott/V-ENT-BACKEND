"""The explainer-relevant stage settings travel on the public payloads.

Review of the preset explainers, 25 October 2026: the tournament page picks the
explainer for a battle royale scored by time, a tie decided by sub-match wins or
a crew battle, and a McMahon Swiss from the stage's settings, and neither public
payload carried any. Only those three keys are sent; the rest stay private.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from .tests_battle_royale import br_stage
from .tests_stage_engine import add_stage, field


class PublicRulesTests(TestCase):
    def test_a_time_scored_battle_royale_says_so_on_both_payloads(self):
        t, creator, regs, stage = br_stage(4, {'lobby_size': 4, 'maps': 1, 'score_by': 'time'})
        detail = APIClient().get('/tournament/view-tournament/%s/' % t.slug).json()['data']
        self.assertEqual(detail['stages'][0]['settings'], {'score_by': 'time'})
        bracket = APIClient().get('/tournament/get-tournament-brackets/%s/' % t.slug).json()['data']
        self.assertEqual(bracket['stages'][0]['settings'], {'score_by': 'time'})

    def test_only_the_explainer_keys_leave_the_server(self):
        t, creator, regs = field(4, 'round_robin')
        add_stage(t, 0, 'round_robin', settings={'points_win': 3, 'points_draw': 1})
        detail = APIClient().get('/tournament/view-tournament/%s/' % t.slug).json()['data']
        self.assertEqual(detail['stages'][0]['settings'], {})

    def test_a_mcmahon_swiss_carries_its_bands(self):
        t, creator, regs = field(8, 'swiss')
        add_stage(t, 0, 'swiss', settings={'mcmahon_bands': 3})
        detail = APIClient().get('/tournament/view-tournament/%s/' % t.slug).json()['data']
        self.assertEqual(detail['stages'][0]['settings'].get('mcmahon_bands'), 3)
