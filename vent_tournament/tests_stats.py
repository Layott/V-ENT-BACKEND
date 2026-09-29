"""Tournament stats, derived from the results (inbox 306, 29 September 2026).

What these hold: every number comes from a recorded result; a cancelled match
and a bye count for nothing; a walkover is a win and a loss with no goals; a
knockout won on penalties is a win; ties share a place; the game decides which
boards exist; head to head covers every stage and battle royale; the endpoints
answer exactly as the tournament page does, to every kind of reader.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games

from . import stats
from .models import BracketMatch, BRMap, MatchPlayerStat, TieFixture
from .tests import client_for, make_tournament, make_user, register
from .tests_battle_royale import br_stage, enter
from .tests_stage_engine import draw


def played(t, n, a, b, s1, s2, *, status='completed', winner=None, pens=None, rnd=1, games=None):
    if winner is None and status == 'completed':
        winner = a if s1 > s2 else b if s2 > s1 else None
    return BracketMatch.objects.create(
        tournament=t, round_number=rnd, match_number=n,
        participant_1=a, participant_2=b, winner=winner,
        score_p1=s1, score_p2=s2, status=status,
        penalties_p1=pens[0] if pens else None, penalties_p2=pens[1] if pens else None,
        games=games or [], completed_at=timezone.now(), draw_allowed=True)


class RecordsTests(TestCase):
    def setUp(self):
        self.org = make_user(7000)
        self.t = make_tournament(self.org)
        self.a, self.b, self.c, self.d = [register(self.t, make_user(7001 + i)) for i in range(4)]

    def test_wins_draws_losses_walkovers_and_what_counts_for_nothing(self):
        played(self.t, 1, self.a, self.b, 3, 1)
        played(self.t, 2, self.a, self.c, 2, 2)
        played(self.t, 3, self.a, self.d, 0, 0, status='walkover_p1')
        played(self.t, 4, self.b, self.c, 9, 0, status='cancelled')
        BracketMatch.objects.create(tournament=self.t, round_number=1, match_number=5,
                                    participant_1=self.b, status='bye')
        played(self.t, 6, self.b, self.d, 1, 1, winner=self.d, pens=(3, 4))

        rows = stats.entrant_records(self.t)
        a = rows[self.a.id]
        self.assertEqual((a['played'], a['wins'], a['draws'], a['losses']), (3, 2, 1, 0))
        self.assertEqual(a['walkover_wins'], 1)
        self.assertEqual((a['scored'], a['conceded']), (5, 3), 'a walkover scores nothing')
        self.assertEqual(a['form'], ['W', 'D', 'W'])
        b = rows[self.b.id]
        self.assertEqual((b['played'], b['wins'], b['losses']), (2, 0, 2),
                         'the cancelled match and the bye count for nothing')
        d = rows[self.d.id]
        self.assertEqual((d['wins'], d['draws'], d['walkover_losses']), (1, 0, 1),
                         'won on penalties is a win, not a draw')

    def test_the_longest_streak_and_form_read_in_order(self):
        played(self.t, 1, self.a, self.b, 1, 0)
        played(self.t, 2, self.a, self.c, 1, 0)
        played(self.t, 3, self.a, self.d, 0, 1)
        played(self.t, 4, self.a, self.b, 2, 0, rnd=2)
        a = stats.entrant_records(self.t)[self.a.id]
        self.assertEqual(a['longest_win_streak'], 2)
        self.assertEqual(a['form'], ['W', 'W', 'L', 'W'])

    def test_games_inside_a_series_are_counted(self):
        played(self.t, 1, self.a, self.b, 2, 1, games=[{'p1': 13, 'p2': 9}, {'p1': 7, 'p2': 13},
                                                       {'p1': 13, 'p2': 11}])
        a = stats.entrant_records(self.t)[self.a.id]
        self.assertEqual((a['games_won'], a['games_lost']), (2, 1))

    def test_ties_share_a_place_and_nobody_is_picked_by_database_order(self):
        played(self.t, 1, self.a, self.b, 1, 0)
        played(self.t, 2, self.c, self.d, 1, 0)
        board = next(b for b in stats.compute(self.t)['boards'] if b['key'] == 'wins')
        places = sorted(r['place'] for r in board['rows'] if r['value'] == 1)
        self.assertEqual(places, [1, 1])

    def test_a_rate_needs_enough_matches(self):
        # a: 1 of 1. b: 3 of 4. With somebody on 4 played, 3 is the minimum.
        played(self.t, 1, self.a, self.c, 1, 0)
        for n, opp in enumerate((self.c, self.d, self.c)):
            played(self.t, 10 + n, self.b, opp, 1, 0)
        played(self.t, 20, self.b, self.d, 0, 1)
        board = next(b for b in stats.compute(self.t)['boards'] if b['key'] == 'win_rate')
        leader = board['rows'][0]['entrant']['registration_id']
        self.assertNotEqual(leader, self.a.id, 'one win from one match does not lead')

    def test_records_name_the_biggest_win(self):
        played(self.t, 1, self.a, self.b, 5, 0)
        played(self.t, 2, self.c, self.d, 3, 3)
        rec = stats.compute(self.t)['records']
        self.assertEqual(rec['biggest_win']['margin'], 5)
        self.assertEqual(rec['highest_scoring']['total'], 6)

    def test_an_empty_tournament_claims_no_leader(self):
        out = stats.compute(self.t)
        self.assertFalse(out['has_results'])
        self.assertEqual(out['boards'], [])


class WhatTheGameRecordsTests(TestCase):
    def setUp(self):
        self.org = make_user(7100)

    def test_clean_sheets_only_for_football(self):
        fc, _ = Games.objects.get_or_create(game_title='EA FC 26')
        t = make_tournament(self.org)
        t.tournament_game = fc
        t.save(update_fields=['tournament_game'])
        a, b = register(t, make_user(7101)), register(t, make_user(7102))
        played(t, 1, a, b, 2, 0)
        keys = [x['key'] for x in stats.compute(t)['boards']]
        self.assertIn('clean_sheets', keys)
        self.assertEqual(stats.compute(t)['score_unit'], 'goals')

        cod, _ = Games.objects.get_or_create(game_title='Call of Duty Mobile')
        t2 = make_tournament(self.org)
        t2.tournament_game = cod
        t2.save(update_fields=['tournament_game'])
        c, d = register(t2, make_user(7103)), register(t2, make_user(7104))
        played(t2, 1, c, d, 2, 0)
        self.assertNotIn('clean_sheets', [x['key'] for x in stats.compute(t2)['boards']])

    def test_per_player_numbers_the_game_counts(self):
        t = make_tournament(self.org)
        a, b = register(t, make_user(7110)), register(t, make_user(7111))
        m1 = played(t, 1, a, b, 1, 0)
        m2 = played(t, 2, a, b, 0, 1)
        for match, kills_a, deaths_a in ((m1, 12, 3), (m2, 8, 1)):
            MatchPlayerStat.objects.create(match=match, player=a.user, registration=a, key='kills', value=kills_a)
            MatchPlayerStat.objects.create(match=match, player=a.user, registration=a, key='deaths', value=deaths_a)
        MatchPlayerStat.objects.create(match=m1, player=b.user, registration=b, key='kills', value=15)
        MatchPlayerStat.objects.create(match=m1, player=b.user, registration=b, key='deaths', value=9)
        boards = {x['key']: x for x in stats.compute(t)['boards']}
        kills = boards['metric:kills']
        self.assertEqual((kills['rows'][0]['person']['user_id'], kills['rows'][0]['value']),
                         (a.user_id, 20))
        self.assertEqual(kills['rows'][0]['matches'], 2)
        deaths = boards['metric:deaths']
        self.assertFalse(deaths['metric']['higher_is_better'])
        self.assertEqual(deaths['rows'][0]['person']['user_id'], a.user_id,
                         'fewest deaths leads, because fewer is better')

    def test_the_games_inside_team_ties(self):
        t = make_tournament(self.org, bracket_type='aggregate_2v2')
        a, b = register(t, make_user(7120)), register(t, make_user(7121))
        tie = played(t, 1, a, b, 1, 0)
        p1, p2 = make_user(7122), make_user(7123)
        TieFixture.objects.create(tie=tie, slot=1, player_1=p1, player_2=p2,
                                  goals_1=3, goals_2=1, status='completed')
        TieFixture.objects.create(tie=tie, slot=2, player_1=p1, player_2=p2,
                                  goals_1=2, goals_2=2, status='completed')
        TieFixture.objects.create(tie=tie, slot=3, player_1=p1, player_2=p2,
                                  goals_1=9, goals_2=0, status='scheduled')
        boards = {x['key']: x for x in stats.compute(t)['boards']}
        self.assertEqual(boards['tie_goals']['rows'][0]['value'], 5, 'a game not played is not counted')
        self.assertEqual(boards['tie_wins']['rows'][0]['person']['user_id'], p1.user_id)


class BattleRoyaleStatsTests(TestCase):
    def setUp(self):
        self.t, self.creator, self.regs, self.stage = br_stage(3, {'lobby_size': 3, 'maps': 2})
        draw(self.stage, self.creator)
        self.m1, self.m2 = BRMap.objects.filter(lobby__stage=self.stage).order_by('number')

    def lines(self, reg, kills, damage=0):
        return [{'user_id': reg.user_id, 'kills': kills, 'damage': damage, 'assists': 0}]

    def test_first_places_kills_damage_and_match_mvps(self):
        a, b, c = self.regs
        enter(self.m1, {a: (1, 0), b: (2, 0), c: (3, 0)}, self.creator,
              players={a.id: self.lines(a, 2, 300), b.id: self.lines(b, 6, 900), c.id: self.lines(c, 1)})
        enter(self.m2, {a: (1, 0), b: (3, 0), c: (2, 0)}, self.creator,
              players={a.id: self.lines(a, 7, 1200), b.id: self.lines(b, 1), c.id: self.lines(c, 0)})
        boards = {x['key']: x for x in stats.compute(self.t)['boards']}
        self.assertEqual(boards['br_first_places']['rows'][0]['entrant']['registration_id'], a.id)
        self.assertEqual(boards['br_first_places']['rows'][0]['value'], 2)
        self.assertEqual(boards['br_kills']['rows'][0]['value'], 9)
        self.assertEqual(boards['br_damage']['rows'][0]['value'], 1500)
        mvps = boards['br_mvps']['rows']
        self.assertEqual(sorted(r['value'] for r in mvps), [1, 1], 'one match MVP each')

    def test_the_picker_opens_on_two_who_met(self):
        a, b, c = self.regs
        enter(self.m1, {a: (2, 0), b: (1, 0), c: (3, 0)}, self.creator)
        self.assertEqual(stats.compute(self.t)['suggested_pair'], [b.id, a.id])

    def test_the_whole_field_can_be_compared(self):
        field = [e['registration_id'] for e in stats.compute(self.t)['field']]
        self.assertEqual(sorted(field), sorted(r.id for r in self.regs))

    def test_head_to_head_in_battle_royale_is_who_finished_ahead(self):
        a, b, c = self.regs
        enter(self.m1, {a: (1, 4), b: (2, 1), c: (3, 0)}, self.creator)
        enter(self.m2, {a: (3, 0), b: (1, 5), c: (2, 0)}, self.creator)
        h = stats.head_to_head(self.t, a.id, b.id)['battle_royale']
        self.assertEqual((h['maps'], h['first_ahead'], h['second_ahead']), (2, 1, 1))
        self.assertEqual((h['first_kills'], h['second_kills']), (4, 6))


class HeadToHeadTests(TestCase):
    def setUp(self):
        self.org = make_user(7200)
        self.t = make_tournament(self.org)
        self.a, self.b, self.c = [register(self.t, make_user(7201 + i)) for i in range(3)]

    def test_the_picker_opens_on_the_latest_meeting(self):
        played(self.t, 1, self.a, self.c, 2, 1)
        played(self.t, 2, self.b, self.a, 0, 1, rnd=2)
        self.assertEqual(stats.compute(self.t)['suggested_pair'], [self.b.id, self.a.id])

    def test_every_meeting_from_both_sides(self):
        played(self.t, 1, self.a, self.b, 2, 1)
        played(self.t, 2, self.b, self.a, 1, 1, winner=self.b, pens=(5, 4), rnd=2)
        played(self.t, 3, self.a, self.c, 4, 0)
        h = stats.head_to_head(self.t, self.a.id, self.b.id)
        s = h['summary']
        self.assertEqual((s['played'], s['first_wins'], s['second_wins'], s['draws']), (2, 1, 1, 0))
        self.assertEqual((s['first_scored'], s['second_scored']), (3, 2))
        self.assertEqual(h['meetings'][1]['penalties'], [4, 5], 'from the first side\'s view')
        self.assertIsNone(h['battle_royale'])


class DoorsTests(TestCase):
    """Every kind of reader, the way the tournament page answers them."""

    def setUp(self):
        self.org = make_user(7300)
        self.t = make_tournament(self.org)
        self.a, self.b = register(self.t, make_user(7301)), register(self.t, make_user(7302))
        played(self.t, 1, self.a, self.b, 1, 0)
        self.admin = make_user(7303, staff=True)
        # An admin who overrules an owner has met the authenticator and holds a role.
        self.admin.admin_role = 'mod_admin'
        self.admin.login_session_2fa_at = timezone.now()
        self.admin.save(update_fields=['admin_role', 'login_session_2fa_at'])

    def url(self, t=None, h2h=False):
        t = t or self.t
        return '/tournament/%s/stats/%s' % (t.slug or t.tournament_id, 'head-to-head/' if h2h else '')

    def test_a_stranger_signed_out_reads_a_public_tournament(self):
        res = APIClient().get(self.url())
        self.assertEqual(res.status_code, 200, res.content[:300])
        body = res.json()
        self.assertEqual((body['status'], body['code']), ('success', 'OK'))
        self.assertTrue(body['data']['has_results'])

    def test_a_draft_is_hidden_from_everybody_but_its_organiser_and_an_admin(self):
        self.t.is_draft = True
        self.t.save(update_fields=['is_draft'])
        self.assertEqual(APIClient().get(self.url()).status_code, 404)
        self.assertEqual(client_for(self.a.user).get(self.url()).status_code, 404)
        self.assertEqual(client_for(self.org).get(self.url()).status_code, 200)
        self.assertEqual(client_for(self.admin).get(self.url()).status_code, 200)
        # The same answer the tournament page gives each of them.
        page = '/tournament/view-tournament/%s/' % self.t.tournament_id
        for who in (None, self.a.user, self.org, self.admin):
            c = client_for(who) if who else APIClient()
            self.assertEqual(c.get(self.url()).status_code, c.get(page).status_code, who)

    def test_head_to_head_refusals_carry_codes(self):
        c = APIClient()
        self.assertEqual(c.get(self.url(h2h=True)).json()['code'], 'H2H_PICK_TWO')
        same = c.get(self.url(h2h=True), {'a': self.a.id, 'b': self.a.id})
        self.assertEqual(same.json()['code'], 'H2H_SAME_ENTRANT')
        other = make_tournament(self.org)
        outsider = register(other, make_user(7304))
        res = c.get(self.url(h2h=True), {'a': self.a.id, 'b': outsider.id})
        self.assertEqual((res.status_code, res.json()['code']), (400, 'H2H_NOT_ENTRANT'))
        ok = c.get(self.url(h2h=True), {'a': self.a.id, 'b': self.b.id})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['data']['summary']['first_wins'], 1)

    def test_an_unknown_tournament_is_404_with_a_code(self):
        res = APIClient().get('/tournament/no-such-cup-999/stats/')
        self.assertEqual((res.status_code, res.json()['code']), (404, 'TOURNAMENT_NOT_FOUND'))
