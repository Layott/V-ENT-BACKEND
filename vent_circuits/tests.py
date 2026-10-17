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
