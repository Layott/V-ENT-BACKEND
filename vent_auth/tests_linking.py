"""Linking a Discord or Steam account to a V-ENT profile.

CEO, 7 September 2026, reading my own list back to me: "Discord/Steam linking
has no tests. /unlazy build these and fix them please."

`views_linking.py` shipped with the start endpoint, both callbacks, disconnect
and a settings panel, and not one test. It is live on production today, where
every user hits the unconfigured path because `DISCORD_CLIENT_ID` is not on the
box, and nothing proved even that answered correctly.

## What writing these found

**Two V-ENT accounts could verify the SAME Discord handle.** `PlatformAccount`
is unique on `(user, platform)`, which stops one person linking two Discords and
does nothing at all about two people linking one. The callback went straight to
`update_or_create(user=user, platform='discord')`, so the second person's link
succeeded and both profiles showed `verified: True`.

That empties the word. The whole difference between a linked account and a
hand-typed one is that the platform confirmed it, and a confirmation two people
can hold is not a confirmation. Somebody could have taken a known player's
Discord handle and worn it.

The frontend was already ahead of the backend here: `LinkedAccountsPanel`
handles a `taken` outcome, with the sentence "That account is already connected
to another V-ENT profile", and the backend had no code path that could ever
send it. A handled outcome nobody emits is the tell.

Fixed in `_claim_or_taken`, and `TakenTests` below is the proof.
"""
import uuid
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import PlatformAccount, Users


def a_user(name, **extra):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        full_name=name.title(),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
        **extra)
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class StartTests(TestCase):
    """Where the browser is sent, and what happens when it cannot be sent anywhere."""

    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('lk_start')

    def test_anonymous_cannot_start(self):
        """The gate is the API, never the button. See tests_signed_out."""
        res = self.client.get('/auth/link/discord/start/')
        self.assertIn(res.status_code, (400, 401, 403))

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': '', 'DISCORD_CLIENT_SECRET': ''})
    def test_discord_unconfigured_says_so(self):
        """THE PATH EVERY USER HITS TODAY.

        No credentials are on the production box, so this 503 is the entire
        Discord experience right now. It has to be a clean, named refusal
        rather than a traceback or a broken redirect to Discord.
        """
        res = self.client.get('/auth/link/discord/start/', **self.auth)
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json().get('code'), 'DISCORD_LINKING_NOT_SET')
        self.assertIs(res.json().get('configured'), False)

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec'})
    def test_discord_configured_returns_an_authorize_url(self):
        res = self.client.get('/auth/link/discord/start/', **self.auth)
        self.assertEqual(res.status_code, 200)
        url = res.json()['data']['url']
        self.assertTrue(url.startswith('https://discord.com/api/oauth2/authorize?'))
        self.assertIn('client_id=cid', url)
        self.assertIn('scope=identify', url)
        # The state is what stops a callback being replayed against another
        # account, so its absence is the whole flow being unsafe.
        self.assertIn('state=', url)

    def test_steam_needs_no_key(self):
        """Steam's OpenID is open. The API key only buys a display name."""
        res = self.client.get('/auth/link/steam/start/', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['data']['url'].startswith(
            'https://steamcommunity.com/openid/login?'))

    def test_an_unknown_provider_is_refused(self):
        res = self.client.get('/auth/link/psn/start/', **self.auth)
        self.assertEqual(res.status_code, 400)


class StateTests(TestCase):
    """The signed state carries the account, so it must not be forgeable."""

    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('lk_state')

    def test_a_forged_state_links_nothing(self):
        res = self.client.get('/auth/link/discord/callback/?code=x&state=not-a-real-state')
        self.assertEqual(res.status_code, 302)
        self.assertIn('discord=failed', res['Location'])
        self.assertEqual(PlatformAccount.objects.count(), 0)

    def test_no_code_links_nothing(self):
        from .views_linking import _sign
        res = self.client.get('/auth/link/discord/callback/?state=%s' % _sign(self.user))
        self.assertEqual(res.status_code, 302)
        self.assertIn('discord=failed', res['Location'])
        self.assertEqual(PlatformAccount.objects.count(), 0)


def _discord_returns(username, global_name=None):
    """Stand in for Discord: a token exchange then a profile read."""
    token = mock.Mock(status_code=200)
    token.json.return_value = {'access_token': 'at'}
    me = mock.Mock(status_code=200)
    me.json.return_value = {'username': username, 'global_name': global_name or username}
    return token, me


class LinkTests(TestCase):
    """The happy path, which nothing had ever exercised."""

    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('lk_ok')

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec'})
    def test_a_successful_link_is_stored_and_verified(self):
        from .views_linking import _sign
        token, me = _discord_returns('kaycee', 'KayCee')
        with mock.patch('vent_auth.views_linking.http.post', return_value=token), \
             mock.patch('vent_auth.views_linking.http.get', return_value=me):
            res = self.client.get(
                '/auth/link/discord/callback/?code=abc&state=%s' % _sign(self.user))

        self.assertEqual(res.status_code, 302)
        self.assertIn('discord=linked', res['Location'])
        row = PlatformAccount.objects.get(user=self.user, platform='discord')
        self.assertEqual(row.gamertag, 'kaycee')
        self.assertEqual(row.display_name, 'KayCee')
        self.assertTrue(row.connected)
        # Verified is the entire point: Discord said this handle is theirs.
        self.assertTrue(row.verified)

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec'})
    def test_relinking_the_same_account_updates_rather_than_duplicates(self):
        from .views_linking import _sign
        token, me = _discord_returns('kaycee', 'KayCee')
        with mock.patch('vent_auth.views_linking.http.post', return_value=token), \
             mock.patch('vent_auth.views_linking.http.get', return_value=me):
            self.client.get('/auth/link/discord/callback/?code=a&state=%s' % _sign(self.user))

        token2, me2 = _discord_returns('kaycee', 'KayCee Renamed')
        with mock.patch('vent_auth.views_linking.http.post', return_value=token2), \
             mock.patch('vent_auth.views_linking.http.get', return_value=me2):
            res = self.client.get(
                '/auth/link/discord/callback/?code=b&state=%s' % _sign(self.user))

        self.assertIn('discord=linked', res['Location'])
        self.assertEqual(PlatformAccount.objects.filter(platform='discord').count(), 1)
        self.assertEqual(
            PlatformAccount.objects.get(platform='discord').display_name, 'KayCee Renamed')

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec'})
    def test_discord_refusing_the_token_links_nothing(self):
        from .views_linking import _sign
        with mock.patch('vent_auth.views_linking.http.post',
                        return_value=mock.Mock(status_code=401, text='nope')):
            res = self.client.get(
                '/auth/link/discord/callback/?code=abc&state=%s' % _sign(self.user))
        self.assertIn('discord=failed', res['Location'])
        self.assertEqual(PlatformAccount.objects.count(), 0)


class TakenTests(TestCase):
    """The hole these tests were written to find.

    Before the fix, both of these passed a link and both profiles claimed
    `verified: True` for one Discord account.
    """

    def setUp(self):
        self.client = APIClient()
        self.first, _ = a_user('lk_first')
        self.second, _ = a_user('lk_second')

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec'})
    def test_a_second_person_cannot_claim_the_same_discord(self):
        from .views_linking import _sign
        token, me = _discord_returns('kaycee')

        with mock.patch('vent_auth.views_linking.http.post', return_value=token), \
             mock.patch('vent_auth.views_linking.http.get', return_value=me):
            self.client.get('/auth/link/discord/callback/?code=a&state=%s' % _sign(self.first))
            res = self.client.get(
                '/auth/link/discord/callback/?code=b&state=%s' % _sign(self.second))

        # The outcome the frontend has always handled and the backend could
        # never send.
        self.assertIn('discord=taken', res['Location'])
        self.assertFalse(
            PlatformAccount.objects.filter(user=self.second, platform='discord').exists())
        # And the first person keeps theirs, rather than being quietly unseated.
        self.assertTrue(
            PlatformAccount.objects.get(user=self.first, platform='discord').verified)

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec'})
    def test_a_disconnected_handle_is_free_again(self):
        """Somebody who unlinks releases the handle, or it is a lifetime lock."""
        from .views_linking import _sign
        token, me = _discord_returns('kaycee')
        with mock.patch('vent_auth.views_linking.http.post', return_value=token), \
             mock.patch('vent_auth.views_linking.http.get', return_value=me):
            self.client.get('/auth/link/discord/callback/?code=a&state=%s' % _sign(self.first))

        PlatformAccount.objects.filter(user=self.first, platform='discord').update(
            connected=False, verified=False)

        with mock.patch('vent_auth.views_linking.http.post', return_value=token), \
             mock.patch('vent_auth.views_linking.http.get', return_value=me):
            res = self.client.get(
                '/auth/link/discord/callback/?code=b&state=%s' % _sign(self.second))

        self.assertIn('discord=linked', res['Location'])
        self.assertTrue(
            PlatformAccount.objects.get(user=self.second, platform='discord').verified)


class DisconnectTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('lk_dc')
        PlatformAccount.objects.create(
            user=self.user, platform='discord', gamertag='kaycee',
            connected=True, verified=True)

    def test_anonymous_cannot_disconnect(self):
        res = self.client.post('/auth/link/discord/disconnect/')
        self.assertIn(res.status_code, (400, 401, 403))
        self.assertTrue(PlatformAccount.objects.get(platform='discord').connected)

    def test_disconnect_clears_the_verification(self):
        res = self.client.post('/auth/link/discord/disconnect/', **self.auth)
        self.assertEqual(res.status_code, 200)
        row = PlatformAccount.objects.filter(user=self.user, platform='discord').first()
        # Either the row is gone or it is no longer claiming to be verified.
        # What must never survive is a verified badge for an account nobody
        # holds any more.
        self.assertFalse(row and row.verified)


class StatusTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('lk_status')

    def test_anonymous_is_refused(self):
        res = self.client.get('/auth/link/status/')
        self.assertIn(res.status_code, (400, 401, 403))

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': '', 'DISCORD_CLIENT_SECRET': ''})
    def test_status_reports_discord_unconfigured(self):
        """What the settings panel renders its disabled button from."""
        res = self.client.get('/auth/link/status/', **self.auth)
        self.assertEqual(res.status_code, 200)
        providers = res.json()['data']['providers']
        self.assertIs(providers['discord']['configured'], False)

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec'})
    def test_status_reports_discord_configured(self):
        res = self.client.get('/auth/link/status/', **self.auth)
        self.assertIs(res.json()['data']['providers']['discord']['configured'], True)
