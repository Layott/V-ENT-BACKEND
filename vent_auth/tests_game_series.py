"""A game has editions, and admins keep both lists.

EA FC ships a new edition every year and the catalogue held "EA FC 24" and
"EA FC 25" as two unrelated rows, so nothing tied this year's game to last
year's and the dropdown grew an entry a year.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from .models import GameMode, GameSeries, Games, Users


def an_admin(role='mod_admin', token='game-admin-grant'):
    user = Users.objects.create(
        username='adm_%s' % uuid.uuid4().hex[:6],
        email='adm_%s@vent.test' % uuid.uuid4().hex[:6],
        is_staff=True, admin_role=role,
        login_session_token=token,
    )
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save(update_fields=['login_session_created_at', 'login_session_2fa_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % token}


class GameSeriesModelTests(TestCase):
    def test_two_games_may_share_an_edition_name(self):
        """"2025" is a perfectly normal edition name for more than one game."""
        a = Games.objects.get_or_create(game_title='Game A')[0]
        b = Games.objects.get_or_create(game_title='Game B')[0]
        GameSeries.objects.get_or_create(game=a, name='2025')[0]
        GameSeries.objects.get_or_create(game=b, name='2025')[0]
        self.assertEqual(GameSeries.objects.filter(name='2025').count(), 2)

    def test_one_game_cannot_have_the_same_edition_twice(self):
        game = Games.objects.get_or_create(game_title='Game C')[0]
        GameSeries.objects.get_or_create(game=game, name='2025')[0]
        with self.assertRaises(Exception):
            # create, not get_or_create: the point is that the second one is
            # refused, and get_or_create would quietly hand back the first.
            GameSeries.objects.create(game=game, name='2025')

    def test_an_edition_gets_a_slug_from_the_game_and_its_name(self):
        # Its own title: the catalogue seeds EA FC with real editions now, and
        # a test that asserts what exists has to own what exists.
        game = Games.objects.create(game_title='Slug Probe FC')
        series = GameSeries.objects.create(game=game, name='Slug Probe FC 26')
        self.assertTrue(series.slug)
        self.assertIn('slug-probe-fc', series.slug)


class GamesEndpointTests(TestCase):
    def setUp(self):
        self.game = Games.objects.create(game_title='Catalogue Probe FC')
        GameSeries.objects.create(
            game=self.game, name='Catalogue Probe FC 25', release_year=2024)
        GameSeries.objects.create(
            game=self.game, name='Catalogue Probe FC 24', release_year=2023,
            is_active=False)
        Games.objects.get_or_create(game_title='Retired Title', defaults={'is_active': False})[0]

    def test_the_picker_only_sees_live_games(self):
        rows = self.client.get('/auth/games/').json()['data']['games']
        names = [g['name'] for g in rows]
        self.assertIn('Catalogue Probe FC', names)
        self.assertNotIn('Retired Title', names)

    def test_a_game_carries_its_editions(self):
        rows = self.client.get('/auth/games/').json()['data']['games']
        ea = next(g for g in rows if g['name'] == 'Catalogue Probe FC')
        names = [s['name'] for s in ea['series']]
        self.assertIn('Catalogue Probe FC 25', names)

    def test_a_retired_edition_is_not_offered(self):
        rows = self.client.get('/auth/games/').json()['data']['games']
        ea = next(g for g in rows if g['name'] == 'Catalogue Probe FC')
        self.assertNotIn('Catalogue Probe FC 24', [s['name'] for s in ea['series']])

    def test_the_console_can_ask_for_everything(self):
        rows = self.client.get('/auth/games/?all=1').json()['data']['games']
        names = [g['name'] for g in rows]
        self.assertIn('Retired Title', names)


class AdminGameCatalogueTests(TestCase):
    def setUp(self):
        self.admin, self.auth = an_admin()

    def test_an_admin_can_add_a_game_and_an_edition(self):
        res = self.client.post('/auth/admin/games/', data={'name': 'Street Fighter'},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content)
        game_id = res.json()['data']['id']

        res = self.client.post('/auth/admin/games/%s/series/' % game_id,
                               data={'name': '6', 'release_year': 2023},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual([s['name'] for s in res.json()['data']['series']], ['6'])

    def test_a_duplicate_game_is_refused(self):
        Games.objects.get_or_create(game_title='Tekken')[0]
        res = self.client.post('/auth/admin/games/', data={'name': 'tekken'},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 409, res.content)

    def test_retiring_a_game_takes_it_out_of_the_picker_without_deleting_it(self):
        """Games cascades into tournaments, so retire is the only safe removal."""
        game = Games.objects.get_or_create(game_title='Old Title')[0]
        res = self.client.patch('/auth/admin/games/%s/' % game.game_id,
                                data={'is_active': False},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        game.refresh_from_db()
        self.assertFalse(game.is_active)
        self.assertTrue(Games.objects.filter(pk=game.pk).exists())

        rows = self.client.get('/auth/games/').json()['data']['games']
        self.assertNotIn('Old Title', [g['name'] for g in rows])

    def test_renaming_an_edition_moves_its_slug(self):
        game = Games.objects.create(game_title='Rename Probe FC')
        series = GameSeries.objects.create(game=game, name='Rename Probe FC 25')
        before = series.slug
        res = self.client.patch('/auth/admin/series/%s/' % series.series_id,
                                data={'name': 'Rename Probe FC 26'},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        series.refresh_from_db()
        self.assertNotEqual(series.slug, before)

    def test_an_ordinary_member_cannot_touch_the_catalogue(self):
        member = Users.objects.create(
            username='player_%s' % uuid.uuid4().hex[:5],
            email='p_%s@vent.test' % uuid.uuid4().hex[:5],
            login_session_token='member-token',
        )
        member.login_session_created_at = timezone.now()
        member.login_session_2fa_at = timezone.now()
        member.save(update_fields=['login_session_created_at', 'login_session_2fa_at'])
        res = self.client.post('/auth/admin/games/', data={'name': 'Nope'},
                               content_type='application/json',
                               HTTP_AUTHORIZATION='Bearer member-token')
        self.assertIn(res.status_code, (401, 403), res.content)
        self.assertFalse(Games.objects.filter(game_title='Nope').exists())

    def test_a_finance_admin_cannot_shape_the_catalogue(self):
        _user, auth = an_admin(role='finance_admin', token='fin-grant')
        res = self.client.post('/auth/admin/games/', data={'name': 'Nope Either'},
                               content_type='application/json', **auth)
        self.assertEqual(res.status_code, 403, res.content)


class AdminGameModeTests(TestCase):
    """Adding, renaming and retiring the ways a game is played.

    The modes were seeded by a migration and nothing could touch them
    afterwards, so a game added through the console arrived with no modes and no
    way to give it any - and the wizard then offered its organiser nothing to
    pick.
    """

    def setUp(self):
        self.admin, self.auth = an_admin()
        # A title nothing seeds, so the assertions are about this test's own rows.
        self.game = Games.objects.create(game_title='Mode Probe')

    def add(self, **body):
        return self.client.post('/auth/admin/games/%s/modes/' % self.game.game_id,
                                data=body, content_type='application/json', **self.auth)

    def test_an_admin_adds_a_mode_and_the_wizard_can_then_see_it(self):
        res = self.add(name='5v5 Bomb', team_size=5, default_format='single_elimination')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual([m['name'] for m in res.json()['data']['modes']], ['5v5 Bomb'])

        # The endpoint the wizard reads, not the one that wrote it.
        public = self.client.get('/tournament/games/%s/modes/' % self.game.game_id)
        self.assertEqual(public.status_code, 200, public.content)
        modes = public.json()['data']['modes']
        self.assertEqual([m['name'] for m in modes], ['5v5 Bomb'])
        self.assertEqual(modes[0]['team_size'], 5)

    def test_a_mode_needs_a_name(self):
        self.assertEqual(self.add(name='   ').status_code, 400)

    def test_the_same_mode_twice_is_refused(self):
        self.add(name='Deathmatch')
        self.assertEqual(self.add(name='deathmatch').status_code, 409)

    def test_the_same_name_under_a_different_edition_is_allowed(self):
        """An edition that changed a mode keeps its own version of it."""
        series = GameSeries.objects.create(game=self.game, name='2026')
        self.add(name='Deathmatch')
        res = self.add(name='Deathmatch', series=series.series_id)
        self.assertEqual(res.status_code, 201, res.content)

    def test_an_edition_from_another_game_is_refused(self):
        other = Games.objects.create(game_title='Mode Probe Other')
        series = GameSeries.objects.create(game=other, name='2026')
        res = self.add(name='Deathmatch', series=series.series_id)
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_team_size_that_is_not_a_number_is_refused(self):
        self.assertEqual(self.add(name='Weird', team_size='five').status_code, 400)

    def test_renaming_a_mode(self):
        self.add(name='Bomb')
        mode = GameMode.objects.get(game=self.game, name='Bomb')
        res = self.client.patch('/auth/admin/modes/%s/' % mode.mode_id,
                                data={'name': '5v5 Bomb'},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        mode.refresh_from_db()
        self.assertEqual(mode.name, '5v5 Bomb')

    def test_retiring_a_mode_hides_it_from_the_wizard_without_deleting_it(self):
        """A tournament records the mode it was played in by name. Deleting one
        rewrites the history rather than ending it."""
        self.add(name='Spike Rush')
        mode = GameMode.objects.get(game=self.game, name='Spike Rush')
        self.client.patch('/auth/admin/modes/%s/' % mode.mode_id,
                          data={'is_active': False},
                          content_type='application/json', **self.auth)

        public = self.client.get('/tournament/games/%s/modes/' % self.game.game_id)
        self.assertEqual([m['name'] for m in public.json()['data']['modes']], [])
        self.assertTrue(GameMode.objects.filter(pk=mode.pk).exists())

    def test_a_patch_that_changes_nothing_says_so(self):
        self.add(name='Bomb')
        mode = GameMode.objects.get(game=self.game, name='Bomb')
        res = self.client.patch('/auth/admin/modes/%s/' % mode.mode_id, data={},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'NO_FIELDS_TO_UPDATE')

    def test_a_stranger_cannot_add_a_mode(self):
        """Refused, and nothing is written.

        The status is 400 rather than 401, which is this codebase's standing
        convention for a missing Authorization header and is wrong on its own
        terms. Asserted as "not accepted" rather than pinned to 400, so fixing
        that convention does not break this test.
        """
        before = GameMode.objects.filter(game=self.game).count()
        res = self.client.post('/auth/admin/games/%s/modes/' % self.game.game_id,
                               data={'name': 'Nope'}, content_type='application/json')
        self.assertGreaterEqual(res.status_code, 400, res.content)
        self.assertEqual(GameMode.objects.filter(game=self.game).count(), before)
