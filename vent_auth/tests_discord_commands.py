"""Slash commands, and the signature that is the whole security model.

CEO, 7 September 2026: "also let the bot have commands like / commands it can
use inisde the server."

`SignatureTests` is the one that matters. The interactions endpoint is public
and unauthenticated because Discord calls it, and the ONLY thing standing
between it and anybody on the internet is the Ed25519 signature. If that check
is wrong the endpoint is open.

Discord tests this itself before accepting the URL, sending deliberately bad
signatures and refusing the endpoint unless every one is rejected. These tests
do the same thing so a regression is caught here rather than by the endpoint
quietly going open.
"""
import json
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import Client, TestCase
from django.utils import timezone

from . import discord_commands as commands
from .models import PlatformAccount, Users


def a_keypair():
    """A throwaway signing key, so tests can produce genuinely valid
    signatures rather than mocking the check they exist to exercise."""
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw().hex()
    return private, public


def signed(private, body, timestamp='1700000000'):
    sig = private.sign(timestamp.encode() + body).hex()
    return {'HTTP_X_SIGNATURE_ED25519': sig,
            'HTTP_X_SIGNATURE_TIMESTAMP': timestamp}


class SignatureTests(TestCase):
    """Nothing is parsed, looked up or answered before this passes."""

    def setUp(self):
        self.client = Client()
        self.private, self.public = a_keypair()
        self.url = '/auth/discord/interactions/'

    def _post(self, payload, headers):
        return self.client.post(self.url, data=json.dumps(payload),
                                content_type='application/json', **headers)

    def test_a_valid_signature_gets_the_ping_answered(self):
        body = json.dumps({'type': 1}).encode()
        with mock.patch.dict('os.environ', {'DISCORD_PUBLIC_KEY': self.public}):
            res = self.client.post(self.url, data=body,
                                   content_type='application/json',
                                   **signed(self.private, body))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {'type': 1})

    def test_no_signature_at_all_is_refused(self):
        with mock.patch.dict('os.environ', {'DISCORD_PUBLIC_KEY': self.public}):
            res = self._post({'type': 1}, {})
        self.assertEqual(res.status_code, 401)

    def test_a_forged_signature_is_refused(self):
        """The exact thing Discord tries before accepting the URL."""
        body = json.dumps({'type': 1}).encode()
        with mock.patch.dict('os.environ', {'DISCORD_PUBLIC_KEY': self.public}):
            res = self.client.post(
                self.url, data=body, content_type='application/json',
                HTTP_X_SIGNATURE_ED25519='00' * 64,
                HTTP_X_SIGNATURE_TIMESTAMP='1700000000')
        self.assertEqual(res.status_code, 401)

    def test_a_signature_from_the_wrong_key_is_refused(self):
        other, _ = a_keypair()
        body = json.dumps({'type': 1}).encode()
        with mock.patch.dict('os.environ', {'DISCORD_PUBLIC_KEY': self.public}):
            res = self.client.post(self.url, data=body,
                                   content_type='application/json',
                                   **signed(other, body))
        self.assertEqual(res.status_code, 401)

    def test_a_tampered_body_is_refused(self):
        """The signature covers the body, so changing one byte breaks it.

        This is why the RAW body is verified rather than a re-serialised dict:
        re-serialising changes the bytes and would break every valid request.
        """
        body = json.dumps({'type': 1}).encode()
        headers = signed(self.private, body)
        with mock.patch.dict('os.environ', {'DISCORD_PUBLIC_KEY': self.public}):
            res = self.client.post(self.url,
                                   data=json.dumps({'type': 2}).encode(),
                                   content_type='application/json', **headers)
        self.assertEqual(res.status_code, 401)

    def test_with_no_key_configured_nothing_is_trusted(self):
        body = json.dumps({'type': 1}).encode()
        with mock.patch.dict('os.environ', {'DISCORD_PUBLIC_KEY': ''}), \
             mock.patch.object(commands, 'DEFAULT_PUBLIC_KEY', ''):
            res = self.client.post(self.url, data=body,
                                   content_type='application/json',
                                   **signed(self.private, body))
        self.assertEqual(res.status_code, 401)


class CommandTests(TestCase):
    """What each command answers, driven through handle() directly."""

    def setUp(self):
        self.user = Users.objects.create(
            username='cmd_player', email='cmd_player@vent.test',
            full_name='Cmd Player', is_active=True)

    def _run(self, sub, args=None, discord_id=''):
        options = [{'type': 1, 'name': sub,
                    'options': [{'name': k, 'value': v}
                                for k, v in (args or {}).items()]}]
        payload = {'type': 2, 'data': {'name': 'vent', 'options': options}}
        if discord_id:
            payload['member'] = {'user': {'id': discord_id}}
        return commands.handle(payload)

    def test_every_answer_is_ephemeral_so_it_does_not_fill_a_channel(self):
        for sub in ('link', 'me'):
            out = self._run(sub)
            self.assertEqual(out['data'].get('flags'), commands.EPHEMERAL,
                             '%s was not ephemeral' % sub)

    def test_a_one_letter_search_is_refused_rather_than_scanning_everything(self):
        out = self._run('tournament', {'name': 'a'})
        self.assertIn('two characters', out['data']['content'])

    def test_searching_for_nothing_says_so_plainly(self):
        out = self._run('tournament', {'name': 'zzznothinghere'})
        self.assertIn('Nothing called', out['data']['content'])

    def test_me_tells_an_unlinked_person_how_to_link(self):
        out = self._run('me', discord_id='123456789012345678')
        self.assertIn('not connected', out['data']['content'])

    def test_me_finds_a_linked_account(self):
        PlatformAccount.objects.create(
            user=self.user, platform='discord',
            provider_user_id='123456789012345678', connected=True,
            verified=True)
        out = self._run('me', discord_id='123456789012345678')
        self.assertIn('Cmd Player', json.dumps(out))

    def test_an_unknown_command_does_not_blow_up(self):
        out = commands.handle({'type': 2, 'data': {'name': 'vent',
                                                   'options': [
                                                       {'type': 1,
                                                        'name': 'nonsense'}]}})
        self.assertIn('do not know', out['data']['content'])

    def test_who_reads_a_direct_message_as_well_as_a_server(self):
        """`member` in a server, `user` in a DM. Reading one is a bug that
        shows up the first time somebody uses it outside a server."""
        self.assertEqual(
            commands._who({'member': {'user': {'id': '1'}}}), '1')
        self.assertEqual(commands._who({'user': {'id': '2'}}), '2')
        self.assertEqual(commands._who({}), '')


class RegisteredCommandTests(TestCase):
    """The catalogue Discord is told about."""

    def test_every_command_has_a_description_because_discord_refuses_without(self):
        for row in commands.COMMANDS:
            self.assertTrue(row.get('description'))
            for sub in row.get('options', []):
                self.assertTrue(sub.get('description'),
                                '%s has no description' % sub.get('name'))

    def test_nothing_registered_changes_anything(self):
        """All read-only on purpose: a slash command is typed by anybody in a
        server, and the interaction alone cannot say whether they are an
        organiser. Anything destructive belongs on the V-ENT console."""
        names = {s['name'] for r in commands.COMMANDS
                 for s in r.get('options', [])}
        for dangerous in ('delete', 'purge', 'ban', 'kick', 'role'):
            self.assertNotIn(dangerous, names)
