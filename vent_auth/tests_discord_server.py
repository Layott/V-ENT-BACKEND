"""An organisation driving the bot in its own Discord server.

CEO, 7 September 2026: **"let each organiser grant only the parts they want."**

The tests that earn their place are the refusals. An organisation that granted
only announcements must not be able to delete anybody's messages, and the
proof has to exist in two forms:

- the INVITE it authorises carries no Manage Messages bit, so Discord itself
  would refuse (`PermissionBitsTests`);
- and the API refuses first anyway, so a widened grant made by hand in the
  database still cannot be used without Discord agreeing (`GrantTests`).

Belt and braces on purpose. These actions happen in a community that is not
ours, at the instruction of somebody who does not own it.
"""
import uuid
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .discord_server import CAPABILITIES, normalise, permission_bits
from .models import Organization, OrgMember, Users
from .models_discord import DiscordAction, DiscordServer

GUILD = '999888777666555444'
CONFIGURED = {'DISCORD_CLIENT_ID': 'cid', 'DISCORD_CLIENT_SECRET': 'sec',
              'DISCORD_BOT_TOKEN': 'bot.token.value'}

MANAGE_MESSAGES = 1 << 13
MANAGE_ROLES = 1 << 28
ADMINISTRATOR = 1 << 3
ANNOUNCE_BITS = (1 << 10) | (1 << 11) | (1 << 14) | (1 << 16)


def a_user(name):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True)
    u.login_session_created_at = timezone.now()
    u.save()
    return u, {'HTTP_AUTHORIZATION': 'Bearer %s' % u.login_session_token}


def an_org(owner):
    return Organization.objects.create(
        org_name='Vermillion %s' % uuid.uuid4().hex[:6],
        org_creator=owner, org_owner=owner)


class PermissionBitsTests(TestCase):
    """The invite an organiser authorises carries only what they ticked."""

    def test_announcements_only_cannot_delete_or_manage_roles(self):
        bits = permission_bits(['announce'])
        self.assertTrue(bits & (1 << 11))                 # Send Messages
        self.assertFalse(bits & MANAGE_MESSAGES)
        self.assertFalse(bits & MANAGE_ROLES)

    def test_asking_for_moderation_adds_only_that(self):
        base = permission_bits(['announce'])
        with_mod = permission_bits(['announce', 'moderate'])
        self.assertTrue(with_mod & MANAGE_MESSAGES)
        self.assertFalse(with_mod & MANAGE_ROLES)
        self.assertEqual(with_mod & base, base)

    def test_announce_is_always_included_because_nothing_works_without_it(self):
        self.assertIn('announce', normalise([]))
        self.assertIn('announce', normalise(['moderate']))

    def test_an_invented_capability_is_dropped_rather_than_trusted(self):
        self.assertEqual(normalise(['announce', 'administrator', 'nuke']),
                         ['announce'])
        self.assertFalse(permission_bits(['administrator']) & ADMINISTRATOR)


class GrantTests(TestCase):
    """What the API offers follows the grant, not what somebody asks for."""

    def setUp(self):
        self.client = APIClient()
        self.owner, self.auth = a_user('ds_owner')
        self.org = an_org(self.owner)
        # Granted announcements only, and Discord happens to allow everything.
        # The grant is what must stop it.
        self.server = DiscordServer.objects.create(
            org=self.org, guild_id=GUILD, guild_name='Their Server',
            granted=['announce'], live_permissions=ADMINISTRATOR)

    def _url(self, tail):
        return '/auth/discord/guild/%s/servers/%s/%s' % (
            self.org.slug or self.org.org_id, self.server.id, tail)

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_moderation_is_refused_when_it_was_not_granted(self):
        with mock.patch('vent_auth.discord.purge_messages') as purge:
            res = self.client.post(self._url('purge/'),
                                   {'channel_id': '1', 'limit': 10},
                                   format='json', **self.auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_GRANTED')
        # And Discord was never asked.
        self.assertFalse(purge.called)

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_roles_are_refused_when_they_were_not_granted(self):
        with mock.patch('vent_auth.discord.add_role') as add:
            res = self.client.post(self._url('role/'),
                                   {'discord_user_id': '5', 'role_id': '6'},
                                   format='json', **self.auth)
        self.assertEqual(res.status_code, 403)
        self.assertFalse(add.called)

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_a_refusal_is_recorded_because_a_run_of_them_is_a_signal(self):
        self.client.post(self._url('purge/'), {'channel_id': '1', 'limit': 5},
                         format='json', **self.auth)
        row = DiscordAction.objects.get()
        self.assertFalse(row.ok)
        self.assertEqual(row.actor_id, self.owner.user_id)

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_tagging_is_its_own_grant_separate_from_posting(self):
        """Posting quietly and notifying everybody are different asks."""
        with mock.patch('vent_auth.discord.post_message',
                        return_value=(True, {})) as post:
            plain = self.client.post(self._url('post/'),
                                     {'channel_id': '1', 'content': 'hello'},
                                     format='json', **self.auth)
            self.assertEqual(plain.status_code, 200)

            tagged = self.client.post(
                self._url('post/'),
                {'channel_id': '1', 'content': 'hello',
                 'mention_roles': ['77']}, format='json', **self.auth)
        self.assertEqual(tagged.status_code, 403)
        # The plain one went, the tagged one did not.
        self.assertEqual(post.call_count, 1)


class LivePermissionTests(TestCase):
    """Discord is the one that counts, even when the grant says yes."""

    def setUp(self):
        self.client = APIClient()
        self.owner, self.auth = a_user('ds_live')
        self.org = an_org(self.owner)
        # Granted everything, but the bot's role in Discord can only post.
        self.server = DiscordServer.objects.create(
            org=self.org, guild_id=GUILD, granted=list(CAPABILITIES),
            live_permissions=ANNOUNCE_BITS)

    @mock.patch.dict('os.environ', CONFIGURED)
    def test_a_granted_capability_discord_no_longer_allows_is_refused(self):
        with mock.patch('vent_auth.discord.purge_messages') as purge:
            res = self.client.post(
                '/auth/discord/guild/%s/servers/%s/purge/'
                % (self.org.slug or self.org.org_id, self.server.id),
                {'channel_id': '1', 'limit': 5}, format='json', **self.auth)
        self.assertEqual(res.status_code, 403)
        self.assertIn('no longer has permission', res.json()['message'])
        self.assertFalse(purge.called)

    def test_administrator_satisfies_everything(self):
        self.server.live_permissions = ADMINISTRATOR
        self.server.save(update_fields=['live_permissions'])
        for name in CAPABILITIES:
            ok, why = self.server.may(name)
            self.assertTrue(ok, '%s refused: %s' % (name, why))


class WhoMayTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner, self.auth = a_user('ds_who_owner')
        self.member, self.member_auth = a_user('ds_who_member')
        self.stranger, self.stranger_auth = a_user('ds_who_stranger')
        self.org = an_org(self.owner)
        OrgMember.objects.create(org=self.org, user=self.member, role='member')
        self.server = DiscordServer.objects.create(
            org=self.org, guild_id=GUILD, granted=['announce'],
            live_permissions=ADMINISTRATOR)

    def _servers_url(self):
        return '/auth/discord/guild/%s/servers/' % (self.org.slug
                                                    or self.org.org_id)

    def test_an_anonymous_caller_is_refused(self):
        self.assertIn(self.client.get(self._servers_url()).status_code,
                      (400, 401, 403))

    def test_a_stranger_is_refused(self):
        self.assertEqual(
            self.client.get(self._servers_url(), **self.stranger_auth).status_code,
            403)

    def test_an_ordinary_member_is_refused(self):
        """Putting the org's bot into somebody's server is speaking for it."""
        self.assertEqual(
            self.client.get(self._servers_url(), **self.member_auth).status_code,
            403)


class OneServerOneOrgTests(TestCase):
    def test_the_guild_id_is_unique_so_two_orgs_cannot_claim_one_server(self):
        from django.db import IntegrityError, transaction
        a, _ = a_user('ds_a')
        b, _ = a_user('ds_b')
        DiscordServer.objects.create(org=an_org(a), guild_id=GUILD,
                                     granted=['announce'])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DiscordServer.objects.create(org=an_org(b), guild_id=GUILD,
                                             granted=['announce'])
