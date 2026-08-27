"""A game has editions, and admins keep both lists.

EA FC ships a new edition every year and the catalogue held "EA FC 24" and
"EA FC 25" as two unrelated rows, so nothing tied this year's game to last
year's and the dropdown grew an entry a year.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from .models import GameSeries, Games, Users


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
        a = Games.objects.create(game_title='Game A')
        b = Games.objects.create(game_title='Game B')
        GameSeries.objects.create(game=a, name='2025')
        GameSeries.objects.create(game=b, name='2025')
        self.assertEqual(GameSeries.objects.filter(name='2025').count(), 2)

    def test_one_game_cannot_have_the_same_edition_twice(self):
        game = Games.objects.create(game_title='Game C')
        GameSeries.objects.create(game=game, name='2025')
        with self.assertRaises(Exception):
            GameSeries.objects.create(game=game, name='2025')

    def test_an_edition_gets_a_slug_from_the_game_and_its_name(self):
        game = Games.objects.create(game_title='EA FC')
        series = GameSeries.objects.create(game=game, name='EA FC 26')
        self.assertTrue(series.slug)
        self.assertIn('ea-fc', series.slug)


class GamesEndpointTests(TestCase):
    def setUp(self):
        self.game = Games.objects.create(game_title='EA FC')
        GameSeries.objects.create(game=self.game, name='EA FC 25', release_year=2024)
        GameSeries.objects.create(game=self.game, name='EA FC 24', release_year=2023,
                                  is_active=False)
        Games.objects.create(game_title='Retired Title', is_active=False)

    def test_the_picker_only_sees_live_games(self):
        rows = self.client.get('/auth/games/').json()['data']['games']
        names = [g['name'] for g in rows]
        self.assertIn('EA FC', names)
        self.assertNotIn('Retired Title', names)

    def test_a_game_carries_its_editions(self):
        rows = self.client.get('/auth/games/').json()['data']['games']
        ea = next(g for g in rows if g['name'] == 'EA FC')
        names = [s['name'] for s in ea['series']]
        self.assertIn('EA FC 25', names)

    def test_a_retired_edition_is_not_offered(self):
        rows = self.client.get('/auth/games/').json()['data']['games']
        ea = next(g for g in rows if g['name'] == 'EA FC')
        self.assertNotIn('EA FC 24', [s['name'] for s in ea['series']])

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
        Games.objects.create(game_title='Tekken')
        res = self.client.post('/auth/admin/games/', data={'name': 'tekken'},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 409, res.content)

    def test_retiring_a_game_takes_it_out_of_the_picker_without_deleting_it(self):
        """Games cascades into tournaments, so retire is the only safe removal."""
        game = Games.objects.create(game_title='Old Title')
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
        game = Games.objects.create(game_title='EA FC')
        series = GameSeries.objects.create(game=game, name='EA FC 25')
        before = series.slug
        res = self.client.patch('/auth/admin/series/%s/' % series.series_id,
                                data={'name': 'EA FC 26'},
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
