"""An outside community as a linked account, and the switch that hides one.

CEO, 30 August 2026: "afc should be added to connected accounts once a user
signs in or signs up with it." It already was, in the database: signing in with
an African Free Fire Community account wrote an ExternalIdentity row. The
settings panel that exists precisely to list connected accounts knew nothing
about it, so the one place anybody would look said nothing.

And, the same day: "pending when the afc is done fixing, lets hide afc for now."
AFC's login page then answered in about twelve seconds and sat on "Loading...",
so the button led somewhere that never finished. Hiding it was a switch rather
than a deletion, because the credentials were real and the flow worked; only
their sign-in page did not.

AFC shipped their fix on 30 August 2026 and the default is back on, so the
switch is now the way to hold it shut rather than the way it ships. These tests
outlive that: a switch that has been used once will be used again.
"""
import uuid
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_partners.models import ExternalIdentity, InboundLogin
from vent_partners.views_sso import attach_identity, link_or_create_user

AFC_ON = {
    'AFC_CLIENT_ID': 'afc-client',
    'AFC_CLIENT_SECRET': 'afc-secret',
    'AFC_AUTHORIZE_URL': 'https://africanfreefirecommunity.com/oauth/authorize',
    'AFC_TOKEN_URL': 'https://africanfreefirecommunity.com/oauth/token',
    'AFC_USERINFO_URL': 'https://africanfreefirecommunity.com/api/me',
    'AFC_SSO_ENABLED': '1',
}
AFC_OFF = dict(AFC_ON, AFC_SSO_ENABLED='0')
# Credentials present, the switch never mentioned. This is how a host that has
# been given keys and no opinion about the switch behaves.
AFC_DEFAULT = {k: v for k, v in AFC_ON.items() if k != 'AFC_SSO_ENABLED'}


def a_user(name, password=None):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
    )
    if password:
        user.set_password(password)
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class SwitchedOffTests(TestCase):
    def test_a_switched_off_provider_is_not_offered_at_all(self):
        """Not listed as unavailable: not listed. The login page would
        otherwise have to tell "not set up" apart from "set up and deliberately
        hidden", which is not its business."""
        with mock.patch.dict('os.environ', AFC_OFF):
            res = self.client.get('/partners/inbound/providers/')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('afc', res.json()['data']['providers'])

    def test_it_comes_back_by_setting_one_variable(self):
        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.get('/partners/inbound/providers/')
        self.assertIn('afc', res.json()['data']['providers'])
        self.assertTrue(res.json()['data']['providers']['afc']['configured'])

    def test_starting_a_switched_off_provider_is_refused(self):
        """Hiding the button is not the enforcement. Anybody who kept the old
        address would otherwise still be sent into the broken page."""
        with mock.patch.dict('os.environ', AFC_OFF):
            res = self.client.get('/partners/inbound/afc/start/')
        self.assertEqual(res.status_code, 503)

    def test_with_keys_and_no_switch_set_it_is_offered(self):
        """AFC fixed their login page on 30 August 2026, so the default is on.

        A host that has been handed credentials and says nothing about the
        switch gets the button, rather than having to know to set a variable
        whose reason for existing has passed.
        """
        from vent_partners.views_sso import inbound_config
        with mock.patch.dict('os.environ', AFC_DEFAULT, clear=True):
            cfg = inbound_config('afc')
        self.assertTrue(cfg['enabled'])
        self.assertTrue(cfg['configured'])

    def test_with_no_keys_and_no_switch_set_nothing_is_offered(self):
        """The default being on must not mean a button on a host with no keys.

        `enabled` and `credentials` are separate questions and `configured`
        needs both, so production draws nothing until somebody adds the client
        id and secret.
        """
        from vent_partners.views_sso import inbound_config
        with mock.patch.dict('os.environ', {}, clear=True):
            cfg = inbound_config('afc')
        self.assertTrue(cfg['enabled'])
        self.assertFalse(cfg['credentials'])
        self.assertFalse(cfg['configured'])

        # Listed, and honestly: a provider missing its keys says
        # `configured: false` and the login page draws nothing for it. That is
        # a different case from switched off, which is not listed at all.
        with mock.patch.dict('os.environ', {}, clear=True):
            res = self.client.get('/partners/inbound/providers/')
        self.assertFalse(res.json()['data']['providers']['afc']['configured'])

    def test_switching_it_off_does_not_throw_away_the_credentials(self):
        """The client id and secret survive, so turning it back on is one
        variable rather than a re-issue from AFC's console."""
        from vent_partners.views_sso import inbound_config
        with mock.patch.dict('os.environ', AFC_OFF):
            cfg = inbound_config('afc')
        self.assertTrue(cfg['credentials'])
        self.assertFalse(cfg['enabled'])
        self.assertFalse(cfg['configured'])


class LinkedAccountsPanelTests(TestCase):
    """What /auth/link/status/ says about an outside community."""

    def test_somebody_who_signed_in_with_afc_sees_it_connected(self):
        user, auth = a_user('afc_player')
        user.signup_type = 'afc'
        user.save(update_fields=['signup_type'])
        ExternalIdentity.objects.create(
            user=user, provider='afc', external_id='9001',
            external_username='naija_ace', last_login_at=timezone.now())

        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.get('/auth/link/status/', **auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = res.json()['data']['external']['afc']
        self.assertTrue(row['connected'])
        self.assertEqual(row['handle'], 'naija_ace')
        self.assertEqual(row['label'], 'African Free Fire Community')

    def test_an_account_with_no_password_is_told_afc_is_its_way_in(self):
        """No Disconnect button on the only door."""
        user, auth = a_user('only_afc')
        user.signup_type = 'afc'
        user.password = ''
        user.save(update_fields=['signup_type', 'password'])
        ExternalIdentity.objects.create(user=user, provider='afc', external_id='1')

        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.get('/auth/link/status/', **auth)
        self.assertTrue(res.json()['data']['external']['afc']['is_sign_in_method'])

    def test_an_account_with_a_password_may_disconnect(self):
        user, auth = a_user('has_password', password='SomethingReal1!')
        user.signup_type = 'afc'
        user.save(update_fields=['signup_type'])
        ExternalIdentity.objects.create(user=user, provider='afc', external_id='2')

        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.get('/auth/link/status/', **auth)
        self.assertFalse(res.json()['data']['external']['afc']['is_sign_in_method'])

    def test_a_hidden_provider_nobody_is_linked_to_is_not_listed(self):
        _, auth = a_user('ordinary')
        with mock.patch.dict('os.environ', AFC_OFF):
            res = self.client.get('/auth/link/status/', **auth)
        self.assertNotIn('afc', res.json()['data']['external'])

    def test_a_hidden_provider_somebody_IS_linked_to_is_still_listed(self):
        """Their account genuinely is connected. The panel must not quietly
        stop saying so because the button was hidden for everybody else."""
        user, auth = a_user('linked_already')
        ExternalIdentity.objects.create(user=user, provider='afc', external_id='3',
                                        external_username='still_here')
        with mock.patch.dict('os.environ', AFC_OFF):
            res = self.client.get('/auth/link/status/', **auth)
        row = res.json()['data']['external']['afc']
        self.assertTrue(row['connected'])
        self.assertFalse(row['configured'])


class AttachIdentityTests(TestCase):
    """Adding a provider to an account that already exists."""

    def setUp(self):
        self.user, self.auth = a_user('owner', password='SomethingReal1!')

    def test_it_links(self):
        outcome = attach_identity('afc', {'id': '55', 'username': 'ace'}, self.user)
        self.assertEqual(outcome, 'linked')
        self.assertTrue(ExternalIdentity.objects.filter(
            user=self.user, provider='afc', external_id='55').exists())

    def test_linking_the_same_one_twice_says_already(self):
        attach_identity('afc', {'id': '55', 'username': 'ace'}, self.user)
        self.assertEqual(attach_identity('afc', {'id': '55', 'username': 'ace'}, self.user),
                         'already')
        self.assertEqual(ExternalIdentity.objects.count(), 1)

    def test_an_account_already_on_somebody_else_is_refused(self):
        """Two V-ENT accounts pointing at one AFC account would both answer to
        the same sign-in, and whichever row was found first would win, silently
        and differently over time."""
        other, _ = a_user('other')
        ExternalIdentity.objects.create(user=other, provider='afc', external_id='55')
        self.assertEqual(attach_identity('afc', {'id': '55'}, self.user), 'taken')
        self.assertEqual(
            ExternalIdentity.objects.get(external_id='55').user_id, other.user_id)

    def test_a_profile_with_nothing_to_identify_it_fails(self):
        self.assertEqual(attach_identity('afc', {'username': 'nobody'}, self.user), 'failed')

    def test_a_rename_at_the_provider_is_picked_up(self):
        attach_identity('afc', {'id': '55', 'username': 'old_name'}, self.user)
        attach_identity('afc', {'id': '55', 'username': 'new_name'}, self.user)
        self.assertEqual(ExternalIdentity.objects.get().external_username, 'new_name')


class LinkFromSettingsTests(TestCase):
    """Starting the flow while signed in links rather than signs in."""

    def test_a_signed_in_start_remembers_who_asked(self):
        user, auth = a_user('linker')
        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.get('/partners/inbound/afc/start/', **auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(InboundLogin.objects.get().link_user_id, user.user_id)

    def test_a_signed_out_start_remembers_nobody(self):
        with mock.patch.dict('os.environ', AFC_ON):
            self.client.get('/partners/inbound/afc/start/')
        self.assertIsNone(InboundLogin.objects.get().link_user_id)

    def test_a_stale_token_is_a_sign_in_rather_than_an_error(self):
        """`inbound_start` is reachable both ways. A token that no longer works
        means "signing in", not "failed"."""
        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.get('/partners/inbound/afc/start/',
                                  HTTP_AUTHORIZATION='Bearer notarealtoken')
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(InboundLogin.objects.get().link_user_id)

    def test_the_callback_links_and_goes_back_to_the_settings_page(self):
        user, auth = a_user('linker')
        with mock.patch.dict('os.environ', AFC_ON):
            start = self.client.get('/partners/inbound/afc/start/', **auth)
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(start.json()['data']['url']).query)['state'][0]

        token_response = mock.Mock(status_code=200)
        token_response.json.return_value = {'access_token': 'a'}
        me_response = mock.Mock(status_code=200)
        me_response.json.return_value = {'id': '9001', 'username': 'naija_ace'}

        with mock.patch.dict('os.environ', AFC_ON), \
             mock.patch('vent_partners.views_sso.http.post', return_value=token_response), \
             mock.patch('vent_partners.views_sso.http.get', return_value=me_response):
            res = self.client.get(f'/partners/inbound/afc/callback/?code=x&state={state}')

        self.assertEqual(res.status_code, 302)
        self.assertIn('/settings?panel=linked&afc=linked', res['Location'])
        self.assertTrue(ExternalIdentity.objects.filter(
            user=user, provider='afc', external_id='9001').exists())
        # No new account, and no new session: they were already signed in.
        self.assertEqual(Users.objects.filter(signup_type='afc').count(), 0)

    def test_linking_an_account_somebody_else_owns_says_so_on_the_way_back(self):
        user, auth = a_user('linker')
        other, _ = a_user('other')
        ExternalIdentity.objects.create(user=other, provider='afc', external_id='9001')

        with mock.patch.dict('os.environ', AFC_ON):
            start = self.client.get('/partners/inbound/afc/start/', **auth)
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(start.json()['data']['url']).query)['state'][0]

        token_response = mock.Mock(status_code=200)
        token_response.json.return_value = {'access_token': 'a'}
        me_response = mock.Mock(status_code=200)
        me_response.json.return_value = {'id': '9001', 'username': 'naija_ace'}

        with mock.patch.dict('os.environ', AFC_ON), \
             mock.patch('vent_partners.views_sso.http.post', return_value=token_response), \
             mock.patch('vent_partners.views_sso.http.get', return_value=me_response):
            res = self.client.get(f'/partners/inbound/afc/callback/?code=x&state={state}')
        self.assertIn('afc=taken', res['Location'])


class DisconnectTests(TestCase):
    def test_an_account_with_a_password_can_disconnect(self):
        user, auth = a_user('safe', password='SomethingReal1!')
        ExternalIdentity.objects.create(user=user, provider='afc', external_id='1')
        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.post('/partners/inbound/afc/disconnect/', **auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_disconnecting_the_only_way_in_is_refused(self):
        """Otherwise a real account with a wallet and a tournament history is
        left with nobody able to reach it."""
        user, auth = a_user('locked_out')
        user.signup_type = 'afc'
        user.password = ''
        user.save(update_fields=['signup_type', 'password'])
        ExternalIdentity.objects.create(user=user, provider='afc', external_id='1')

        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.post('/partners/inbound/afc/disconnect/', **auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ONLY_SIGN_IN_METHOD')
        self.assertTrue(ExternalIdentity.objects.exists())

    def test_a_google_account_may_disconnect_without_a_password(self):
        user, auth = a_user('google_person')
        user.signup_type = 'google'
        user.password = ''
        user.save(update_fields=['signup_type', 'password'])
        ExternalIdentity.objects.create(user=user, provider='afc', external_id='1')
        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.post('/partners/inbound/afc/disconnect/', **auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_disconnecting_what_was_never_connected_is_a_404(self):
        _, auth = a_user('nothing_linked', password='SomethingReal1!')
        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.post('/partners/inbound/afc/disconnect/', **auth)
        self.assertEqual(res.status_code, 404)

    def test_signing_out_does_not_let_a_stranger_disconnect(self):
        user, _ = a_user('victim', password='SomethingReal1!')
        ExternalIdentity.objects.create(user=user, provider='afc', external_id='1')
        with mock.patch.dict('os.environ', AFC_ON):
            res = self.client.post('/partners/inbound/afc/disconnect/')
        # 400 rather than 401 is this codebase's answer to a missing bearer
        # token, everywhere, from the one shared helper. What matters here is
        # that it is refused and the link survives.
        self.assertIn(res.status_code, (400, 401, 403))
        self.assertTrue(ExternalIdentity.objects.exists())


class EmailArrivesLaterTests(TestCase):
    """AFC began sending an address only for approved, consented players.

    Everybody who signed in before that is already bound by external id to an
    account created without one, and that match returns before the address is
    ever looked at. Without healing, the fix on their side would do nothing for
    exactly the people it was meant to help.
    """

    def _signed_in_once_with_no_email(self, external_id='e1', handle='Fyre'):
        with mock.patch.dict('os.environ', AFC_ON):
            outcome = link_or_create_user(
                'afc', {'id': external_id, 'username': handle, 'email': 'seed@afc.test'})
        # Force the account back to the shape the broken window produced.
        outcome.email = '%s@afc.external' % outcome.username
        outcome.save(update_fields=['email'])
        return outcome

    def test_a_returning_player_is_handed_the_account_they_already_had(self):
        theirs = Users.objects.create(username='real_fyre', email='fyre@gmail.com')
        shell = self._signed_in_once_with_no_email()
        self.assertNotEqual(shell.pk, theirs.pk)

        with mock.patch.dict('os.environ', AFC_ON):
            got = link_or_create_user(
                'afc', {'id': 'e1', 'username': 'Fyre', 'email': 'FYRE@gmail.com'})

        self.assertEqual(got.pk, theirs.pk)
        self.assertEqual(
            ExternalIdentity.objects.get(provider='afc', external_id='e1').user_id,
            theirs.user_id)

    def test_a_player_with_no_other_account_keeps_theirs_and_gains_the_address(self):
        shell = self._signed_in_once_with_no_email(external_id='e2', handle='Solo')
        with mock.patch.dict('os.environ', AFC_ON):
            got = link_or_create_user(
                'afc', {'id': 'e2', 'username': 'Solo', 'email': 'solo@gmail.com'})
        self.assertEqual(got.pk, shell.pk)
        got.refresh_from_db()
        self.assertEqual(got.email, 'solo@gmail.com')

    def test_an_account_with_a_real_address_is_never_moved(self):
        """Somebody who signed up here properly and later linked AFC keeps their
        account whatever the provider now says."""
        theirs, _ = a_user('settled')
        theirs.email = 'settled@vent.test'
        theirs.save(update_fields=['email'])
        ExternalIdentity.objects.create(
            user=theirs, provider='afc', external_id='e3',
            external_username='Settled', last_login_at=timezone.now())
        Users.objects.create(username='decoy', email='someone_else@gmail.com')

        with mock.patch.dict('os.environ', AFC_ON):
            got = link_or_create_user(
                'afc', {'id': 'e3', 'username': 'Settled', 'email': 'someone_else@gmail.com'})

        self.assertEqual(got.pk, theirs.pk)
        theirs.refresh_from_db()
        self.assertEqual(theirs.email, 'settled@vent.test')

    def test_still_no_address_still_refuses_to_create(self):
        before = Users.objects.count()
        with mock.patch.dict('os.environ', AFC_ON):
            self.assertEqual(
                link_or_create_user('afc', {'id': 'e4', 'username': 'nocsnt'}),
                'no-email')
        self.assertEqual(Users.objects.count(), before)
