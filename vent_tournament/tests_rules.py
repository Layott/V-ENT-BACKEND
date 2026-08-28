"""An organiser setting up their own rules.

The requirement, in the CEO's words: "users to be able to setup their own point
systems, bracket, tie breakers and change and arrange as they want nothing
rigid, all editable".

So the tests are mostly about what an organiser is ALLOWED to do, which is
nearly everything, and the short list of things that are refused because they
would produce a standings table nobody can explain.
"""
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from vent_auth.models import Games, Users

from . import rules as rules_mod
from . import tiebreak
from .models import BracketMatch, Tournament, TournamentRegistration, TournamentRuleset


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('tok-%s' % name)[:16], **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class RulesetValidationTests(TestCase):
    def test_an_organiser_may_set_any_points_they_like(self):
        """Fifteen for a win is somebody's league. Refusing it would be this
        code deciding it knows their sport better than they do."""
        cleaned = rules_mod.clean({
            'format': 'round_robin',
            'points': {'win': 15, 'draw': 7, 'loss': 2},
        })
        self.assertEqual(cleaned['points'], {'win': 15, 'draw': 7, 'loss': 2})

    def test_zero_for_a_win_is_allowed_because_it_is_theirs_to_get_wrong(self):
        cleaned = rules_mod.clean({'format': 'round_robin', 'points': {'win': 0}})
        self.assertEqual(cleaned['points']['win'], 0)

    def test_the_tiebreaker_order_is_the_setting(self):
        cleaned = rules_mod.clean({
            'format': 'round_robin',
            'tiebreakers': ['goals_for', 'head_to_head', 'goal_difference'],
        })
        self.assertEqual(
            cleaned['tiebreakers'],
            ['goals_for', 'head_to_head', 'goal_difference'])

    def test_a_tiebreaker_that_does_not_exist_is_refused(self):
        """It would produce a standings table nobody can explain."""
        with self.assertRaises(rules_mod.RulesetError) as caught:
            rules_mod.clean({'format': 'round_robin', 'tiebreakers': ['vibes']})
        self.assertEqual(caught.exception.field, 'tiebreakers')

    def test_the_same_tiebreaker_twice_is_refused(self):
        with self.assertRaises(rules_mod.RulesetError):
            rules_mod.clean({
                'format': 'round_robin',
                'tiebreakers': ['head_to_head', 'head_to_head'],
            })

    def test_a_placement_table_can_be_anything_the_organiser_wants(self):
        cleaned = rules_mod.clean({
            'format': 'battle_royale',
            'placement_points': {1: 25, 2: 20, 3: 15, 4: 10, 5: 5},
            'points_per_kill': 2,
        })
        self.assertEqual(cleaned['placement_points'][1], 25)
        self.assertEqual(cleaned['points_per_kill'], 2)

    def test_a_placement_table_keyed_by_nonsense_is_refused(self):
        with self.assertRaises(rules_mod.RulesetError):
            rules_mod.clean({
                'format': 'battle_royale',
                'placement_points': {'first': 10},
            })

    def test_an_empty_placement_table_is_refused(self):
        with self.assertRaises(rules_mod.RulesetError):
            rules_mod.clean({'format': 'battle_royale', 'placement_points': {}})

    def test_an_unknown_format_is_refused(self):
        with self.assertRaises(rules_mod.RulesetError):
            rules_mod.clean({'format': 'battle_chess'})

    def test_a_preset_is_a_copy_not_the_preset_itself(self):
        """Editing one tournament must not change the next one."""
        first = rules_mod.preset_for('battle_royale')
        first['placement_points'][1] = 999
        second = rules_mod.preset_for('battle_royale')
        self.assertNotEqual(second['placement_points'][1], 999)


class ScoringByTheOrganisersRulesTests(TestCase):
    def test_the_table_pays_what_the_organiser_set(self):
        ruleset = rules_mod.clean({
            'format': 'round_robin',
            'points': {'win': 10, 'draw': 4, 'loss': 1},
        })
        table = rules_mod.build_table(ruleset, [
            {'a': 1, 'b': 2, 'score_a': 2, 'score_b': 0},
            {'a': 1, 'b': 3, 'score_a': 1, 'score_b': 1},
        ])
        self.assertEqual(table[1].points, 14)   # a win and a draw
        self.assertEqual(table[2].points, 1)    # a loss still pays here
        self.assertEqual(table[3].points, 4)

    def test_a_custom_placement_table_is_what_gets_paid(self):
        ruleset = rules_mod.clean({
            'format': 'battle_royale',
            'placement_points': {1: 25, 2: 20},
            'points_per_kill': 3,
        })
        table = rules_mod.build_table(ruleset, [
            {'participant': 1, 'placement': 1, 'kills': 4},
        ])
        self.assertEqual(table[1].points, 25 + 12)

    def test_the_organisers_tiebreak_order_decides_the_table(self):
        """Same results, two orders, two different winners."""
        # 1 and 2 finish level on three points. 2 won the meeting; 1 has by far
        # the better difference. Which of them wins the tournament depends
        # entirely on the order the organiser put the tie-breakers in.
        results = [
            {'a': 1, 'b': 3, 'score_a': 9, 'score_b': 0},   # 1: 3 pts, +9 then +8
            {'a': 2, 'b': 1, 'score_a': 1, 'score_b': 0},   # 2: 3 pts, +1
        ]
        base = {'format': 'round_robin', 'points': {'win': 3, 'draw': 1, 'loss': 0}}

        h2h_first = rules_mod.clean(
            dict(base, tiebreakers=['head_to_head', 'goal_difference']))
        gd_first = rules_mod.clean(
            dict(base, tiebreakers=['goal_difference', 'head_to_head']))

        by_h2h = tiebreak.standings(
            rules_mod.build_table(h2h_first, results), h2h_first['tiebreakers'])
        by_gd = tiebreak.standings(
            rules_mod.build_table(gd_first, results), gd_first['tiebreakers'])

        self.assertEqual(by_h2h[0]['participant_id'], 2)   # 2 beat 1
        self.assertEqual(by_gd[0]['participant_id'], 1)    # 1 has +9
        self.assertEqual(by_h2h[0]['separated_by'], 'head_to_head')
        self.assertEqual(by_gd[0]['separated_by'], 'goal_difference')


class RulesEndpointTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('rules_owner')
        self.other, self.other_auth = a_user('rules_stranger')
        self.admin, self.admin_auth = a_user(
            'rules_admin', is_staff=True, admin_role='super_admin')
        game = Games.objects.get_or_create(game_title='Rules Probe')[0]
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Rules Probe Cup', tournament_creator=self.owner,
            tournament_game=game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='round_robin',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3),
            is_draft=False,
        )

    def url(self, suffix=''):
        return '/tournament/%s/rules/%s' % (self.tournament.tournament_id, suffix)

    def test_anybody_can_read_the_rules(self):
        """A player deciding whether to enter should see what a win is worth
        before they pay an entry fee."""
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(data['rules']['format'], 'round_robin')
        self.assertTrue(data['available_tiebreakers'])

    def test_the_owner_can_change_everything(self):
        res = self.client.put(self.url('set/'), data={'rules': {
            'format': 'round_robin',
            'points': {'win': 5, 'draw': 2, 'loss': 1},
            'tiebreakers': ['goals_for', 'head_to_head'],
        }}, content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        saved = TournamentRuleset.objects.get(tournament=self.tournament).data
        self.assertEqual(saved['points']['win'], 5)
        self.assertEqual(saved['tiebreakers'], ['goals_for', 'head_to_head'])

    def test_a_stranger_cannot(self):
        res = self.client.put(self.url('set/'), data={'rules': {
            'format': 'round_robin', 'points': {'win': 99},
        }}, content_type='application/json', **self.other_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_changing_the_format_moves_the_tournament_with_it(self):
        """The two must not be able to disagree about what is being played."""
        res = self.client.put(self.url('set/'), data={'rules': {
            'format': 'battle_royale',
            'placement_points': {1: 12, 2: 9},
            'points_per_kill': 1,
        }}, content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.bracket_type, 'battle_royale')

    def test_a_bad_ruleset_says_which_field(self):
        res = self.client.put(self.url('set/'), data={'rules': {
            'format': 'round_robin', 'tiebreakers': ['nonsense'],
        }}, content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'tiebreakers')

    def test_reset_puts_the_preset_back(self):
        self.client.put(self.url('set/'), data={'rules': {
            'format': 'round_robin', 'points': {'win': 99},
        }}, content_type='application/json', **self.owner_auth)
        res = self.client.post(self.url('reset/'), content_type='application/json',
                               **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['rules']['points']['win'], 3)

    def test_the_rules_lock_once_something_has_been_played(self):
        """Rewriting the points table after results restates every standing
        without touching a single result."""
        reg = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.owner, status='confirmed')
        BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=reg, status='completed')

        res = self.client.put(self.url('set/'), data={'rules': {
            'format': 'round_robin', 'points': {'win': 50},
        }}, content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'RESULTS_ALREADY_RECORDED')

    def test_an_admin_can_still_change_them_after_results(self):
        """Somebody has to be able to correct a genuine mistake."""
        reg = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.owner, status='confirmed')
        BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=reg, status='completed')

        res = self.client.put(self.url('set/'), data={'rules': {
            'format': 'round_robin', 'points': {'win': 50},
        }}, content_type='application/json', **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_presets_endpoint_offers_every_format(self):
        res = self.client.get('/tournament/rule-presets/')
        self.assertEqual(res.status_code, 200, res.content)
        presets = res.json()['data']['presets']
        self.assertIn('battle_royale', presets)
        self.assertIn('aggregate_2v2', presets)
        self.assertTrue(res.json()['data']['placement_presets'])
