"""Three of the eight formats the wizard offers were stored as single elimination.

CEO, 2 September 2026, with a screenshot of the rules panel reading "One loss
and you are out" on a tournament created as an aggregate league:

    "this option was set during creation of the tournament and it still ended
    up reverting to this, does it mean those structure was just fake or these
    are the fake ones."

Neither was fake. The wizard sent `bracket_type=aggregate_2v2` with the points
and the tiebreak order the organiser typed. `create_tournament` ran the value
through `normalize_bracket_type`, which kept its own alias map in `views.py`,
and that map had never learned `gsl`, `aggregate_2v2` or `ladder`. Anything it
did not know became the default, `single_elimination`. From then on every
reader was honest about a wrong fact: `_wants_league` said no, so the points
and tiebreakers were dropped and no LeagueRules row was made; the rules panel
built its preset from `single_elimination`; the standings would have been a
knockout bracket. Rows 26, 28 and 29 on production all carry it.

`tests_formats_alias.py` already pinned `formats.get` for every wizard value.
It could not see this, because the creation path used a second map. There is
now one resolver, `formats.get`, and this file pins the creation path, the
edit path, the label and the league predicate against it.
"""
import json
from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from vent_auth.models import Games, UserWallet, Users
from vent_tournament import formats
from vent_tournament.models import LeagueRules, Tournament
from vent_tournament.tests_formats_alias import WIZARD_VALUES
from vent_tournament.views import (
    _wants_league, bracket_label, normalize_bracket_type)

# The formats decided by a table rather than by a bracket. Everything the
# wizard collects on its league step belongs to exactly these.
TABLE_FORMATS = {'round_robin', 'aggregate_2v2', 'ladder'}


class NormaliserAgreesWithTheFormatCatalogue(SimpleTestCase):
    def test_every_wizard_value_survives_creation(self):
        for value in WIZARD_VALUES:
            self.assertEqual(normalize_bracket_type(value),
                             formats.get(value).key, value)

    def test_the_three_that_were_lost(self):
        self.assertEqual(normalize_bracket_type('aggregate_2v2'), 'aggregate_2v2')
        self.assertEqual(normalize_bracket_type('gsl'), 'gsl')
        self.assertEqual(normalize_bracket_type('ladder'), 'ladder')

    def test_old_spellings_still_resolve(self):
        for value in ('Single Elimination', 'single-elimination', 'swiss-system',
                      'Round Robin', 'battle-royale', 'free_for_all'):
            self.assertIsNotNone(formats.get(normalize_bracket_type(value)), value)

    def test_nonsense_falls_back_to_the_default(self):
        self.assertEqual(normalize_bracket_type('best of the best'), 'single_elimination')
        self.assertEqual(normalize_bracket_type(''), 'single_elimination')
        self.assertEqual(normalize_bracket_type(None), 'single_elimination')

    def test_the_caller_may_choose_the_fallback(self):
        # The edit path falls back to what the row already holds.
        self.assertEqual(normalize_bracket_type('nonsense', 'ladder'), 'ladder')

    def test_the_label_is_the_format_label(self):
        for value in WIZARD_VALUES:
            self.assertEqual(bracket_label(value), formats.get(value).label, value)

    def test_a_table_format_wants_a_league_and_nothing_else_does(self):
        for key, definition in formats.FORMATS.items():
            self.assertEqual(_wants_league(key), key in TABLE_FORMATS, key)
            self.assertEqual(definition.advancement == 'table', key in TABLE_FORMATS, key)


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('f-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(user_wallet_id='w%09d' % user.user_id, user=user,
                              wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class CreatingTheThreeFormats(TestCase):
    """The wizard's request, as it sends it, for the formats that were lost."""

    def setUp(self):
        self.organiser, self.auth = a_user('fmt_org')
        Games.objects.create(game_title='EA FC FN')
        now = timezone.now()
        self.payload = {
            'tournament_title': 'Rivalry Series',
            'game': 'EA FC FN',
            'game_mode': '2v2 Aggregate',
            'tournament_description': 'five nations',
            'tournament_type': 'online',
            'start_date_and_time': (now + timedelta(days=7)).isoformat(),
            'end_date_and_time': (now + timedelta(days=8)).isoformat(),
            'entry_type': 'Free',
            'tournament_visibility': 'public',
            'tournament_access': 'team',
            'team_size': 2,
            'min_number_of_participants': 2,
            'max_number_of_participants': 5,
            'is_draft': '0',
        }

    def create(self, **overrides):
        body = dict(self.payload, **overrides)
        res = self.client.post('/tournament/create-tournament/', body, **self.auth)
        self.assertEqual(res.status_code, 201, res.content[:300])
        return Tournament.objects.get(tournament_id=res.data['data']['tournament_id'])

    def test_an_aggregate_league_is_stored_as_one_with_its_rules(self):
        t = self.create(
            bracket_type='aggregate_2v2',
            points_win='3', points_draw='1', points_loss='0',
            players_per_team='2',
            tiebreakers=json.dumps(['head_to_head', 'goal_difference', 'goals_for']),
        )
        self.assertEqual(t.bracket_type, 'aggregate_2v2')
        rules = LeagueRules.objects.get(tournament=t)
        self.assertEqual((rules.points_win, rules.points_draw, rules.points_loss), (3, 1, 0))
        self.assertEqual(rules.players_per_team, 2)
        self.assertEqual(rules.tiebreakers, ['head_to_head', 'goal_difference', 'goals_for'])

    def test_gsl_and_ladder_keep_their_names(self):
        self.assertEqual(self.create(bracket_type='gsl').bracket_type, 'gsl')
        self.assertEqual(self.create(bracket_type='ladder').bracket_type, 'ladder')

    def test_the_rules_panel_is_built_from_the_format_that_was_chosen(self):
        t = self.create(bracket_type='aggregate_2v2', players_per_team='2')
        res = self.client.get('/tournament/%s/rules/' % t.tournament_id, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        rules = res.data['data']['rules']
        self.assertEqual(rules['format'], 'aggregate_2v2')
        self.assertEqual(rules['scoring'], 'aggregate_goals')

    def test_the_public_payload_names_the_format(self):
        t = self.create(bracket_type='aggregate_2v2', players_per_team='2')
        res = self.client.get('/tournament/view-tournament/%s/' % t.tournament_id)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.data['data']['bracket_type'], 'aggregate_2v2')
        self.assertEqual(res.data['data']['format_label'], 'Aggregate tie')

    def test_editing_to_a_league_format_keeps_it(self):
        t = self.create(bracket_type='single_elimination')
        res = self.client.put(
            '/tournament/edit-tournament/%d/' % t.tournament_id,
            data={'bracket_type': 'ladder'}, content_type='application/json',
            **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        t.refresh_from_db()
        self.assertEqual(t.bracket_type, 'ladder')
