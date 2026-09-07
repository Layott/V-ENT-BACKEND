"""Signing in and signing up with Discord.

CEO, 7 September 2026: "DO THE BAOVE TWO ALSO" and "users should be able to
sign up with discord also".

The test that earns its place here is `EmailCollisionTests`. Everything else
confirms the happy path; that one confirms a door is shut. Merging a Discord
sign-in onto an existing account because the email matches would mean anybody
who can get a Discord account onto your address owns your V-ENT account, and a
V-ENT account holds a wallet.
"""
import uuid
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import PlatformAccount, Users


def a_user(name, **extra):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        full_name=name.title(),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
        is_active=True, **extra)
    u.login_session_created_at = timezone.now()
    u.save()
    return u


def discord_says(discord_id, email, username='kaycee', global_name=None):
    """Stand in for Discord: a token exchange, then a profile read."""
    token = mock.Mock(status_code=200)
    token.json.return_value = {'access_token': 'at'}
    me = mock.Mock(status_code=200)
    me.json.return_value = {
        'id': discord_id, 'email': email,
        'username': username, 'global_name': global_name or username,
    }
    return token, me


def a_state():
    from .views_discord_auth import STATE_SALT
    from django.core import signing
    return signing.dumps({'next': '', 'at': timezone.now().isoformat()},
                         salt=STATE_SALT)


CONFIGURED = {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec'}


class StartTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @mock.patch.dict('os.environ', {'DISCORD_CLIENT_ID': '', 'DISCORD_CLIENT_SECRET': ''})
    def test_unconfigured_says_so_rather_than_sending_anybody_anywhere(self):
        res = self.client.get('/auth/discord/start/')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json().get('code'), 'DISCORD_SIGNIN_NOT_SET')

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_start_is_public_because_the_whole_point_is_having_no_account(self):
        res = self.client.get('/auth/discord/start/')
        self.assertEqual(res.status_code, 200)
        url = res.json()['data']['url']
        self.assertTrue(url.startswith('https://discord.com/api/oauth2/authorize?'))
        # `email` is what makes an account possible. Without it there is
        # nothing to recover the account with.
        self.assertIn('identify+email', url.replace('%20', '+'))
        self.assertIn('state=', url)


class SignUpTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_a_new_person_gets_an_account_a_wallet_and_a_session(self):
        token, me = discord_says('991', 'new_player@vent.test', 'kaycee', 'KayCee')
        with mock.patch('vent_auth.views_discord_auth.http.post', return_value=token), \
             mock.patch('vent_auth.views_discord_auth.http.get', return_value=me):
            res = self.client.get('/auth/discord/callback/?code=a&state=%s' % a_state())

        self.assertEqual(res.status_code, 302)
        self.assertIn('outcome=created', res['Location'])
        self.assertIn('token=', res['Location'])

        user = Users.objects.get(email='new_player@vent.test')
        self.assertEqual(user.signup_type, 'discord')
        self.assertEqual(user.provider_id, '991')
        self.assertTrue(user.login_session_token)
        self.assertTrue(hasattr(user, 'wallet'))

        # The link is recorded too, so Settings shows Discord connected and a
        # direct message has an id to go to. One description of the connection.
        row = PlatformAccount.objects.get(user=user, platform='discord')
        self.assertEqual(row.provider_user_id, '991')
        self.assertTrue(row.verified)

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_a_discord_account_with_no_email_cannot_make_an_account(self):
        token, me = discord_says('992', '', 'noemail')
        with mock.patch('vent_auth.views_discord_auth.http.post', return_value=token), \
             mock.patch('vent_auth.views_discord_auth.http.get', return_value=me):
            res = self.client.get('/auth/discord/callback/?code=a&state=%s' % a_state())
        self.assertIn('outcome=no_email', res['Location'])
        self.assertFalse(Users.objects.filter(provider_id='992').exists())


class SignInTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_a_returning_person_is_matched_on_the_id_not_the_handle(self):
        """The point of storing the snowflake.

        Somebody renames themselves on Discord between visits. They are the
        same person and must land in the same account; matching on the handle
        would have made a second one, and worse, would eventually match a
        stranger who took the old name.
        """
        user = a_user('dc_returning')
        PlatformAccount.objects.create(
            user=user, platform='discord', provider_user_id='777',
            gamertag='old_name', connected=True, verified=True)

        token, me = discord_says('777', user.email, 'a_completely_new_name')
        with mock.patch('vent_auth.views_discord_auth.http.post', return_value=token), \
             mock.patch('vent_auth.views_discord_auth.http.get', return_value=me):
            res = self.client.get('/auth/discord/callback/?code=a&state=%s' % a_state())

        self.assertIn('outcome=ok', res['Location'])
        self.assertIn(user.username, res['Location'])
        self.assertEqual(Users.objects.filter(username=user.username).count(), 1)

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_somebody_who_signed_up_with_discord_before_signs_back_in(self):
        user = a_user('dc_signup', signup_type='discord', provider_id='555')
        token, me = discord_says('555', user.email)
        with mock.patch('vent_auth.views_discord_auth.http.post', return_value=token), \
             mock.patch('vent_auth.views_discord_auth.http.get', return_value=me):
            res = self.client.get('/auth/discord/callback/?code=a&state=%s' % a_state())
        self.assertIn('outcome=ok', res['Location'])
        self.assertEqual(Users.objects.filter(provider_id='555').count(), 1)


class EmailCollisionTests(TestCase):
    """The door this file exists to prove is shut."""

    def setUp(self):
        self.client = APIClient()

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_an_email_on_an_existing_account_is_refused_not_merged(self):
        victim = a_user('dc_victim')
        original_token = victim.login_session_token

        token, me = discord_says('666', victim.email, 'attacker')
        with mock.patch('vent_auth.views_discord_auth.http.post', return_value=token), \
             mock.patch('vent_auth.views_discord_auth.http.get', return_value=me):
            res = self.client.get('/auth/discord/callback/?code=a&state=%s' % a_state())

        self.assertIn('outcome=email_taken', res['Location'])
        # No session was handed out.
        self.assertNotIn('token=', res['Location'])
        # No account was made, and the real account was not touched.
        self.assertFalse(Users.objects.filter(provider_id='666').exists())
        victim.refresh_from_db()
        self.assertEqual(victim.login_session_token, original_token)
        self.assertNotEqual(victim.signup_type, 'discord')


class StateTests(TestCase):
    """A callback that can be replayed or forged is not a sign-in."""

    def setUp(self):
        self.client = APIClient()

    def test_a_forged_state_signs_nobody_in(self):
        res = self.client.get('/auth/discord/callback/?code=a&state=not-real')
        self.assertIn('outcome=failed', res['Location'])
        self.assertEqual(Users.objects.filter(signup_type='discord').count(), 0)

    def test_no_code_signs_nobody_in(self):
        res = self.client.get('/auth/discord/callback/?state=%s' % a_state())
        self.assertIn('outcome=failed', res['Location'])

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_discord_refusing_the_exchange_signs_nobody_in(self):
        with mock.patch('vent_auth.views_discord_auth.http.post',
                        return_value=mock.Mock(status_code=401, text='nope')):
            res = self.client.get('/auth/discord/callback/?code=a&state=%s' % a_state())
        self.assertIn('outcome=failed', res['Location'])
        self.assertEqual(Users.objects.filter(signup_type='discord').count(), 0)
