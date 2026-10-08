"""A linked account shows itself once connected, and nobody types one (inbox 417).

CEO, 8 October 2026: "Then connect discord button and steam don't work
properly. You shouldn't as a user have to input your usernames or id for
discord or steams, it should show once you connect." And: "i cant get the steam
keyy for now. for the discord it was only ui, tapping the connect just put it on
and nothing was triggered."

What these hold:

- Steam's name and picture come from the public profile XML when there is no
  Web API key, and a private or failed profile falls back to the id quietly.
- Discord's picture is stored from the profile Discord returns.
- Connect pressed on the profile page comes back to the profile page.
- The profile lists proven accounts only, as a list, with name and picture; a
  handle typed into the old boxes is not shown.
- The endpoint that let anybody type a Discord or Steam handle, and keep the
  "verified" mark while doing it, is gone.
"""
import uuid
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import PlatformAccount, Users
from .views_linking import _sign, _steam_profile

STEAM_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<profile>
  <steamID64>76561197960287930</steamID64>
  <steamID><![CDATA[Rabscuttle]]></steamID>
  <avatarMedium><![CDATA[https://avatars.fastly.steamstatic.com/c5d5_medium.jpg]]></avatarMedium>
</profile>"""


def a_user(name):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def _ok(text, status_code=200):
    res = mock.Mock(status_code=status_code)
    res.text = text
    return res


def _steam_return(state, steam_id='76561197960287930'):
    return ('/auth/link/steam/callback/?state=%s'
            '&openid.claimed_id=https://steamcommunity.com/openid/id/%s'
            '&openid.mode=id_res' % (state, steam_id))


@mock.patch.dict('os.environ', {'STEAM_API_KEY': ''})
class SteamWithoutAKeyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('steamer')

    def test_the_name_and_picture_come_from_the_public_profile(self):
        with mock.patch('vent_auth.views_linking.http.post', return_value=_ok('ns:x\nis_valid:true\n')), \
             mock.patch('vent_auth.views_linking.http.get', return_value=_ok(STEAM_XML)):
            res = self.client.get(_steam_return(_sign(self.user)))
        self.assertEqual(res.status_code, 302)
        self.assertIn('steam=linked', res['Location'])
        row = PlatformAccount.objects.get(user=self.user, platform='steam')
        self.assertEqual(row.display_name, 'Rabscuttle')
        self.assertEqual(row.avatar_url, 'https://avatars.fastly.steamstatic.com/c5d5_medium.jpg')
        self.assertEqual(row.gamertag, '76561197960287930')
        self.assertTrue(row.verified)

    def test_a_private_or_failed_profile_falls_back_to_the_id(self):
        with mock.patch('vent_auth.views_linking.http.post', return_value=_ok('is_valid:true')), \
             mock.patch('vent_auth.views_linking.http.get', return_value=_ok('', status_code=503)):
            res = self.client.get(_steam_return(_sign(self.user)))
        self.assertIn('steam=linked', res['Location'])
        row = PlatformAccount.objects.get(user=self.user, platform='steam')
        self.assertEqual(row.display_name, '')
        self.assertEqual(row.avatar_url, '')

    def test_a_picture_from_anywhere_but_steam_is_dropped(self):
        hostile = STEAM_XML.replace('https://avatars.fastly.steamstatic.com/c5d5_medium.jpg',
                                    'https://evil.example/track.gif')
        with mock.patch('vent_auth.views_linking.http.get', return_value=_ok(hostile)):
            self.assertEqual(_steam_profile('1'), ('Rabscuttle', ''))


class ReturnTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('returner')

    def test_connect_from_the_profile_comes_back_to_the_profile(self):
        with mock.patch('vent_auth.views_linking.http.post', return_value=_ok('is_valid:true')), \
             mock.patch('vent_auth.views_linking.http.get', return_value=_ok(STEAM_XML)):
            res = self.client.get(_steam_return(_sign(self.user, 'profile')))
        self.assertIn('/edit-user-profile?panel=accounts&steam=linked', res['Location'])

    def test_an_unknown_return_goes_to_settings(self):
        with mock.patch('vent_auth.views_linking.http.post', return_value=_ok('is_valid:true')), \
             mock.patch('vent_auth.views_linking.http.get', return_value=_ok(STEAM_XML)):
            res = self.client.get(_steam_return(_sign(self.user, 'https://evil.example')))
        self.assertIn('/settings?panel=linked&steam=linked', res['Location'])

    def test_start_carries_where_it_came_from(self):
        res = self.client.get('/auth/link/steam/start/?back=profile', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertIn('steamcommunity.com/openid/login', res.data['data']['url'])


@mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec'})
class DiscordPictureTests(TestCase):
    def test_the_picture_discord_returns_is_kept(self):
        client = APIClient()
        user, _auth = a_user('pic')
        token = mock.Mock(status_code=200)
        token.json.return_value = {'access_token': 'at'}
        me = mock.Mock(status_code=200)
        me.json.return_value = {'id': '80351110224678912', 'username': 'nelly',
                                'global_name': 'Nelly', 'avatar': '8342729096ea3675442027381ff50dfe'}
        with mock.patch('vent_auth.views_linking.http.post', return_value=token), \
             mock.patch('vent_auth.views_linking.http.get', return_value=me):
            client.get('/auth/link/discord/callback/?code=x&state=%s' % _sign(user))
        row = PlatformAccount.objects.get(user=user, platform='discord')
        self.assertEqual(
            row.avatar_url,
            'https://cdn.discordapp.com/avatars/80351110224678912/8342729096ea3675442027381ff50dfe.png?size=128')


class ProfileListsProvenAccountsOnlyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('shower')
        PlatformAccount.objects.create(user=self.user, platform='discord', gamertag='nelly',
                                       display_name='Nelly', avatar_url='https://cdn.discordapp.com/x.png',
                                       connected=True, verified=True)
        # Typed into the old boxes: never proven.
        PlatformAccount.objects.create(user=self.user, platform='steam', gamertag='typedhandle',
                                       connected=True, verified=False)

    def test_the_profile_shows_the_proven_one_with_its_picture(self):
        res = self.client.get('/user/%s/profile/' % self.user.username)
        accounts = res.data['data']['gaming_accounts']
        self.assertIsInstance(accounts, list)
        self.assertEqual([a['platform'] for a in accounts], ['discord'])
        self.assertEqual(accounts[0]['label'], 'Discord')
        self.assertEqual(accounts[0]['handle'], 'Nelly')
        self.assertEqual(accounts[0]['avatar'], 'https://cdn.discordapp.com/x.png')

    def test_settings_does_not_call_a_typed_handle_connected(self):
        res = self.client.get('/auth/link/status/', **self.auth)
        linked = res.data['data']['linked']
        self.assertTrue(linked['discord']['connected'])
        self.assertFalse(linked['steam']['connected'])

    def test_nobody_can_type_a_handle_any_more(self):
        res = self.client.post('/auth/update-gaming-accounts/',
                               {'accounts': {'discord': {'gamertag': 'anyone', 'connected': True}}},
                               format='json', **self.auth)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(PlatformAccount.objects.get(user=self.user, platform='discord').gamertag, 'nelly')
