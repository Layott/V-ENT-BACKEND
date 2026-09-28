"""Battle royale end to end: lobbies, scoring, the table, MVPs, match point,
carry-over, advancing and finishing.

CEO, 28 September 2026: "For battle royale, build it end to end you can use a
similar system used in AFC, including how they calculate and how point
systems, mvps, tie breaker and all of that is set ... you don't hardcode what
number of squads can be in a lobby".

The numbers in these tests are worked by hand from AFC's rules, which is the
point: a scoring function tested against itself proves nothing.
"""
from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient

from . import br_engine, stage_engine, stage_settings
from .models import BRMap, BRResult, TournamentRegistration
from .tests import client_for
from .tests_stage_engine import add_stage, draw, field


def rows_for(br_map, finishes, players=None):
    """finishes: {registration: (placement or None, kills)}."""
    out = []
    for reg, (placement, kills) in finishes.items():
        row = {'registration_id': reg.id, 'placement': placement, 'kills': kills,
               'played': placement is not None}
        if players and reg.id in players:
            row['players'] = players[reg.id]
            row.pop('kills')
        out.append(row)
    return out


def enter(br_map, finishes, user, players=None):
    return br_engine.enter_results(br_map, rows_for(br_map, finishes, players), user)


def br_stage(n, settings=None, advances=0, order=0, t=None):
    if t is None:
        t, creator, regs = field(n, 'battle_royale')
    else:
        creator, regs = t.tournament_creator, list(t.registrations.order_by('seed'))
    stage = add_stage(t, order, 'battle_royale', advances=advances, settings=settings or {})
    return t, creator, regs, stage


class LobbyTests(TestCase):
    def test_the_organiser_sets_the_lobby_size_and_seeds_snake(self):
        t, creator, regs, stage = br_stage(10, {'lobby_size': 4, 'maps': 3})
        draw(stage, creator)
        lobbies = list(stage.br_lobbies.all())
        self.assertEqual(len(lobbies), 3)
        seeds = [sorted(s.registration.seed for s in l.seats.all()) for l in lobbies]
        # 1,2,3 then 6,5,4 then 7,8,9 then 10: snake across three lobbies.
        self.assertEqual(seeds, [[1, 6, 7], [2, 5, 8], [3, 4, 9, 10]])
        self.assertEqual(BRMap.objects.filter(lobby__stage=stage).count(), 9)

    def test_any_lobby_size_the_organiser_picks(self):
        for size, lobbies in ((2, 5), (10, 1), (60, 1), (3, 4)):
            t, creator, regs, stage = br_stage(10, {'lobby_size': size, 'maps': 1})
            draw(stage, creator)
            self.assertEqual(stage.br_lobbies.count(), lobbies, size)

    def test_a_lobby_size_out_of_range_is_refused_with_a_code(self):
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('battle_royale', {'lobby_size': 1})
        self.assertEqual((caught.exception.code, caught.exception.field),
                         ('OUT_OF_RANGE', 'lobby_size'))

    def test_moving_a_squad_before_anybody_has_played(self):
        t, creator, regs, stage = br_stage(4, {'lobby_size': 2, 'maps': 1})
        draw(stage, creator)
        br_engine.move_seat(stage, regs[0].id, 2)
        self.assertEqual(stage.br_lobbies.get(number=2).seats.count(), 3)
        lobby2 = stage.br_lobbies.get(number=2)
        seated = [s.registration for s in lobby2.seats.all()]
        enter(lobby2.maps.first(), {seated[0]: (1, 0), seated[1]: (2, 0), seated[2]: (3, 0)},
              creator)
        with self.assertRaises(br_engine.BRError) as caught:
            br_engine.move_seat(stage, regs[0].id, 1)
        self.assertEqual(caught.exception.code, 'LOBBY_HAS_RESULTS')


class ScoringTests(TestCase):
    def setUp(self):
        self.t, self.creator, self.regs, self.stage = br_stage(
            4, {'lobby_size': 4, 'maps': 2, 'per_kill': 1})
        draw(self.stage, self.creator)
        self.m1, self.m2 = BRMap.objects.filter(lobby__stage=self.stage).order_by('number')

    def test_free_fire_table_plus_a_point_a_kill(self):
        a, b, c, d = self.regs
        enter(self.m1, {a: (1, 5), b: (2, 7), c: (3, 0), d: (4, 1)}, self.creator)
        got = {r.registration_id: r.total for r in BRResult.objects.filter(map=self.m1)}
        # 12+5, 9+7, 8+0, 7+1
        self.assertEqual(got, {a.id: 17, b.id: 16, c.id: 8, d.id: 8})

    def test_a_squad_that_did_not_play_gets_no_placement_points(self):
        a, b, c, d = self.regs
        enter(self.m1, {a: (1, 2), b: (2, 0), c: (3, 0)}, self.creator)
        missing = BRResult.objects.get(map=self.m1, registration=d)
        self.assertFalse(missing.played)
        self.assertEqual(missing.total, 0)
        row = {r['registration_id']: r for r in br_engine.standings(self.stage)}[d.id]
        self.assertEqual(row['maps_played'], 0)

    def test_assists_damage_bonus_and_penalty(self):
        self.stage.settings = stage_settings.clean(
            'battle_royale', {'per_kill': 2, 'per_assist': 1, 'per_1000_damage': 0.5})
        self.stage.save(update_fields=['settings'])
        a, b, c, d = self.regs
        br_engine.enter_results(self.m1, [
            {'registration_id': a.id, 'placement': 1, 'kills': 3, 'assists': 2,
             'damage': 2400, 'bonus': 5, 'penalty': 2, 'adjustment_note': 'late'},
            {'registration_id': b.id, 'placement': 2},
        ], self.creator)
        r = BRResult.objects.get(map=self.m1, registration=a)
        # 12 + 3*2 + 2*1 + 2.4*0.5 + 5 - 2
        self.assertEqual(r.placement_points, 12)
        self.assertEqual(r.kill_points, 9.2)
        self.assertEqual(r.total, 24.2)

    def test_a_custom_placement_table(self):
        self.stage.settings = stage_settings.clean(
            'battle_royale', {'placement_preset': 'custom',
                              'placement_points': {'1': 15, '2': 10}})
        self.stage.save(update_fields=['settings'])
        a, b, c, d = self.regs
        enter(self.m1, {a: (1, 0), b: (2, 0), c: (3, 0)}, self.creator)
        got = {r.registration_id: r.total for r in BRResult.objects.filter(map=self.m1)}
        self.assertEqual((got[a.id], got[b.id], got[c.id]), (15, 10, 0))

    def test_changing_the_table_rescores_what_was_entered(self):
        a, b, c, d = self.regs
        enter(self.m1, {a: (1, 0), b: (2, 0)}, self.creator)
        self.stage.settings = stage_settings.clean('battle_royale',
                                                   {'placement_preset': 'pubg_mobile'})
        self.stage.save(update_fields=['settings'])
        br_engine.rescore(self.stage)
        self.assertEqual(BRResult.objects.get(map=self.m1, registration=a).total, 10)

    def test_refusals(self):
        a, b, c, d = self.regs
        cases = [
            ([{'registration_id': a.id, 'placement': 1},
              {'registration_id': b.id, 'placement': 1}], 'PLACEMENT_TWICE'),
            ([{'registration_id': a.id, 'placement': 9}], 'OUT_OF_RANGE'),
            ([{'registration_id': a.id}], 'PLACEMENT_REQUIRED'),
            ([{'registration_id': 999999, 'placement': 1}], 'NOT_IN_THIS_LOBBY'),
            ([{'registration_id': a.id, 'placement': 1},
              {'registration_id': a.id, 'placement': 2}], 'SQUAD_TWICE'),
            ([{'registration_id': a.id, 'played': False}], 'NOBODY_PLAYED'),
            ([{'registration_id': a.id, 'placement': 1, 'kills': 5,
               'players': [{'name': 'x', 'kills': 2}]}], 'KILLS_DO_NOT_ADD_UP'),
            ([], 'NO_RESULTS'),
        ]
        for rows, code in cases:
            with self.assertRaises(br_engine.BRError) as caught:
                br_engine.enter_results(self.m1, rows, self.creator)
            self.assertEqual(caught.exception.code, code, rows)

    def test_a_player_who_is_not_in_the_squad_is_refused(self):
        a, b, c, d = self.regs
        with self.assertRaises(br_engine.BRError) as caught:
            br_engine.enter_results(self.m1, [
                {'registration_id': a.id, 'placement': 1,
                 'players': [{'user_id': b.user_id, 'kills': 1}]}], self.creator)
        self.assertEqual(caught.exception.code, 'PLAYER_NOT_IN_SQUAD')

    def test_squad_kills_are_its_players_kills(self):
        a, b, c, d = self.regs
        enter(self.m1, {a: (1, 0), b: (2, 0)}, self.creator,
              players={a.id: [{'user_id': a.user_id, 'kills': 4, 'damage': 900}]})
        r = BRResult.objects.get(map=self.m1, registration=a)
        self.assertEqual((r.kills, r.damage, r.total), (4, 900, 16))

    def test_re_entering_replaces_rather_than_adds(self):
        a, b, c, d = self.regs
        enter(self.m1, {a: (1, 9), b: (2, 0)}, self.creator)
        enter(self.m1, {a: (2, 0), b: (1, 0)}, self.creator)
        self.assertEqual(BRResult.objects.filter(map=self.m1).count(), 4)
        self.assertEqual(BRResult.objects.get(map=self.m1, registration=a).total, 9)

    def test_a_closed_stage_takes_no_results(self):
        self.stage.status = 'complete'
        self.stage.save(update_fields=['status'])
        a, b, c, d = self.regs
        with self.assertRaises(br_engine.BRError) as caught:
            enter(self.m1, {a: (1, 0)}, self.creator)
        self.assertEqual(caught.exception.code, 'STAGE_CLOSED')


class TableTests(TestCase):
    def setUp(self):
        self.t, self.creator, self.regs, self.stage = br_stage(
            4, {'lobby_size': 4, 'maps': 2, 'per_kill': 1})
        draw(self.stage, self.creator)
        self.m1, self.m2 = BRMap.objects.filter(lobby__stage=self.stage).order_by('number')

    def test_total_then_booyahs_then_kills_then_last_match(self):
        a, b, c, d = self.regs
        # a: 12+0 + 7+0 = 19, one booyah
        # b: 7+3 + 9+0  = 19, no booyah, 3 kills
        # c: 9+1 + 8+1  = 19, no booyah, 2 kills
        # d: 8+0 + 12-1 ...
        enter(self.m1, {a: (1, 0), b: (4, 3), c: (2, 1), d: (3, 0)}, self.creator)
        enter(self.m2, {a: (4, 0), b: (2, 0), c: (3, 1), d: (1, 0)}, self.creator)
        rows = br_engine.standings(self.stage)
        order = [r['registration_id'] for r in rows]
        # d: 8 + 12 = 20 leads on points. a, b, c are level on 19.
        self.assertEqual(order[0], d.id)
        self.assertEqual(order[1:], [a.id, b.id, c.id])
        by = {r['registration_id']: r for r in rows}
        self.assertEqual(by[a.id]['decided_by'], 'points')
        self.assertEqual(by[b.id]['decided_by'], 'placement_count')
        self.assertEqual(by[c.id]['decided_by'], 'total_kills')

    def test_the_organiser_reorders_the_tiebreakers(self):
        self.stage.settings = stage_settings.clean(
            'battle_royale', {'tiebreakers': ['total_kills', 'placement_count']})
        self.stage.save(update_fields=['settings'])
        a, b, c, d = self.regs
        enter(self.m1, {a: (1, 0), b: (4, 3), c: (2, 1), d: (3, 0)}, self.creator)
        enter(self.m2, {a: (4, 0), b: (2, 0), c: (3, 1), d: (1, 0)}, self.creator)
        order = [r['registration_id'] for r in br_engine.standings(self.stage)]
        self.assertEqual(order[1:], [b.id, c.id, a.id])

    def test_an_unknown_tiebreaker_is_refused(self):
        with self.assertRaises(stage_settings.SettingsError):
            stage_settings.clean('battle_royale', {'tiebreakers': ['goal_difference']})

    def test_carried_points_count_in_the_total(self):
        t, creator, regs, stage = br_stage(2, {'lobby_size': 2, 'maps': 1})
        draw(stage, creator)
        seat = stage.br_lobbies.first().seats.get(registration=regs[1])
        seat.carried_points = 10
        seat.save(update_fields=['carried_points'])
        enter(stage.br_lobbies.first().maps.first(), {regs[0]: (1, 0), regs[1]: (2, 0)}, creator)
        by = {r['registration_id']: r for r in br_engine.standings(stage)}
        self.assertEqual((by[regs[0].id]['points'], by[regs[1].id]['points']), (12, 19))


class MvpTests(TestCase):
    def setUp(self):
        self.t, self.creator, self.regs, self.stage = br_stage(
            3, {'lobby_size': 3, 'maps': 2})
        draw(self.stage, self.creator)
        self.m1, self.m2 = BRMap.objects.filter(lobby__stage=self.stage).order_by('number')

    def lines(self, reg, kills, damage=0, assists=0):
        return [{'user_id': reg.user_id, 'kills': kills, 'damage': damage, 'assists': assists}]

    def test_kills_first_then_damage(self):
        a, b, c = self.regs
        enter(self.m1, {a: (1, 0), b: (2, 0), c: (3, 0)}, self.creator,
              players={a.id: self.lines(a, 3, 500), b.id: self.lines(b, 3, 900)})
        mvp = br_engine.map_mvp(self.m1, br_engine.settings_of(self.stage))
        self.assertEqual(mvp['user_id'], b.user_id)

    def test_winning_team_scope(self):
        self.stage.settings = stage_settings.clean('battle_royale', {'mvp_scope': 'winning_team'})
        self.stage.save(update_fields=['settings'])
        a, b, c = self.regs
        enter(self.m1, {a: (1, 0), b: (2, 0), c: (3, 0)}, self.creator,
              players={a.id: self.lines(a, 1), b.id: self.lines(b, 9)})
        mvp = br_engine.map_mvp(self.m1, br_engine.settings_of(self.stage))
        self.assertEqual(mvp['user_id'], a.user_id)

    def test_a_dead_heat_names_nobody(self):
        a, b, c = self.regs
        enter(self.m1, {a: (1, 0), b: (2, 0), c: (3, 0)}, self.creator,
              players={a.id: self.lines(a, 2, 100, 1), b.id: self.lines(b, 2, 100, 1)})
        self.assertIsNone(br_engine.map_mvp(self.m1, br_engine.settings_of(self.stage)))

    def test_stage_mvp_is_most_match_mvps_and_counts_as_a_tiebreaker(self):
        a, b, c = self.regs
        enter(self.m1, {a: (1, 0), b: (2, 0), c: (3, 0)}, self.creator,
              players={a.id: self.lines(a, 1), b.id: self.lines(b, 4)})
        enter(self.m2, {a: (1, 0), b: (2, 0), c: (3, 0)}, self.creator,
              players={a.id: self.lines(a, 1), b.id: self.lines(b, 5)})
        mvp = br_engine.stage_mvp(self.stage)
        self.assertEqual((mvp['user_id'], mvp['match_mvps'], mvp['kills']), (b.user_id, 2, 9))
        by = {r['registration_id']: r for r in br_engine.standings(self.stage)}
        self.assertEqual(by[b.id]['mvp_count'], 2)
        # A person on the page, described the one way everything else is.
        from vent_auth.views_community import _person
        from vent_auth.models import Users
        expected = set(_person(None, Users.objects.get(pk=b.user_id)).keys())
        self.assertEqual(set(mvp['user'].keys()), expected)
        self.assertEqual(set(br_engine.map_mvp(self.m1, br_engine.settings_of(self.stage))['user'].keys()),
                         expected)


class MatchPointTests(TestCase):
    def test_match_point_is_the_reason_only_when_it_is_the_reason(self):
        """Walk, 28 September 2026: 39 behind a champion on 54 read "below the
        squad that won on match point"; it was below on points."""
        rows = [{'champion': True, 'points': 54, 'seed': 2, 'name': 'B'},
                {'champion': False, 'points': 39, 'seed': 1, 'name': 'D'},
                {'champion': False, 'points': 60, 'seed': 3, 'name': 'X'}]
        ranked = br_engine._rank(rows, [])
        self.assertEqual([(r['name'], r['decided_by']) for r in ranked],
                         [('B', None), ('X', 'match_point'), ('D', 'points')])
        # Straight under the champion and behind on points: points.
        ranked = br_engine._rank([dict(r) for r in rows[:2]], [])
        self.assertEqual([(r['name'], r['decided_by']) for r in ranked],
                         [('B', None), ('D', 'points')])

    def test_the_first_win_at_the_threshold_takes_it(self):
        t, creator, regs, stage = br_stage(
            3, {'lobby_size': 3, 'maps': 5, 'match_point': 20})
        draw(stage, creator)
        a, b, c = regs
        maps = list(BRMap.objects.filter(lobby__stage=stage).order_by('number'))
        enter(maps[0], {a: (1, 0), b: (2, 0), c: (3, 0)}, creator)     # a 12
        enter(maps[1], {a: (1, 0), b: (2, 0), c: (3, 0)}, creator)     # a 24: over, but was 12 before
        self.assertFalse(any(r['champion'] for r in br_engine.standings(stage)))
        enter(maps[2], {b: (1, 30), a: (2, 0), c: (3, 0)}, creator)    # b 9+9+12+30: not at threshold before
        self.assertFalse(any(r['champion'] for r in br_engine.standings(stage)))
        enter(maps[3], {a: (1, 0), b: (2, 0), c: (3, 0)}, creator)     # a at 33 wins: champion
        rows = br_engine.standings(stage)
        self.assertEqual(rows[0]['registration_id'], a.id)
        self.assertTrue(rows[0]['champion'])
        # b has more points and is still second: match point decides it.
        self.assertGreater(rows[1]['points'], rows[0]['points'])
        self.assertEqual(rows[1]['decided_by'], 'match_point')

        # The lobby is over although a match is left.
        self.assertTrue(br_engine.finished(stage))


class ChainTests(TestCase):
    def test_lobbies_into_a_final_lobby_with_a_head_start(self):
        t, creator, regs = field(8, 'battle_royale')
        groups = add_stage(t, 0, 'battle_royale', advances=2,
                           settings={'lobby_size': 4, 'maps': 1, 'advance_by': 'lobby',
                                     'carry_over': {'1': 5}})
        final = add_stage(t, 1, 'battle_royale', settings={'lobby_size': 8, 'maps': 1})
        draw(groups, creator)
        for lobby in groups.br_lobbies.all():
            seated = sorted((s.registration for s in lobby.seats.all()), key=lambda r: r.seed)
            enter(lobby.maps.first(),
                  {reg: (i + 1, 0) for i, reg in enumerate(seated)}, creator)
        self.assertTrue(stage_engine.stage_finished(groups))
        chosen = stage_engine.advancing(groups)
        self.assertEqual(len(chosen), 4)
        self.assertEqual([r['lobby_rank'] for r in chosen], [1, 1, 2, 2])
        with transaction.atomic():
            stage_engine.advance(groups, creator)
        final.refresh_from_db()
        seats = {s.registration_id: s.carried_points
                 for s in final.br_lobbies.get().seats.all()}
        self.assertEqual(sorted(seats.values()), [0, 0, 5, 5])

    def test_top_squads_into_a_knockout(self):
        t, creator, regs = field(6, 'battle_royale')
        groups = add_stage(t, 0, 'battle_royale', advances=4,
                           settings={'lobby_size': 6, 'maps': 1})
        ko = add_stage(t, 1, 'single_elimination')
        draw(groups, creator)
        lobby = groups.br_lobbies.get()
        enter(lobby.maps.first(), {r: (r.seed, 0) for r in regs}, creator)
        with transaction.atomic():
            stage_engine.advance(groups, creator)
        ko.refresh_from_db()
        self.assertEqual(ko.matches.count(), 3)

    def test_a_one_format_battle_royale_runs_and_finishes(self):
        t, creator, regs = field(3, 'battle_royale')
        stage = br_engine.ensure_stage(t)
        self.assertIsNotNone(stage)
        self.assertEqual(br_engine.ensure_stage(t).pk, stage.pk)
        stage.settings = stage_settings.clean('battle_royale', {'maps': 1})
        stage.save(update_fields=['settings'])
        draw(stage, creator)
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            stage_engine.finish_last_stage(stage, creator)
        self.assertEqual(caught.exception.code, 'STAGE_NOT_FINISHED')
        a, b, c = regs
        enter(stage.br_lobbies.get().maps.get(), {c: (1, 0), a: (2, 0), b: (3, 0)}, creator)
        stage_engine.finish_last_stage(stage, creator)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        got = {r.id: r.final_position for r in TournamentRegistration.objects.filter(tournament=t)}
        self.assertEqual(got, {c.id: 1, a.id: 2, b.id: 3})

    def test_the_rules_screen_and_the_stage_agree(self):
        from .models import TournamentRuleset
        t, creator, regs = field(3, 'battle_royale')
        TournamentRuleset.objects.create(tournament=t, data={
            'format': 'battle_royale', 'placement_points': {1: 20, 2: 10},
            'points_per_kill': 3, 'matches': 4})
        stage = br_engine.ensure_stage(t)
        s = br_engine.settings_of(stage)
        self.assertEqual((s['placement_points'], s['per_kill'], s['maps']),
                         ({'1': 20, '2': 10}, 3, 4))
        stage.settings = stage_settings.clean('battle_royale', dict(s, per_kill=2))
        stage.save(update_fields=['settings'])
        br_engine.sync_to_ruleset(stage)
        self.assertEqual(TournamentRuleset.objects.get(tournament=t).data['points_per_kill'], 2)

    def test_opening_the_rules_first_keeps_the_games_own_table(self):
        """Walk, 28 September 2026: the page said Free Fire's table, the rules
        screen made PUBG's the first time it was opened, and the stage took
        that as the organiser's own table."""
        from vent_auth.models import Games
        from .models import TournamentRuleset
        for title, preset, first in (('Free Fire', 'free_fire', 12),
                                     ('PUBG Mobile', 'pubg_mobile', 10)):
            t, creator, regs = field(3, 'battle_royale')
            t.tournament_game, _ = Games.objects.get_or_create(game_title=title)
            t.save(update_fields=['tournament_game'])
            before = APIClient().get('/tournament/%s/br/' % t.tournament_id).json()['data']
            self.assertEqual(before['settings']['placement_preset'], preset)
            rules = client_for(creator).get('/tournament/%s/rules/' % t.tournament_id)
            self.assertEqual(rules.status_code, 200)
            data = TournamentRuleset.objects.get(tournament=t).data
            self.assertEqual(data['placement_points']['1'], first, title)
            s = br_engine.settings_of(br_engine.ensure_stage(t))
            self.assertEqual((s['placement_preset'], s['placement_points']['1']), (preset, first))
            self.assertEqual(s['placement_points'], before['settings']['placement_points'])
            # And the tie breakers: the rules screen opened first must not
            # swap the stage's default chain for another one.
            self.assertEqual(s['tiebreakers'], before['settings']['tiebreakers'])
            self.assertEqual(s['tiebreakers'], stage_settings.BR_DEFAULT_TIEBREAKERS)
