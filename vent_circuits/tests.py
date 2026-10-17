# -*- coding: utf-8 -*-
"""Circuits (P5, design section 13).

"Seasons, regions and tiers; a point table per tier in the form
`1: 300, 2-9: 100, default 10`; a ranking per game and entrant type by points
or Elo; public adjustments with a reason; team events can award points to each
player."

PointTableTests pin the table's words both ways; StandingsTests the ranking
(places from final standings, shared places split, team events to players,
adjustments, what never counts); ApiTests the doors.
"""
import itertools
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, TeamMembers, Teams
from vent_tournament.models import Tournament, TournamentRegistration
from vent_tournament.tests import client_for, make_tournament, make_user

from . import standings, tables
from .models import Circuit, CircuitAdjustment, CircuitTier, CircuitTournament

# Users here are numbered far above every fixed number in the other suites
# (see tests_stage_engine.field): one counter for the whole module.
_IDS = itertools.count(3100000)


class PointTableTests(TestCase):
    def test_the_design_example_reads(self):
        table = tables.parse('1: 300, 2-9: 100, default 10')
        self.assertEqual(tables.points(table, 1), Decimal('300'))
        self.assertEqual(tables.points(table, 2), Decimal('100'))
        self.assertEqual(tables.points(table, 9), Decimal('100'))
        self.assertEqual(tables.points(table, 10), Decimal('10'))
        self.assertEqual(tables.points(table, 500), Decimal('10'))

    def test_written_back_the_way_it_was_typed(self):
        table = tables.parse('1:300 ,2 - 9 : 100\n default 10')
        self.assertEqual(tables.text(table), '1: 300, 2-9: 100, default 10')
        self.assertEqual(tables.parse(tables.text(table)), table)

    def test_without_a_default_unlisted_places_score_nothing(self):
        table = tables.parse('1: 50, 2: 30, 3-4: 10')
        self.assertEqual(tables.points(table, 5), Decimal('0'))
        self.assertEqual(tables.text(table), '1: 50, 2: 30, 3-4: 10')

    def test_halves_are_kept(self):
        table = tables.parse('1: 12.5, default 0.25')
        self.assertEqual(tables.points(table, 1), Decimal('12.5'))
        self.assertEqual(tables.points(table, 2), Decimal('0.25'))

    def test_an_empty_table_is_empty(self):
        self.assertEqual(tables.parse(''), {})
        self.assertEqual(tables.parse('   '), {})
        self.assertEqual(tables.text({}), '')
        self.assertEqual(tables.points({}, 1), Decimal('0'))

    def test_shared_places_split_the_places_they_share(self):
        # Two sides share 3rd: they took places 3 and 4 between them, so each
        # gets the mean of the two, the rule the prize split follows.
        table = tables.parse('1: 300, 2: 200, 3: 100, 4: 60, default 10')
        self.assertEqual(tables.shared(table, 3, 2), Decimal('80'))
        self.assertEqual(tables.shared(table, 1, 1), Decimal('300'))
        # Four share 5th: places 5 to 8 are all the default.
        self.assertEqual(tables.shared(table, 5, 4), Decimal('10'))
        # Three share 2nd: (200 + 100 + 60) / 3, kept to two places.
        self.assertEqual(tables.shared(table, 2, 3), Decimal('120'))
        self.assertEqual(tables.shared(tables.parse('1: 10, 2: 10, 3: 0'), 1, 3), Decimal('6.67'))

    def test_what_cannot_be_read_is_refused_with_the_part(self):
        for text, code, part in (
                ('1: 300, 2-x: 100', 'POINT_TABLE_INVALID', '2-x: 100'),
                ('first: 300', 'POINT_TABLE_INVALID', 'first: 300'),
                ('1 300', 'POINT_TABLE_INVALID', '1 300'),
                ('1: -5', 'POINT_TABLE_RANGE', '1: -5'),
                ('0: 10', 'POINT_TABLE_RANGE', '0: 10'),
                ('1: 1000001', 'POINT_TABLE_RANGE', '1: 1000001'),
                ('1: 10.125', 'POINT_TABLE_INVALID', '1: 10.125'),
                ('9-2: 10', 'POINT_TABLE_ORDER', '9-2: 10'),
                ('1-4: 10, 3: 5', 'POINT_TABLE_OVERLAP', '3: 5'),
                ('1: 10, 1: 5', 'POINT_TABLE_OVERLAP', '1: 5'),
                ('default 5, default 6', 'POINT_TABLE_OVERLAP', 'default 6'),
                ('1-5000: 10', 'POINT_TABLE_RANGE', '1-5000: 10')):
            with self.assertRaises(tables.TableError, msg=text) as caught:
                tables.parse(text)
            self.assertEqual((caught.exception.code, caught.exception.part), (code, part), text)

    def test_a_table_too_long_is_refused(self):
        text = ', '.join('%d: 1' % place for place in range(1, 130))
        with self.assertRaises(tables.TableError) as caught:
            tables.parse(text)
        self.assertEqual(caught.exception.code, 'POINT_TABLE_TOO_LONG')

    def test_a_stored_table_is_checked_again(self):
        # Anything that did not come through parse() is refused, not trusted.
        for raw in ({'rows': [[1, 1, '-3']]}, {'rows': 'x'}, {'default': 'abc'}, ['1: 3'], {'rows': [[2, 1, '3']]}):
            with self.assertRaises(tables.TableError, msg=raw):
                tables.clean(raw)
        self.assertEqual(tables.clean({'rows': [[1, 1, '300']], 'default': '10'}),
                         {'rows': [[1, 1, '300']], 'default': '10'})


# ---------------------------------------------------------------------------
# The points ranking
# ---------------------------------------------------------------------------

def person(**extra):
    user = make_user(next(_IDS))
    for key, value in extra.items():
        setattr(user, key, value)
    if extra:
        user.save(update_fields=list(extra))
    return user


def finished(owner, game, places, *, access='individual'):
    """A completed tournament of `game` whose entrants finished at `places`
    ({key: (user or team, place or None)}). Returns (tournament, {key: registration})."""
    t = make_tournament(owner, status='completed')
    t.tournament_game = game
    t.tournament_access = access
    t.save(update_fields=['tournament_game', 'tournament_access'])
    regs = {}
    for key, (who, place) in places.items():
        if isinstance(who, Teams):
            reg = TournamentRegistration.objects.create(tournament=t, team=who, status='confirmed')
        else:
            reg = TournamentRegistration.objects.create(tournament=t, user=who, status='confirmed')
        if place is not None:
            reg.final_position = place
            reg.save(update_fields=['final_position'])
        regs[key] = reg
    return t, regs


def club(owner, game, *members):
    team = Teams.objects.create(team_name='Club %d' % next(_IDS), team_owner=owner,
                                team_creator=owner, game=game)
    TeamMembers.objects.create(team=team, user=owner, is_captain=True)
    for member in members:
        TeamMembers.objects.create(team=team, user=member)
    return team


def row_of(rows, kind, ident):
    return next(r for r in rows if r['entrant']['type'] == kind and r['entrant']['id'] == ident)


class StandingsTests(TestCase):
    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='EA FC 26')[0]
        self.owner = person()
        self.circuit = Circuit.objects.create(
            name='Naija FC Season 1', owner=self.owner, game=self.game, entrant='player',
            default_table=tables.parse('1: 100, 2: 60, 3: 40, 4: 20, default 5'), is_published=True)
        self.major = CircuitTier.objects.create(
            circuit=self.circuit, name='Major', order=0,
            table=tables.parse('1: 300, 2: 200, 3: 100, 4: 60, default 10'))
        self.a, self.b, self.c, self.d = (person(full_name=n) for n in
                                          ('Ada Obi', 'Bola Ade', 'Chidi Eze', 'Dayo Bello'))

    def link(self, tournament, tier=None):
        return CircuitTournament.objects.create(circuit=self.circuit, tournament=tournament, tier=tier)

    def test_places_go_through_the_tier_table_or_the_default(self):
        t1, _ = finished(self.owner, self.game, {'a': (self.a, 1), 'b': (self.b, 2)})
        t2, _ = finished(self.owner, self.game, {'b': (self.b, 1), 'a': (self.a, 2)})
        self.link(t1, self.major)
        self.link(t2)
        rows = standings.points_table(self.circuit)
        self.assertEqual(row_of(rows, 'user', self.a.user_id)['points'], Decimal('360'))
        self.assertEqual(row_of(rows, 'user', self.b.user_id)['points'], Decimal('300'))
        self.assertEqual([r['entrant']['id'] for r in rows], [self.a.user_id, self.b.user_id])
        self.assertEqual(rows[0]['rank'], 1)
        self.assertEqual(rows[0]['entrant']['name'], 'Ada Obi')
        self.assertEqual(rows[0]['entrant']['handle'], self.a.username)

    def test_shared_places_split_the_points_and_share_the_rank(self):
        t, _ = finished(self.owner, self.game, {'a': (self.a, 1), 'b': (self.b, 2),
                                                'c': (self.c, 3), 'd': (self.d, 3)})
        self.link(t, self.major)
        rows = standings.points_table(self.circuit)
        c, d = row_of(rows, 'user', self.c.user_id), row_of(rows, 'user', self.d.user_id)
        self.assertEqual((c['points'], d['points']), (Decimal('80'), Decimal('80')))
        self.assertEqual((c['rank'], d['rank']), (3, 3))
        self.assertTrue(c['tied'] and d['tied'])
        self.assertEqual(sorted([c['position'], d['position']]), [3, 4])

    def test_each_row_says_where_its_points_came_from(self):
        t, _ = finished(self.owner, self.game, {'a': (self.a, 1), 'b': (self.b, 2)})
        self.link(t, self.major)
        row = row_of(standings.points_table(self.circuit), 'user', self.a.user_id)
        self.assertEqual(row['events'], [{'tournament': {'slug': t.slug, 'title': t.tournament_title},
                                          'tier': 'Major', 'place': 1, 'shared_with': 0,
                                          'points': Decimal('300')}])
        self.assertEqual((row['played'], row['best_place'], row['wins']), (1, 1, 1))

    def test_a_better_single_place_breaks_a_tie_on_points_and_says_so(self):
        # a: 2nd and 2nd (60 + 60); b: 1st and 4th (100 + 20). Level on 120,
        # b's best single place is better.
        t1, _ = finished(self.owner, self.game, {'a': (self.a, 2), 'b': (self.b, 1)})
        t2, _ = finished(self.owner, self.game, {'a': (self.a, 2), 'b': (self.b, 4)})
        self.link(t1)
        self.link(t2)
        rows = standings.points_table(self.circuit)
        self.assertEqual([r['points'] for r in rows], [Decimal('120'), Decimal('120')])
        self.assertEqual(rows[0]['entrant']['id'], self.b.user_id)
        self.assertEqual(rows[1]['decided_by'], 'best_place')
        self.assertFalse(rows[1]['tied'])

    def test_what_has_not_finished_or_was_called_off_counts_nothing(self):
        running, _ = finished(self.owner, self.game, {'a': (self.a, None), 'b': (self.b, None)})
        Tournament.objects.filter(pk=running.pk).update(status='live')
        cancelled, _ = finished(self.owner, self.game, {'a': (self.a, 1)})
        Tournament.objects.filter(pk=cancelled.pk).update(status='cancelled')
        deleted, _ = finished(self.owner, self.game, {'b': (self.b, 1)})
        withdrawn, regs = finished(self.owner, self.game, {'c': (self.c, 1)})
        TournamentRegistration.objects.filter(pk=regs['c'].pk).update(status='withdrawn')
        for t in (running, cancelled, deleted, withdrawn):
            self.link(t)
        Tournament.all_objects.filter(pk=deleted.pk).update(deleted_at=timezone.now())
        self.assertEqual(standings.points_table(self.circuit), [])

    def test_a_team_event_gives_each_player_the_points_and_keeps_them(self):
        team = club(self.a, self.game, self.b)
        t, _ = finished(self.owner, self.game, {'team': (team, 1), 'c': (self.c, 2)},
                        access='team_and_individual')
        self.link(t, self.major)
        rows = standings.points_table(self.circuit)
        for who in (self.a, self.b):
            self.assertEqual(row_of(rows, 'user', who.user_id)['points'], Decimal('300'))
        # Who played was frozen when the circuit first counted it: b leaving
        # keeps b's points, and somebody joining later gets none.
        TeamMembers.objects.filter(team=team, user=self.b).delete()
        TeamMembers.objects.create(team=team, user=self.d)
        rows = standings.points_table(self.circuit)
        self.assertEqual(row_of(rows, 'user', self.b.user_id)['points'], Decimal('300'))
        self.assertFalse(any(r['entrant']['id'] == self.d.user_id for r in rows))

    def test_a_team_circuit_counts_clubs_only(self):
        self.circuit.entrant = 'team'
        self.circuit.save(update_fields=['entrant'])
        team = club(self.a, self.game, self.b)
        t, _ = finished(self.owner, self.game, {'team': (team, 2), 'c': (self.c, 1)},
                        access='team_and_individual')
        self.link(t)
        rows = standings.points_table(self.circuit)
        self.assertEqual([(r['entrant']['type'], r['entrant']['id'], r['points']) for r in rows],
                         [('team', team.team_id, Decimal('60'))])
        self.assertEqual(rows[0]['entrant']['name'], team.team_name)

    def test_adjustments_are_added_with_their_reason(self):
        t, _ = finished(self.owner, self.game, {'a': (self.a, 1), 'b': (self.b, 2)})
        self.link(t)
        CircuitAdjustment.objects.create(circuit=self.circuit, user=self.b, points=Decimal('50'),
                                         reason='Won the community vote', by=self.owner)
        CircuitAdjustment.objects.create(circuit=self.circuit, user=self.a, points=Decimal('-20'),
                                         reason='No-show at the Lagos final', by=self.owner)
        rows = standings.points_table(self.circuit)
        a, b = row_of(rows, 'user', self.a.user_id), row_of(rows, 'user', self.b.user_id)
        self.assertEqual((a['points'], b['points']), (Decimal('80'), Decimal('110')))
        self.assertEqual(rows[0]['entrant']['id'], self.b.user_id)
        self.assertEqual(b['adjustments'], [{'points': Decimal('50'), 'reason': 'Won the community vote'}])
        # Somebody with only an adjustment is in the ranking too.
        CircuitAdjustment.objects.create(circuit=self.circuit, user=self.d, points=Decimal('5'),
                                         reason='Volunteer referee', by=self.owner)
        self.assertEqual(row_of(standings.points_table(self.circuit), 'user', self.d.user_id)['played'], 0)

    def test_a_tournament_of_another_game_counts_nothing(self):
        other = Games.objects.get_or_create(game_title='Free Fire')[0]
        t, _ = finished(self.owner, other, {'a': (self.a, 1)})
        self.link(t)
        self.assertEqual(standings.points_table(self.circuit), [])


# ---------------------------------------------------------------------------
# The doors
# ---------------------------------------------------------------------------

class ApiTests(TestCase):
    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='EA FC 26')[0]
        self.owner = person(full_name='Sadiq Bello')
        self.stranger = person()
        self.player = person(full_name='Ada Obi')

    def as_(self, user):
        return client_for(user) if user is not None else APIClient()

    def make(self, who=None, **body):
        values = {'name': 'Naija FC Season 1', 'game': self.game.game_id, 'entrant': 'player',
                  'season': '2026 Season 1', 'region': 'West Africa',
                  'default_table': '1: 100, 2: 60, default 5'}
        values.update(body)
        return self.as_(who or self.owner).post('/circuits/', values, format='json')

    def made(self, **body):
        res = self.make(**body)
        self.assertEqual(res.status_code, 201, res.content[:300])
        return res.json()['data']['circuit']['slug']

    def publish(self, slug):
        res = self.as_(self.owner).patch('/circuits/%s/' % slug, {'is_published': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_making_one_is_a_draft_with_an_address_from_its_name(self):
        res = self.make()
        self.assertEqual(res.status_code, 201, res.content[:300])
        row = res.json()['data']['circuit']
        self.assertEqual(row['slug'], 'naija-fc-season-1')
        self.assertEqual((row['is_published'], row['can_manage']), (False, True))
        self.assertEqual(row['default_table'], '1: 100, 2: 60, default 5')
        self.assertEqual(row['game']['title'], 'EA FC 26')
        self.assertEqual(self.as_(None).post('/circuits/', {'name': 'x'}, format='json').status_code, 401)

    def test_a_draft_is_answered_like_nothing_to_everybody_but_its_manager(self):
        slug = self.made()
        for who in (None, self.stranger):
            for path in ('/circuits/%s/' % slug, '/circuits/no-such-circuit/'):
                res = self.as_(who).get(path)
                self.assertEqual((res.status_code, res.json()['code']), (404, 'CIRCUIT_NOT_FOUND'), (who, path))
            res = self.as_(who).patch('/circuits/%s/' % slug, {'name': 'Mine'}, format='json')
            self.assertEqual(res.status_code, 401 if who is None else 404)
        self.assertEqual(self.as_(self.owner).get('/circuits/%s/' % slug).status_code, 200)
        listed = self.as_(None).get('/circuits/').json()['data']['circuits']
        self.assertEqual(listed, [])
        mine = self.as_(self.owner).get('/circuits/?mine=1').json()['data']['circuits']
        self.assertEqual([c['slug'] for c in mine], [slug])

    def test_published_it_is_anybodys_to_read_signed_out(self):
        slug = self.made()
        self.publish(slug)
        t, _ = finished(self.owner, self.game, {'a': (self.player, 1), 'b': (self.stranger, 2)})
        draft = make_tournament(self.owner)
        Tournament.objects.filter(pk=draft.pk).update(is_draft=True, status='draft', tournament_game=self.game)
        circuit = Circuit.objects.get(slug=slug)
        CircuitTournament.objects.create(circuit=circuit, tournament=t)
        CircuitTournament.objects.create(circuit=circuit, tournament=draft)
        body = self.as_(None).get('/circuits/%s/' % slug).json()['data']
        self.assertFalse(body['circuit']['can_manage'])
        self.assertEqual([r['entrant']['name'] for r in body['standings']], ['Ada Obi', self.stranger.username])
        self.assertEqual(body['standings'][0]['points'], '100')
        self.assertEqual(body['standings'][0]['events'][0]['points'], '100')
        # A draft tournament in it is its organiser's, never the public's.
        self.assertEqual([x['slug'] for x in body['tournaments']], [t.slug])
        self.assertTrue(body['tournaments'][0]['counts'])
        owner_view = self.as_(self.owner).get('/circuits/%s/' % slug).json()['data']
        self.assertEqual(len(owner_view['tournaments']), 2)
        self.assertEqual([c['slug'] for c in self.as_(None).get('/circuits/').json()['data']['circuits']], [slug])
        # A misspelt search still finds it (the one forgiving matcher).
        typo = self.as_(None).get('/circuits/?q=naija%20fc%20seasn').json()['data']['circuits']
        self.assertEqual([c['slug'] for c in typo], [slug])
        found = self.as_(None).get('/circuits/?tournament=%s' % t.slug).json()['data']['circuits']
        self.assertEqual([c['slug'] for c in found], [slug])
        # The tournament page's line: its tier, and the table it scores by.
        self.assertEqual(found[0]['for_tournament'], {'tier': None, 'table': '1: 100, 2: 60, default 5'})
        self.assertEqual(self.as_(None).get('/circuits/?tournament=no-such-thing').json()['data']['circuits'], [])

    def test_a_renamed_circuit_keeps_its_old_address(self):
        slug = self.made()
        self.publish(slug)
        res = self.as_(self.owner).patch('/circuits/%s/' % slug, {'name': 'Naija FC 2026'}, format='json')
        new = res.json()['data']['circuit']['slug']
        self.assertEqual(new, 'naija-fc-2026')
        moved = self.as_(None).get('/circuits/%s/' % slug).json()
        self.assertEqual((moved['status'], moved['data']['slug']), ('moved', new))

    def test_only_its_manager_changes_it(self):
        slug = self.made()
        self.publish(slug)
        for method in ('patch', 'delete'):
            res = getattr(self.as_(self.stranger), method)('/circuits/%s/' % slug, {'name': 'Mine'}, format='json')
            self.assertEqual((res.status_code, res.json()['code']), (403, 'NOT_YOUR_CIRCUIT'))
        res = self.as_(self.stranger).post('/circuits/%s/tiers/' % slug, {'name': 'Major'}, format='json')
        self.assertEqual(res.status_code, 403)
        admin = person(is_staff=True, admin_role='admin', login_session_2fa_at=timezone.now())
        res = self.as_(admin).patch('/circuits/%s/' % slug, {'season': '2026 Season 2'}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(Circuit.objects.get(slug=slug).name, 'Naija FC Season 1')

    def test_bad_input_is_refused_by_field_with_a_code(self):
        for body, code, field in (({'default_table': '1: 100, 2-x: 5'}, 'POINT_TABLE_INVALID', 'default_table'),
                                  ({'default_table': '1-4: 10, 3: 5'}, 'POINT_TABLE_OVERLAP', 'default_table'),
                                  ({'name': ''}, 'NAME_REQUIRED', 'name'),
                                  ({'region': 'Atlantis'}, 'INVALID_INPUT', 'region'),
                                  ({'entrant': 'robots'}, 'INVALID_INPUT', 'entrant'),
                                  ({'game': 999999}, 'GAME_NOT_FOUND', 'game'),
                                  ({'starts_on': '2026-11-01', 'ends_on': '2026-10-01'},
                                   'CIRCUIT_DATES_ORDER', 'ends_on')):
            res = self.make(**body)
            self.assertEqual((res.status_code, res.json().get('code'), res.json().get('field')),
                             (400, code, field), body)
        res = self.make(default_table='1: 100, 2-x: 5')
        self.assertEqual(res.json()['data']['part'], '2-x: 5')
        self.assertFalse(Circuit.objects.exists())

    def test_tiers(self):
        slug = self.made()
        as_owner = self.as_(self.owner)
        res = as_owner.post('/circuits/%s/tiers/' % slug, {'name': 'Major', 'table': '1: 300, 2-4: 100'},
                            format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        tier = res.json()['data']['tier']
        self.assertEqual(tier['table'], '1: 300, 2-4: 100')
        res = as_owner.post('/circuits/%s/tiers/' % slug, {'name': 'Major'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'TIER_NAME_TAKEN'))
        res = as_owner.patch('/circuits/%s/tiers/%d/' % (slug, tier['id']), {'table': '1: 500'}, format='json')
        self.assertEqual(res.json()['data']['tier']['table'], '1: 500')
        other = self.made(name='Another circuit')
        res = as_owner.patch('/circuits/%s/tiers/%d/' % (other, tier['id']), {'table': '1: 1'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (404, 'TIER_NOT_FOUND'))
        t, _ = finished(self.owner, self.game, {'a': (self.player, 1)})
        as_owner.post('/circuits/%s/tournaments/' % slug, {'tournament': t.slug, 'tier': tier['id']}, format='json')
        self.assertEqual(as_owner.delete('/circuits/%s/tiers/%d/' % (slug, tier['id'])).status_code, 200)
        self.assertIsNone(CircuitTournament.objects.get(tournament=t).tier_id)

    def test_counting_a_tournament(self):
        slug = self.made()
        as_owner = self.as_(self.owner)
        mine, _ = finished(self.owner, self.game, {'a': (self.player, 1)})
        res = as_owner.post('/circuits/%s/tournaments/' % slug, {'tournament': mine.slug}, format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual([x['slug'] for x in res.json()['data']['tournaments']], [mine.slug])
        # Somebody else's tournament, another game, a club circuit and a
        # tournament already counted elsewhere are each refused, by name.
        theirs, _ = finished(self.stranger, self.game, {'a': (self.player, 1)})
        other_game, _ = finished(self.owner, Games.objects.get_or_create(game_title='Free Fire')[0], {})
        refused = (
            ({'tournament': theirs.slug}, 403, 'ONLY_TOURNAMENT_ORGANIZER_CAN'),
            ({'tournament': other_game.slug}, 409, 'CIRCUIT_GAME_MISMATCH'),
            ({'tournament': 'no-such-tournament'}, 404, 'TOURNAMENT_NOT_FOUND'),
            ({'tournament': mine.slug, 'tier': 999999}, 404, 'TIER_NOT_FOUND'),
        )
        for body, http, code in refused:
            res = as_owner.post('/circuits/%s/tournaments/' % slug, body, format='json')
            self.assertEqual((res.status_code, res.json()['code']), (http, code), body)
        second = self.made(name='Second circuit')
        res = as_owner.post('/circuits/%s/tournaments/' % second, {'tournament': mine.slug}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'CIRCUIT_TOURNAMENT_TAKEN'))
        clubs = self.made(name='Club circuit', entrant='team')
        res = as_owner.post('/circuits/%s/tournaments/' % clubs, {'tournament': mine.slug}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'CIRCUIT_ENTRANT_MISMATCH'))
        res = as_owner.delete('/circuits/%s/tournaments/%s/' % (slug, mine.slug))
        self.assertEqual((res.status_code, res.json()['data']['tournaments']), (200, []))
        res = as_owner.delete('/circuits/%s/tournaments/%s/' % (slug, mine.slug))
        self.assertEqual((res.status_code, res.json()['code']), (404, 'TOURNAMENT_NOT_IN_CIRCUIT'))

    def test_adjustments_need_a_reason_and_tell_the_player(self):
        from vent_auth.models import Notification
        slug = self.made()
        as_owner = self.as_(self.owner)
        url = '/circuits/%s/adjustments/' % slug
        for body, http, code in (({'entrant': 'nobody-here', 'points': 5, 'reason': 'Because'}, 404, 'ENTRANT_NOT_FOUND'),
                                 ({'entrant': self.player.username, 'points': 5, 'reason': ''}, 400, 'REASON_REQUIRED'),
                                 ({'entrant': self.player.username, 'points': 0, 'reason': 'Nothing'}, 400, 'ADJUSTMENT_ZERO')):
            res = as_owner.post(url, body, format='json')
            self.assertEqual((res.status_code, res.json()['code']), (http, code), body)
        with self.captureOnCommitCallbacks(execute=True):
            res = as_owner.post(url, {'entrant': '@' + self.player.username, 'points': '-12.5',
                                      'reason': 'No-show at the Lagos final'}, format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        adj = res.json()['data']['adjustments'][0]
        self.assertEqual((adj['points'], adj['reason'], adj['entrant']['name']),
                         ('-12.5', 'No-show at the Lagos final', 'Ada Obi'))
        note = Notification.objects.filter(user=self.player).latest('pk')
        self.assertIn('/circuits/%s' % slug, note.link)
        other = self.made(name='Another circuit')
        res = as_owner.delete('/circuits/%s/adjustments/%d/' % (other, adj['id']))
        self.assertEqual((res.status_code, res.json()['code']), (404, 'ADJUSTMENT_NOT_FOUND'))
        res = as_owner.delete('/circuits/%s/adjustments/%d/' % (slug, adj['id']))
        self.assertEqual((res.status_code, res.json()['data']['adjustments']), (200, []))

    def test_deleting_a_circuit_leaves_its_tournaments(self):
        slug = self.made()
        t, _ = finished(self.owner, self.game, {'a': (self.player, 1)})
        self.as_(self.owner).post('/circuits/%s/tournaments/' % slug, {'tournament': t.slug}, format='json')
        self.assertEqual(self.as_(self.owner).delete('/circuits/%s/' % slug).status_code, 200)
        self.assertFalse(Circuit.objects.exists())
        self.assertTrue(Tournament.objects.filter(pk=t.pk).exists())


# ---------------------------------------------------------------------------
# The Elo engine (P5b)
# ---------------------------------------------------------------------------

def expected(mine, theirs):
    return 1 / (1 + 10 ** ((theirs - mine) / 400))


class EloTests(TestCase):
    def setUp(self):
        self.game = Games.objects.get_or_create(game_title='EA FC 26')[0]
        self.owner = person()
        self.circuit = Circuit.objects.create(name='Naija FC Elo', owner=self.owner, game=self.game,
                                              entrant='player', engine='elo', is_published=True)
        self.a, self.b, self.c = (person(full_name=n) for n in ('Ada Obi', 'Bola Ade', 'Chidi Eze'))
        self.t, self.regs = finished(self.owner, self.game, {'a': (self.a, None), 'b': (self.b, None),
                                                             'c': (self.c, None)})
        CircuitTournament.objects.create(circuit=self.circuit, tournament=self.t)
        self.minute = 0

    def play(self, home, away, winner=None, status='completed', tournament=None, regs=None):
        from vent_tournament.models import BracketMatch
        regs = regs or self.regs
        self.minute += 1
        return BracketMatch.objects.create(
            tournament=tournament or self.t, round_number=1, match_number=self.minute,
            participant_1=regs[home], participant_2=regs[away],
            winner=regs[winner] if winner else None, status=status,
            completed_at=timezone.now() + timedelta(minutes=self.minute))

    def rating(self, rows, user):
        return row_of(rows, 'user', user.user_id)['points']

    def test_a_win_between_equals_moves_sixteen_each_way(self):
        self.play('a', 'b', winner='a')
        rows = standings.table(self.circuit)
        self.assertEqual((self.rating(rows, self.a), self.rating(rows, self.b)), (Decimal('1016'), Decimal('984')))
        self.assertEqual(rows[0]['entrant']['id'], self.a.user_id)
        self.assertEqual((rows[0]['played'], rows[0]['wins']), (1, 1))

    def test_a_draw_between_equals_changes_nothing(self):
        self.play('a', 'b')
        rows = standings.table(self.circuit)
        self.assertEqual((self.rating(rows, self.a), self.rating(rows, self.b)), (Decimal('1000'), Decimal('1000')))

    def test_the_order_matches_were_played_in_decides(self):
        self.play('a', 'b', winner='a')
        self.play('b', 'c', winner='b')
        a = 1000 + 32 * (1 - expected(1000, 1000))
        b = 1000 + 32 * (0 - expected(1000, 1000))
        b2 = b + 32 * (1 - expected(b, 1000))
        c = 1000 + 32 * (0 - expected(1000, b))
        rows = standings.table(self.circuit)
        self.assertEqual(self.rating(rows, self.a), Decimal(round(a)))
        self.assertEqual(self.rating(rows, self.b), Decimal(round(b2)))
        self.assertEqual(self.rating(rows, self.c), Decimal(round(c)))

    def test_a_walkover_and_an_unfinished_match_count_nothing(self):
        self.play('a', 'b', winner='a', status='walkover_p1')
        self.play('a', 'c', winner=None, status='scheduled')
        self.assertEqual(standings.table(self.circuit), [])

    def test_k_comes_from_the_circuit(self):
        self.circuit.settings = {'k': 16}
        self.circuit.save(update_fields=['settings'])
        self.play('a', 'b', winner='a')
        self.assertEqual(self.rating(standings.table(self.circuit), self.a), Decimal('1008'))

    def test_adjustments_move_the_rating_with_their_reason(self):
        self.play('a', 'b', winner='a')
        CircuitAdjustment.objects.create(circuit=self.circuit, user=self.b, points=Decimal('40'),
                                         reason='Replayed after a server crash', by=self.owner)
        rows = standings.table(self.circuit)
        self.assertEqual(self.rating(rows, self.b), Decimal('1024'))
        self.assertEqual(rows[0]['entrant']['id'], self.b.user_id)

    def test_each_tournament_says_how_far_it_moved_the_rating(self):
        self.play('a', 'b', winner='a')
        row = row_of(standings.table(self.circuit), 'user', self.a.user_id)
        # The place is the tournament's own final place, read live: the match
        # signal places the field as results land.
        place = TournamentRegistration.objects.get(pk=self.regs['a'].pk).final_position
        self.assertEqual(row['events'], [{'tournament': {'slug': self.t.slug, 'title': self.t.tournament_title},
                                          'tier': None, 'place': place, 'shared_with': 0,
                                          'points': Decimal('16')}])

    def test_a_player_circuit_rates_one_against_one_only(self):
        team_x, team_y = club(self.a, self.game), club(self.b, self.game)
        t, regs = finished(self.owner, self.game, {'x': (team_x, None), 'y': (team_y, None)},
                           access='team')
        CircuitTournament.objects.create(circuit=self.circuit, tournament=t)
        self.play('x', 'y', winner='x', tournament=t, regs=regs)
        self.assertEqual(standings.table(self.circuit), [])
        self.circuit.entrant = 'team'
        self.circuit.save(update_fields=['entrant'])
        rows = standings.table(self.circuit)
        self.assertEqual([(r['entrant']['type'], r['entrant']['id'], r['points']) for r in rows],
                         [('team', team_x.team_id, Decimal('1016')), ('team', team_y.team_id, Decimal('984'))])

    def test_switching_the_engine_reranks_the_same_results(self):
        self.play('a', 'b', winner='b')
        TournamentRegistration.objects.filter(pk=self.regs['a'].pk).update(final_position=1)
        TournamentRegistration.objects.filter(pk=self.regs['b'].pk).update(final_position=2)
        self.circuit.default_table = tables.parse('1: 100, 2: 60')
        self.circuit.save(update_fields=['default_table'])
        self.assertEqual(standings.table(self.circuit)[0]['entrant']['id'], self.b.user_id)
        res = client_for(self.owner).patch('/circuits/%s/' % self.circuit.slug, {'engine': 'points'}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['standings'][0]['entrant']['id'], self.a.user_id)

    def test_k_is_set_through_the_door_within_bounds(self):
        url = '/circuits/%s/' % self.circuit.slug
        res = client_for(self.owner).patch(url, {'elo_k': 24}, format='json')
        self.assertEqual(res.json()['data']['circuit']['elo_k'], 24)
        res = client_for(self.owner).patch(url, {'elo_k': 500}, format='json')
        self.assertEqual((res.status_code, res.json()['field']), (400, 'elo_k'))


# ---------------------------------------------------------------------------
# A circuit ranking as the source of a progression (P5c)
# ---------------------------------------------------------------------------

class CircuitLineTests(TestCase):
    """Design section 13: "A circuit ranking can be the source of a
    progression: top 8 of the season ranking go to the finals". The same
    Progression row as a feed from another tournament, with the circuit as
    its source; firing it registers the ranking's top places in the target
    exactly as a feed does."""

    def setUp(self):
        from vent_tournament.tests_stage_engine import add_stage
        self.game = Games.objects.get_or_create(game_title='EA FC 26')[0]
        self.owner = person(full_name='Sadiq Bello')
        self.players = [person(full_name=n) for n in ('Ada Obi', 'Bola Ade', 'Chidi Eze', 'Dayo Bello')]
        self.circuit = Circuit.objects.create(
            name='Naija FC Season 1', owner=self.owner, game=self.game, entrant='player',
            default_table=tables.parse('1: 100, 2: 60, 3: 40, 4: 20'), is_published=True)
        season, _ = finished(self.owner, self.game, {str(i): (p, i + 1) for i, p in enumerate(self.players)})
        CircuitTournament.objects.create(circuit=self.circuit, tournament=season)
        self.finals = make_tournament(self.owner)
        self.finals.tournament_game = self.game
        self.finals.save(update_fields=['tournament_game'])
        self.own = [TournamentRegistration.objects.create(tournament=self.finals, user=person(), status='confirmed',
                                                          seed=i + 1) for i in range(2)]
        self.stage = add_stage(self.finals, 0, 'single_elimination', label='Finals',
                               direct=[r.id for r in self.own])

    def line(self, **extra):
        from vent_tournament import feeds
        values = dict(place_from=1, place_to=2, fire='manual')
        values.update(extra)
        return feeds.create_from_circuit(self.stage, self.circuit, self.owner, **values)

    def pull(self, line):
        from django.db import transaction
        from vent_tournament import progressions
        from vent_tournament.models import Progression
        with transaction.atomic():
            progressions.pull(Progression.objects.get(pk=line.pk), self.owner)
        line.refresh_from_db()
        return line

    def made(self, line):
        return list(TournamentRegistration.objects.filter(tournament=self.finals, qualified_via=line)
                    .exclude(status__in=('withdrawn', 'declined')).order_by('source_place', 'id'))

    def test_the_ranking_top_places_are_registered_in_the_target(self):
        from vent_auth.models import Notification
        line = self.line()
        self.assertEqual((line.source_id, line.source_circuit_id, line.target_id),
                         (None, self.circuit.pk, self.stage.pk))
        with self.captureOnCommitCallbacks(execute=True):
            line = self.pull(line)
        made = self.made(line)
        self.assertEqual([(m.user_id, m.source_place) for m in made],
                         [(self.players[0].user_id, 1), (self.players[1].user_id, 2)])
        self.assertTrue(all(m.status == 'confirmed' and m.entry_fee_paid and m.decline_until for m in made))
        self.assertEqual({r['registration_id'] for r in line.sent}, {m.id for m in made})
        self.assertIsNotNone(line.fired_at)
        note = Notification.objects.filter(user=self.players[0]).latest('pk')
        self.assertIn('Naija FC Season 1', note.body)

    def test_refused_by_name(self):
        from vent_tournament import feeds
        other = person()
        theirs = Circuit.objects.create(name='Not mine', owner=other, game=self.game, is_published=True)
        other_game = Games.objects.get_or_create(game_title='Free Fire')[0]
        ff = Circuit.objects.create(name='FF circuit', owner=self.owner, game=other_game, is_published=True)
        clubs = Circuit.objects.create(name='Clubs', owner=self.owner, game=self.game, entrant='team',
                                       is_published=True)
        for circuit, extra, code in ((theirs, {}, 'FEED_NOT_MANAGER'),
                                     (ff, {}, 'FEED_GAME_MISMATCH'),
                                     (clubs, {}, 'FEED_ENTRY_TYPE_MISMATCH'),
                                     (self.circuit, {'place_from': 3, 'place_to': 2}, 'FEED_PLACES'),
                                     (self.circuit, {'on_decline': 'organiser'}, 'FEED_ON_DECLINE')):
            values = dict(place_from=1, place_to=2, fire='manual')
            values.update(extra)
            with self.assertRaises(feeds.FeedError, msg=code) as caught:
                feeds.create_from_circuit(self.stage, circuit, self.owner, **values)
            self.assertEqual(caught.exception.code, code)

    def test_somebody_already_entered_keeps_their_place(self):
        already = TournamentRegistration.objects.create(tournament=self.finals, user=self.players[0],
                                                        status='confirmed')
        line = self.pull(self.line())
        self.assertEqual(TournamentRegistration.objects.filter(tournament=self.finals,
                                                               user=self.players[0]).count(), 1)
        already.refresh_from_db()
        self.assertEqual((already.qualified_via_id, already.source_place), (line.pk, 1))

    def test_the_target_waits_for_the_ranking(self):
        from vent_tournament import progressions
        line = self.line()
        self.assertFalse(progressions.ready(self.stage))
        self.pull(line)
        self.stage.refresh_from_db()
        self.assertTrue(progressions.ready(self.stage) or self.stage.drawn_at is not None)

    def test_a_declined_place_goes_to_the_next_in_the_ranking(self):
        from django.db import transaction
        from vent_tournament import feeds
        line = self.pull(self.line())
        second = next(m for m in self.made(line) if m.source_place == 2)
        with transaction.atomic():
            feeds.decline(second, self.players[1])
        line.refresh_from_db()
        self.assertEqual([(m.user_id, m.source_place) for m in self.made(line)],
                         [(self.players[0].user_id, 1), (self.players[2].user_id, 3)])

    def test_a_recall_hands_the_place_to_the_next_in_the_ranking(self):
        from django.db import transaction
        from vent_tournament import feeds
        line = self.pull(self.line())
        first = next(m for m in self.made(line) if m.source_place == 1)
        preview = feeds.recall_preview(first)
        self.assertEqual(preview['next']['name'], 'Chidi Eze')
        with transaction.atomic():
            feeds.recall(first, self.owner, mode='hand_on')
        line.refresh_from_db()
        self.assertIn(self.players[2].user_id, [m.user_id for m in self.made(line)])

    def test_the_public_feed_rows_name_the_circuit(self):
        from vent_tournament import feeds
        line = self.line()
        row = next(r for r in feeds.rows(self.finals)['incoming'] if r['id'] == line.pk)
        self.assertEqual(row['source']['circuit'], {'slug': self.circuit.slug, 'name': 'Naija FC Season 1'})
        self.assertIsNone(row['source']['stage_id'])
        self.assertEqual(row['target']['stage_id'], self.stage.pk)

    def test_the_doors(self):
        base = '/tournament/%s/feeds/' % self.finals.slug
        res = client_for(self.owner).post(base, {'target_stage': self.stage.pk, 'source_circuit': self.circuit.slug,
                                                 'place_from': 1, 'place_to': 2, 'fire': 'manual'}, format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        feed = res.json()['data']['feed']
        self.assertEqual(feed['source']['circuit']['slug'], self.circuit.slug)
        stranger = person()
        res = client_for(stranger).post('%s%d/pull/' % (base, feed['id']), {}, format='json')
        self.assertEqual(res.status_code, 403)
        res = client_for(self.owner).post('%s%d/pull/' % (base, feed['id']), {}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(TournamentRegistration.objects.filter(tournament=self.finals,
                                                               qualified_via_id=feed['id']).count(), 2)
        res = client_for(self.owner).post('%s%d/pull/' % (base, feed['id']), {}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'ALREADY_ADVANCED'))
        public = self.client.get(base).json()['data']['incoming']
        self.assertEqual([r['id'] for r in public], [feed['id']])
        # The target's bracket reads with seats filled from a ranking.
        res = self.client.get('/tournament/get-tournament-brackets/%s/' % self.finals.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_an_unsent_circuit_line_can_be_removed(self):
        base = '/tournament/%s/feeds/' % self.finals.slug
        line = self.line()
        res = client_for(self.owner).delete('%s%d/' % (base, line.pk))
        self.assertEqual(res.status_code, 200, res.content[:300])
        from vent_tournament.models import Progression
        self.assertFalse(Progression.objects.filter(pk=line.pk).exists())


# ---------------------------------------------------------------------------
# Divisions (P5d)
# ---------------------------------------------------------------------------

class DivisionTests(TestCase):
    """Design section 13: "Promotion and relegation move entrants between
    division circuits across seasons". A league is a ladder of circuits, one
    per division; ending a season copies them and moves the top `up` of each
    division but the top one up, the bottom `down` of each but the bottom one
    down, and tells the people moved. Approved mockup inbox 514."""

    TABLE = '1: 100, 2: 60, 3: 40, 4: 20'

    def setUp(self):
        from .models import CircuitLeague
        from . import leagues
        self.game = Games.objects.get_or_create(game_title='EA FC 26')[0]
        self.owner = person(full_name='Sadiq Bello')
        self.league = CircuitLeague.objects.create(name='Naija FC League', owner=self.owner, game=self.game,
                                                   entrant='player', up=1, down=1, is_published=True)
        self.divisions = []
        self.people = {}
        for name in ('Premier', 'Division 1', 'Division 2'):
            circuit = Circuit.objects.create(
                name='Naija FC %s' % name, owner=self.owner, game=self.game, entrant='player',
                season='2026 Season 1', default_table=tables.parse(self.TABLE), is_published=True)
            CircuitTier.objects.create(circuit=circuit, name='Major', table=tables.parse('1: 300, default 10'))
            leagues.add_division(self.league, circuit)
            players = [person(full_name='%s player %d' % (name, i + 1)) for i in range(4)]
            for p in players:
                leagues.add_member(circuit, ('user', p.user_id))
            self.divisions.append(circuit)
            self.people[circuit.pk] = players

    def play(self, circuit, order):
        """A finished tournament of the division, its players placed in `order`."""
        t, _ = finished(self.owner, self.game, {str(i): (p, i + 1) for i, p in enumerate(order)})
        CircuitTournament.objects.create(circuit=circuit, tournament=t)
        return t

    def play_all(self):
        for circuit in self.divisions:
            self.play(circuit, self.people[circuit.pk])

    def test_divisions_are_numbered_top_first_in_one_season(self):
        self.assertEqual([(c.division, c.league_season) for c in Circuit.objects.filter(league=self.league)
                          .order_by('division')], [(1, 1), (2, 1), (3, 1)])
        # Every season of a league has a "Division 1": the slug says which.
        self.assertEqual(self.divisions[1].slug, 'naija-fc-division-1-2026-season-1')

    def test_a_division_ranks_its_members_from_the_first_day(self):
        premier = self.divisions[0]
        rows = standings.table(premier)
        self.assertEqual(len(rows), 4)
        self.assertEqual({r['points'] for r in rows}, {Decimal('0')})
        # Somebody from another division entering this one's event earns
        # nothing here.
        outsider = self.people[self.divisions[2].pk][0]
        self.play(premier, [outsider] + self.people[premier.pk][:2])
        rows = standings.table(premier)
        self.assertNotIn(outsider.user_id, [r['entrant']['id'] for r in rows])
        self.assertEqual(rows[0]['entrant']['id'], self.people[premier.pk][0].user_id)
        self.assertEqual(rows[0]['points'], Decimal('60'))
        # Read as if in no league (the door that puts everybody in), he is there.
        everyone = standings.table(premier, everyone=True)
        self.assertIn(outsider.user_id, [r['entrant']['id'] for r in everyone])

    def test_on_elo_a_member_who_has_played_nothing_is_on_1000(self):
        premier = self.divisions[0]
        premier.engine = 'elo'
        premier.save(update_fields=['engine'])
        self.assertEqual({r['points'] for r in standings.table(premier)}, {Decimal('1000')})

    def test_the_circuit_page_says_who_goes_up_and_down(self):
        self.play_all()
        zones = []
        for circuit in self.divisions:
            data = self.client.get('/circuits/%s/' % circuit.slug).json()['data']
            zones.append([r['zone'] for r in data['standings']])
            self.assertEqual(data['league']['slug'], self.league.slug)
            self.assertEqual(data['league']['division'], circuit.division)
            self.assertFalse(data['league']['ended'])
        self.assertEqual(zones, [[None, None, None, 'down'], ['up', None, None, 'down'], ['up', None, None, None]])

    def test_no_zones_before_anything_has_counted(self):
        # A new season: everybody on 0 and ranked by name. Nobody is "going
        # down" because of their name.
        data = self.client.get('/circuits/%s/' % self.divisions[1].slug).json()['data']
        self.assertEqual({r['zone'] for r in data['standings']}, {None})
        self.play(self.divisions[1], self.people[self.divisions[1].pk])
        data = self.client.get('/circuits/%s/' % self.divisions[1].slug).json()['data']
        self.assertEqual([r['zone'] for r in data['standings']], ['up', None, None, 'down'])

    def test_the_plan_is_shown_first_and_changes_nothing(self):
        self.play_all()
        res = client_for(self.owner).get('/circuits/leagues/%s/next-season/' % self.league.slug)
        self.assertEqual(res.status_code, 200, res.content[:300])
        plan = res.json()['data']['plan']
        names = lambda rows: [r['entrant']['name'] for r in rows]  # noqa: E731
        self.assertEqual(names(plan[0]['up']), [])
        self.assertEqual(names(plan[0]['down']), ['Premier player 4'])
        self.assertEqual(names(plan[1]['up']), ['Division 1 player 1'])
        self.assertEqual(names(plan[1]['down']), ['Division 1 player 4'])
        self.assertEqual(names(plan[2]['up']), ['Division 2 player 1'])
        self.assertEqual(names(plan[2]['down']), [])
        self.assertEqual(plan[1]['above']['slug'], self.divisions[0].slug)
        self.assertEqual(Circuit.objects.filter(league=self.league).count(), 3)

    def test_ending_the_season_moves_everybody_and_tells_them(self):
        from vent_auth.models import Notification
        from .models import CircuitMember
        self.play_all()
        with self.captureOnCommitCallbacks(execute=True):
            res = client_for(self.owner).post('/circuits/leagues/%s/next-season/' % self.league.slug,
                                              {'season': '2026 Season 2', 'starts_on': '2027-01-05',
                                               'ends_on': '2027-03-28'}, format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(res.json()['data']['moved'], 4)
        following = list(Circuit.objects.filter(league=self.league, league_season=2).order_by('division'))
        self.assertEqual([c.name for c in following], [c.name for c in self.divisions])
        self.assertEqual([c.previous_id for c in following], [c.pk for c in self.divisions])
        self.assertEqual({c.season for c in following}, {'2026 Season 2'})
        self.assertEqual(str(following[0].starts_on), '2027-01-05')
        self.assertEqual([t.name for t in following[0].tiers.all()], ['Major'])
        self.assertFalse(following[0].links.exists())

        def members(circuit):
            return {(m.user.full_name, m.came) for m in CircuitMember.objects.filter(circuit=circuit)
                    .select_related('user')}
        p, d1, d2 = following
        self.assertEqual(members(p), {('Premier player 1', 'stayed'), ('Premier player 2', 'stayed'),
                                      ('Premier player 3', 'stayed'), ('Division 1 player 1', 'up')})
        self.assertEqual(members(d1), {('Premier player 4', 'down'), ('Division 1 player 2', 'stayed'),
                                       ('Division 1 player 3', 'stayed'), ('Division 2 player 1', 'up')})
        self.assertEqual(members(d2), {('Division 1 player 4', 'down'), ('Division 2 player 2', 'stayed'),
                                       ('Division 2 player 3', 'stayed'), ('Division 2 player 4', 'stayed')})
        told = Notification.objects.filter(user=self.people[self.divisions[1].pk][0]).first()
        self.assertIsNotNone(told)
        self.assertIn('Promoted', told.title)
        self.assertFalse(Notification.objects.filter(user=self.people[self.divisions[0].pk][0]).exists())
        # The season that ended now says where people went, and the new one
        # lists everybody on 0 and where they came from.
        old = self.client.get('/circuits/%s/' % self.divisions[1].slug).json()['data']
        self.assertTrue(old['league']['ended'])
        self.assertEqual(old['league']['following']['slug'], d1.slug)
        self.assertEqual([r['zone'] for r in old['standings']], ['up', None, None, 'down'])
        new = self.client.get('/circuits/%s/' % p.slug).json()['data']
        self.assertEqual(len(new['standings']), 4)
        self.assertEqual({r['came'] for r in new['standings']}, {'stayed', 'up'})
        league = self.client.get('/circuits/leagues/%s/' % self.league.slug).json()['data']
        self.assertEqual([s['number'] for s in league['seasons']], [1, 2])
        self.assertEqual(league['season'], 2)
        first = self.client.get('/circuits/leagues/%s/?season=1' % self.league.slug).json()['data']
        self.assertEqual([d['slug'] for d in first['divisions']], [c.slug for c in self.divisions])

    def test_level_at_a_line_is_named(self):
        d1 = self.divisions[1]
        a, b, c, d = self.people[d1.pk]
        # c and d share 3rd: level on points, best place and wins across the
        # line for going down.
        t, regs = finished(self.owner, self.game, {'a': (a, 1), 'b': (b, 2), 'c': (c, 3), 'd': (d, 3)})
        CircuitTournament.objects.create(circuit=d1, tournament=t)
        plan = client_for(self.owner).get('/circuits/leagues/%s/next-season/' % self.league.slug) \
            .json()['data']['plan']
        self.assertEqual(plan[1]['level'], [{'line': 'down', 'names': ['Division 1 player 3', 'Division 1 player 4']}])

    def test_refused_by_name(self):
        from . import leagues
        stranger = person()
        base = '/circuits/leagues/%s/' % self.league.slug
        cases = (
            (client_for(stranger).post(base + 'next-season/', {'season': 'X'}, format='json'), 403, 'NOT_YOUR_LEAGUE'),
            (client_for(self.owner).post(base + 'next-season/', {'season': ''}, format='json'), 400, 'SEASON_REQUIRED'),
            (client_for(self.owner).post(base + 'next-season/', {'season': '2026 Season 1'}, format='json'),
             409, 'SEASON_SAME_NAME'),
            (client_for(self.owner).post(base + 'next-season/', {'season': 'Next', 'starts_on': '2027-02-01',
                                                                 'ends_on': '2027-01-01'}, format='json'),
             400, 'CIRCUIT_DATES_ORDER'),
        )
        for res, http, code in cases:
            self.assertEqual((res.status_code, res.json().get('code')), (http, code), res.content[:300])
        other_game = Games.objects.get_or_create(game_title='Free Fire')[0]
        stray = Circuit.objects.create(name='Free Fire Cup', owner=self.owner, game=other_game, entrant='player')
        res = client_for(self.owner).post(base + 'divisions/', {'circuit': stray.slug}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'CIRCUIT_GAME_MISMATCH'))
        teams = Circuit.objects.create(name='Club Cup', owner=self.owner, game=self.game, entrant='team')
        res = client_for(self.owner).post(base + 'divisions/', {'circuit': teams.slug}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'CIRCUIT_ENTRANT_MISMATCH'))
        # One division per season.
        premier_player = self.people[self.divisions[0].pk][0]
        res = client_for(self.owner).post('/circuits/%s/members/' % self.divisions[1].slug,
                                          {'entrant': premier_player.username}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'ALREADY_IN_DIVISION'))
        self.assertEqual(res.json()['data']['division'], 'Naija FC Premier')
        alone = Circuit.objects.create(name='Lagos Weekly', owner=self.owner, game=self.game, entrant='player')
        res = client_for(self.owner).post('/circuits/%s/members/' % alone.slug,
                                          {'entrant': premier_player.username}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'CIRCUIT_NOT_IN_LEAGUE'))
        # A draft league is nothing to a stranger, and its manager's own.
        self.league.is_published = False
        self.league.save(update_fields=['is_published'])
        self.assertEqual(client_for(stranger).get(base).status_code, 404)
        self.assertEqual(client_for(self.owner).get(base).status_code, 200)
        # An ended season is history: its divisions stay where they are.
        self.league.is_published = True
        self.league.save(update_fields=['is_published'])
        leagues.start_next(self.league, '2026 Season 2')
        res = client_for(self.owner).delete(base + 'divisions/%s/' % self.divisions[2].slug)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'SEASON_OVER'))
        res = client_for(self.owner).post(base + 'divisions/', {'circuit': alone.slug}, format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        self.assertEqual(Circuit.objects.get(pk=alone.pk).league_season, 2)

    def test_the_doors(self):
        owner = person(full_name='Kemi Adeyemi')
        c = client_for(owner)
        res = c.post('/circuits/leagues/', {'name': 'Accra League', 'game': self.game.game_id,
                                            'entrant': 'player', 'up': 2, 'down': 2}, format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        slug = res.json()['data']['league']['slug']
        base = '/circuits/leagues/%s/' % slug
        made = []
        for name in ('Accra Premier', 'Accra Division 1'):
            made.append(c.post('/circuits/', {'name': name, 'game': self.game.game_id, 'season': 'Season 1'},
                               format='json').json()['data']['circuit']['slug'])
            res = c.post(base + 'divisions/', {'circuit': made[-1]}, format='json')
            self.assertEqual(res.status_code, 201, res.content[:300])
        made = [d['slug'] for d in res.json()['data']['divisions']]
        res = c.patch(base + 'divisions/', {'order': list(reversed(made))}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual([d['slug'] for d in res.json()['data']['divisions']], list(reversed(made)))
        res = c.patch(base + 'divisions/', {'order': made[:1]}, format='json')
        self.assertEqual(res.json()['code'], 'DIVISIONS_ORDER')
        top = made[1]
        player = person(full_name='Yaw Mensah')
        res = c.post('/circuits/%s/members/' % top, {'entrant': '@' + player.username}, format='json')
        self.assertEqual(res.status_code, 201, res.content[:300])
        member = res.json()['data']['members'][0]
        self.assertEqual(member['entrant']['handle'], player.username)
        # Everybody the division's tournaments rank, in one press.
        others = [person() for _ in range(3)]
        t, _ = finished(owner, self.game, {str(i): (p, i + 1) for i, p in enumerate([player] + others)})
        CircuitTournament.objects.create(circuit=Circuit.objects.get(slug=top), tournament=t)
        res = c.post('/circuits/%s/members/' % top, {'everyone': True}, format='json')
        self.assertEqual(res.json()['data']['added'], 3)
        # A draft division is its manager's to read.
        self.assertEqual(self.client.get('/circuits/%s/members/' % top).status_code, 404)
        self.assertEqual(len(c.get('/circuits/%s/members/' % top).json()['data']['members']), 4)
        res = c.delete('/circuits/%s/members/%d/' % (top, member['id']))
        self.assertEqual(len(res.json()['data']['members']), 3)
        # Signed out: the league reads, nothing writes.
        self.assertEqual(self.client.get(base).status_code, 404)
        c.patch(base, {'is_published': True}, format='json')
        self.assertEqual(self.client.get(base).status_code, 200)
        self.assertEqual(self.client.post(base + 'next-season/', {'season': 'S2'}, format='json').status_code, 401)
        self.assertEqual(self.client.post('/circuits/%s/members/' % top, {'everyone': True},
                                          format='json').status_code, 401)
        listed = self.client.get('/circuits/leagues/').json()['data']['leagues']
        self.assertIn(slug, [l['slug'] for l in listed])
        mine = c.get('/circuits/leagues/?mine=1').json()['data']['leagues']
        self.assertEqual([l['slug'] for l in mine], [slug])
        # A rename keeps the old address answering.
        res = c.patch(base, {'name': 'Ghana League'}, format='json')
        self.assertEqual(res.json()['data']['league']['slug'], 'ghana-league')
        moved = self.client.get(base).json()
        self.assertEqual((moved['status'], moved['data']['slug']), ('moved', 'ghana-league'))
        # Deleting the league keeps its circuits, out of any league.
        res = c.delete('/circuits/leagues/ghana-league/')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertIsNone(Circuit.objects.get(slug__startswith='accra-premier').league_id)

    def test_leagues_is_never_a_circuit_address(self):
        circuit = Circuit.objects.create(name='Leagues', owner=self.owner, game=self.game)
        self.assertNotEqual(circuit.slug, 'leagues')
        self.assertEqual(self.client.get('/circuits/leagues/').status_code, 200)
