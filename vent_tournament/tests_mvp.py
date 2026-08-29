"""Performance metrics, and the MVP that comes out of them.

PRD section 3: metrics "specific to the game", and an MVP recorded together
with the metrics it was based on.

The decisions worth pinning: a death costs you rather than counting for you,
the defaults follow the game, ties break on the organiser's own order, and an
award that goes against the arithmetic has to say why.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, TeamMembers, Teams, Users, UserWallet

from . import metrics as catalogue
from .models import (BracketMatch, MatchPlayerStat, Tournament,
                     TournamentMetric, TournamentMVP, TournamentRegistration)
from .services import mvp as service


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('v-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    # The wallet id is 10 characters. Truncating the username collides -
    # 'vwmv_alpha1' and 'vwmv_alpha2' are the same string once cut - so it is
    # keyed on the user id, which is unique by construction.
    UserWallet.objects.create(user_wallet_id='w%09d' % user.user_id, user=user,
                              wallet_balance=0)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class MvpBase(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('mv_org')
        self.stranger, self.stranger_auth = a_user('mv_other')
        self.game = Games.objects.create(game_title='Call of Duty: Warzone')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='MVP Probe', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            bracket_type='single_elimination', is_draft=False,
            tournament_access='team', team_size=2)

        self.regs = {}
        self.players = {}
        for name in ('Alpha', 'Bravo'):
            team = Teams.objects.create(
                team_name=name, game=self.game, team_creator=self.organiser,
                team_owner=self.organiser, description='', penalty_points=0,
                number_of_members=2)
            people = []
            for seat in (1, 2):
                player = a_user('mv_%s%d' % (name.lower(), seat))[0]
                TeamMembers.objects.create(team=team, user=player)
                people.append(player)
            self.players[name] = people
            self.regs[name] = TournamentRegistration.objects.create(
                tournament=self.tournament, team=team, status='confirmed')

        self.match = BracketMatch.objects.create(
            tournament=self.tournament, round_number=1, match_number=1,
            participant_1=self.regs['Alpha'], participant_2=self.regs['Bravo'])

    def set_metrics(self, rows, auth=None):
        return self.client.put(
            '/tournament/%s/metrics/' % self.tournament.pk,
            {'metrics': rows}, content_type='application/json',
            **(auth or self.auth))

    def record(self, players, auth=None, match=None):
        return self.client.post(
            '/tournament/%s/matches/%s/stats/'
            % (self.tournament.pk, (match or self.match).pk),
            {'players': players}, content_type='application/json',
            **(auth or self.auth))

    def table(self):
        return self.client.get('/tournament/%s/mvp/' % self.tournament.pk)


class MetricDefaultsTests(MvpBase):
    def test_the_defaults_follow_the_game(self):
        # An organiser opening this should see their own sport, not the union
        # of every sport on the platform.
        keys = [m['key'] for m in self.client.get(
            '/tournament/%s/metrics/' % self.tournament.pk
        ).data['data']['metrics']]
        self.assertIn('kills', keys)
        self.assertIn('placement', keys)
        self.assertNotIn('goals', keys)

    def test_a_football_game_gets_football_metrics(self):
        self.tournament.tournament_game = Games.objects.create(
            game_title='EA FC 25')
        self.tournament.save()
        keys = [m['key'] for m in self.client.get(
            '/tournament/%s/metrics/' % self.tournament.pk
        ).data['data']['metrics']]
        self.assertIn('goals', keys)
        self.assertNotIn('kills', keys)

    def test_an_unknown_game_still_gets_something(self):
        # An empty screen reads as a broken feature rather than as a game the
        # platform has not met.
        self.assertTrue(catalogue.defaults_for_game('Some Game Nobody Named'))
        self.assertTrue(catalogue.defaults_for_game(''))

    def test_a_new_year_in_the_title_does_not_empty_the_list(self):
        self.assertEqual(catalogue.defaults_for_game('EA FC 26'),
                         catalogue.defaults_for_game('EA FC 25'))

    def test_the_defaults_are_marked_as_defaults(self):
        res = self.client.get('/tournament/%s/metrics/' % self.tournament.pk)
        self.assertTrue(res.data['data']['is_default'])

    def test_choosing_them_stops_them_being_defaults(self):
        self.set_metrics([{'key': 'kills', 'weight': 1}, {'key': 'deaths'}])
        res = self.client.get('/tournament/%s/metrics/' % self.tournament.pk)
        self.assertFalse(res.data['data']['is_default'])

    def test_a_mode_beats_the_franchise_it_belongs_to(self):
        # BY_GAME is ordered most specific first and the first match wins.
        # "Call of Duty: Warzone" contains both needles, and it is a battle
        # royale: matching the franchise would drop placement, which is most of
        # how a battle royale is scored. Ordering by needle LENGTH gets this
        # wrong, because 'call of duty' is longer than 'warzone'.
        warzone = catalogue.defaults_for_game('Call of Duty: Warzone')
        multiplayer = catalogue.defaults_for_game('Call of Duty: Black Ops')
        self.assertIn('placement', warzone)
        self.assertNotIn('placement', multiplayer)
        self.assertIn('objectives', multiplayer)

    def test_every_default_key_is_a_real_metric(self):
        # A typo in BY_GAME would silently shorten a game's list rather than
        # raise, because unknown keys are filtered out downstream.
        for needle, keys in catalogue.BY_GAME:
            for key in keys:
                self.assertIsNotNone(catalogue.get(key),
                                     '%s: %s' % (needle, key))
        for key in catalogue.FALLBACK:
            self.assertIsNotNone(catalogue.get(key), key)

    def test_a_death_costs_you(self):
        # Ranking on a raw total would crown whoever died most in a game that
        # pays for damage.
        self.assertLess(catalogue.get('deaths').default_weight, 0)
        self.assertFalse(catalogue.get('deaths').higher_is_better)


class MetricChoiceTests(MvpBase):
    def test_the_organiser_chooses_what_counts(self):
        res = self.set_metrics([
            {'key': 'kills', 'weight': 2},
            {'key': 'objectives', 'weight': 5},
        ])
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(TournamentMetric.objects.count(), 2)

    def test_the_order_they_send_is_the_tiebreak_order(self):
        self.set_metrics([{'key': 'objectives'}, {'key': 'kills'}])
        rows = list(TournamentMetric.objects.filter(
            tournament=self.tournament).order_by('position'))
        self.assertEqual([r.key for r in rows], ['objectives', 'kills'])

    def test_writing_the_list_replaces_it(self):
        # A metric the organiser removed has to stop counting.
        self.set_metrics([{'key': 'kills'}, {'key': 'deaths'}])
        self.set_metrics([{'key': 'kills'}])
        self.assertEqual(
            list(TournamentMetric.objects.filter(tournament=self.tournament)
                 .values_list('key', flat=True)), ['kills'])

    def test_an_unknown_metric_is_refused(self):
        res = self.set_metrics([{'key': 'vibes'}])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'UNKNOWN_METRIC')

    def test_the_same_metric_twice_is_refused(self):
        res = self.set_metrics([{'key': 'kills'}, {'key': 'kills'}])
        self.assertEqual(res.status_code, 400)

    def test_a_weight_that_is_not_a_number_is_refused(self):
        res = self.set_metrics([{'key': 'kills', 'weight': 'lots'}])
        self.assertEqual(res.status_code, 400)

    def test_a_stranger_chooses_nothing(self):
        res = self.set_metrics([{'key': 'kills'}], auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_the_catalogue_comes_back_for_the_picker(self):
        res = self.client.get('/tournament/%s/metrics/' % self.tournament.pk)
        keys = {m['key'] for m in res.data['data']['catalogue']}
        self.assertIn('goals', keys)
        self.assertIn('kills', keys)


class StatRecordingTests(MvpBase):
    def setUp(self):
        super().setUp()
        self.set_metrics([{'key': 'kills', 'weight': 1},
                          {'key': 'deaths', 'weight': -0.5}])

    def test_the_organiser_records_a_stat_line(self):
        res = self.record([{'player': 'mv_alpha1',
                            'stats': {'kills': 10, 'deaths': 2}}])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(MatchPlayerStat.objects.count(), 2)

    def test_the_side_travels_with_the_stat(self):
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 4}}])
        stat = MatchPlayerStat.objects.get(key='kills')
        self.assertEqual(stat.registration_id, self.regs['Alpha'].pk)

    def test_correcting_a_number_updates_rather_than_duplicates(self):
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 4}}])
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 9}}])
        rows = MatchPlayerStat.objects.filter(key='kills')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().value, 9)

    def test_a_metric_this_tournament_does_not_count_is_refused(self):
        # Told at the time, not discovered when the table omits it.
        res = self.record([{'player': 'mv_alpha1', 'stats': {'goals': 3}}])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'METRIC_NOT_COUNTED')

    def test_an_unknown_player_is_a_404(self):
        res = self.record([{'player': 'nobody_at_all', 'stats': {'kills': 1}}])
        self.assertEqual(res.status_code, 404)

    def test_a_value_that_is_not_a_number_is_refused(self):
        res = self.record([{'player': 'mv_alpha1', 'stats': {'kills': 'many'}}])
        self.assertEqual(res.status_code, 400)

    def test_a_stranger_records_nothing(self):
        res = self.record([{'player': 'mv_alpha1', 'stats': {'kills': 1}}],
                          auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(MatchPlayerStat.objects.count(), 0)

    def test_a_match_from_another_tournament_is_a_404(self):
        other = Tournament.objects.create(
            tournament_title='Elsewhere', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            bracket_type='single_elimination', is_draft=False)
        stray = BracketMatch.objects.create(
            tournament=other, round_number=1, match_number=1)
        res = self.record([{'player': 'mv_alpha1', 'stats': {'kills': 1}}],
                          match=stray)
        self.assertEqual(res.status_code, 404)

    def test_the_stat_lines_read_back(self):
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 7, 'deaths': 1}}])
        res = self.client.get('/tournament/%s/matches/%s/stats/'
                              % (self.tournament.pk, self.match.pk))
        self.assertEqual(res.status_code, 200)
        row = res.data['data']['players'][0]
        self.assertEqual(row['username'], 'mv_alpha1')
        self.assertEqual(row['stats']['kills'], 7)


class MvpTableTests(MvpBase):
    def setUp(self):
        super().setUp()
        self.set_metrics([{'key': 'kills', 'weight': 1},
                          {'key': 'deaths', 'weight': -0.5},
                          {'key': 'objectives', 'weight': 2}])

    def test_the_score_is_the_weighted_sum(self):
        self.record([{'player': 'mv_alpha1',
                      'stats': {'kills': 10, 'deaths': 4, 'objectives': 1}}])
        row = self.table().data['data']['table'][0]
        # 10 - 2 + 2
        self.assertEqual(row['score'], 10.0)

    def test_the_breakdown_comes_with_the_score(self):
        # A score with no breakdown is the same unarguable number the PRD is
        # trying to replace.
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 5, 'deaths': 2}}])
        row = self.table().data['data']['table'][0]
        self.assertEqual(row['metrics']['kills'], 5)
        self.assertEqual(row['metrics']['deaths'], 2)

    def test_dying_a_lot_costs_you_the_lead(self):
        self.record([
            {'player': 'mv_alpha1', 'stats': {'kills': 10, 'deaths': 20}},
            {'player': 'mv_bravo1', 'stats': {'kills': 8, 'deaths': 0}},
        ])
        rows = self.table().data['data']['table']
        self.assertEqual(rows[0]['username'], 'mv_bravo1')

    def test_stats_add_up_across_matches(self):
        second = BracketMatch.objects.create(
            tournament=self.tournament, round_number=2, match_number=1,
            participant_1=self.regs['Alpha'], participant_2=self.regs['Bravo'])
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 4}}])
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 6}}],
                    match=second)
        row = self.table().data['data']['table'][0]
        self.assertEqual(row['metrics']['kills'], 10)
        self.assertEqual(row['matches'], 2)

    def test_a_tie_breaks_on_the_organisers_first_metric(self):
        # Same score. Alpha got there on kills, Bravo on objectives, and kills
        # is listed first.
        self.record([
            {'player': 'mv_alpha1', 'stats': {'kills': 4, 'objectives': 0}},
            {'player': 'mv_bravo1', 'stats': {'kills': 0, 'objectives': 2}},
        ])
        rows = self.table().data['data']['table']
        self.assertEqual(rows[0]['score'], rows[1]['score'])
        self.assertEqual(rows[0]['username'], 'mv_alpha1')

    def test_reordering_the_metrics_reorders_the_tie(self):
        self.record([
            {'player': 'mv_alpha1', 'stats': {'kills': 4, 'objectives': 0}},
            {'player': 'mv_bravo1', 'stats': {'kills': 0, 'objectives': 2}},
        ])
        self.set_metrics([{'key': 'objectives', 'weight': 2},
                          {'key': 'kills', 'weight': 1},
                          {'key': 'deaths', 'weight': -0.5}])
        rows = self.table().data['data']['table']
        self.assertEqual(rows[0]['username'], 'mv_bravo1')

    def test_an_unbroken_tie_shares_a_position(self):
        # Printing 3rd and 4th for an unbroken tie asserts a difference the
        # arithmetic did not find.
        self.record([
            {'player': 'mv_alpha1', 'stats': {'kills': 3}},
            {'player': 'mv_bravo1', 'stats': {'kills': 3}},
        ])
        rows = self.table().data['data']['table']
        self.assertEqual(rows[0]['position'], 1)
        self.assertEqual(rows[1]['position'], 1)

    def test_a_metric_the_organiser_removed_stops_counting(self):
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 5, 'deaths': 4}}])
        self.set_metrics([{'key': 'kills', 'weight': 1}])
        row = self.table().data['data']['table'][0]
        self.assertEqual(row['score'], 5.0)
        self.assertNotIn('deaths', row['metrics'])

    def test_putting_it_back_counts_it_again(self):
        # The rows survived, so the metric returning restores the number rather
        # than starting it from zero.
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 5, 'deaths': 4}}])
        self.set_metrics([{'key': 'kills', 'weight': 1}])
        self.set_metrics([{'key': 'kills', 'weight': 1},
                          {'key': 'deaths', 'weight': -0.5}])
        row = self.table().data['data']['table'][0]
        self.assertEqual(row['score'], 3.0)

    def test_the_table_is_public(self):
        # An MVP only its organiser can see is a trophy in a drawer.
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 1}}])
        res = self.client.get('/tournament/%s/mvp/' % self.tournament.pk)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['data']['table']), 1)

    def test_no_stats_is_an_empty_table_not_an_error(self):
        res = self.table()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['table'], [])


class MvpAwardTests(MvpBase):
    def setUp(self):
        super().setUp()
        self.set_metrics([{'key': 'kills', 'weight': 1}])
        self.record([
            {'player': 'mv_alpha1', 'stats': {'kills': 10}},
            {'player': 'mv_bravo1', 'stats': {'kills': 3}},
        ])

    def award(self, payload=None, auth=None):
        return self.client.post(
            '/tournament/%s/mvp/award/' % self.tournament.pk, payload or {},
            content_type='application/json', **(auth or self.auth))

    def test_the_award_defaults_to_the_top_of_the_table(self):
        res = self.award()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['data']['award']['username'], 'mv_alpha1')
        self.assertFalse(res.data['data']['award']['overridden'])

    def test_the_score_is_kept_with_the_award(self):
        # Stats can be corrected afterwards and the award should not silently
        # start disagreeing with itself.
        self.award()
        award = TournamentMVP.objects.get()
        self.assertEqual(award.score, 10.0)
        self.record([{'player': 'mv_alpha1', 'stats': {'kills': 1}}])
        award.refresh_from_db()
        self.assertEqual(award.score, 10.0)

    def test_an_override_has_to_say_why(self):
        res = self.award({'player': 'mv_bravo1'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'REASON_REQUIRED')

    def test_an_override_with_a_reason_is_recorded_as_one(self):
        res = self.award({'player': 'mv_bravo1',
                          'reason': 'Carried the team through the lower bracket.'})
        self.assertEqual(res.status_code, 201, res.data)
        award = TournamentMVP.objects.get()
        self.assertTrue(award.overridden)
        self.assertIn('lower bracket', award.reason)

    def test_naming_the_top_player_is_not_an_override(self):
        res = self.award({'player': 'mv_alpha1'})
        self.assertEqual(res.status_code, 201, res.data)
        self.assertFalse(TournamentMVP.objects.get().overridden)

    def test_awarding_twice_replaces_rather_than_duplicates(self):
        self.award()
        self.award({'player': 'mv_bravo1', 'reason': 'Reconsidered.'})
        self.assertEqual(TournamentMVP.objects.count(), 1)
        self.assertEqual(TournamentMVP.objects.get().player.username,
                         'mv_bravo1')

    def test_a_stranger_awards_nothing(self):
        res = self.award(auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_the_award_shows_on_the_public_table(self):
        self.award()
        res = self.client.get('/tournament/%s/mvp/' % self.tournament.pk)
        self.assertEqual(res.data['data']['award']['username'], 'mv_alpha1')

    def test_nothing_to_award_when_nothing_was_recorded(self):
        MatchPlayerStat.objects.all().delete()
        res = self.award()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'NO_STATS')

    def test_an_unknown_tournament_is_a_404(self):
        res = self.client.post('/tournament/999999/mvp/award/', {},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 404)
