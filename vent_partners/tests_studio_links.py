"""Linking a game account to a V-ENT account (P6c).

Design section 14, item 2: "Account linking: Sign in with V-ENT from the game,
or a one-time code shown in the game client". Challengermode's intent flow
(research file, section 7.3) is the model: the game proves which of its own
accounts is asking, the player proves which V-ENT account they are by being
signed in when they type the code, and only the two together make a link.
"""
import hashlib
import json
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_partners import studio_links
from vent_partners.models import (
    OAuthAccessToken, Partner, PartnerApiKey, SANDBOX_SCOPES, StudioAccountLink, StudioGame,
    StudioLinkCode,
)
from vent_partners.studio_links import LinkRefused
from vent_partners.tests_self_serve import a_user
from vent_partners.tests_studio_delivery import POST, PUBLIC, answer


def a_studio(owner, slug='link-studio', **game_extra):
    partner = Partner.objects.create(
        name='Link Studio', slug=slug, owner=owner, contact_name='Ada',
        contact_email='ada@link.test', status='approved', approved_scopes=['tournaments:read'])
    defaults = dict(partner=partner, name='Link Arena %s' % slug, created_by=owner,
                    events_webhook_url='https://api.link.test/vent/events')
    defaults.update(game_extra)
    game = StudioGame(**defaults)
    game.set_secret()
    game.save()
    return partner, game


@override_settings(STUDIO_DELIVERY_INLINE=True)
class CodeTests(TestCase):
    def setUp(self):
        self.owner, _ = a_user('code_owner')
        self.partner, self.game = a_studio(self.owner, mode='live', live_status='approved')
        self.player, _ = a_user('code_player')

    def test_a_code_reads_as_two_groups_of_four_from_a_plain_alphabet(self):
        _, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        self.assertRegex(code, r'^[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}$')

    def test_only_a_hash_of_the_code_is_stored(self):
        row, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        self.assertNotIn(studio_links.normalise(code), row.code_hash)
        self.assertEqual(row.code_hash,
                         hashlib.sha256(studio_links.normalise(code).encode()).hexdigest())

    def test_a_code_is_found_however_it_is_typed(self):
        row, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        for typed in (code, code.lower(), code.replace('-', ''), ' %s ' % code.replace('-', ' ')):
            self.assertEqual(studio_links.find(typed), row, typed)

    def test_a_code_lives_ten_minutes(self):
        row, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        StudioLinkCode.objects.filter(pk=row.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(studio_links.find(code))

    def test_a_new_code_for_the_same_account_ends_the_old_one(self):
        _, first = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        _, second = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        self.assertIsNone(studio_links.find(first))
        self.assertIsNotNone(studio_links.find(second))

    def test_claiming_links_and_tells_the_studio(self):
        row, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)) as post:
            with self.captureOnCommitCallbacks(execute=True):
                link = studio_links.claim(row, self.player)
        self.assertEqual((link.user, link.external_id, link.method), (self.player, 'acct-1', 'code'))
        row.refresh_from_db()
        self.assertEqual(row.claimed_by, self.player)
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(sent['event'], 'account_linked')
        self.assertEqual(sent['data'], {'account_id': 'acct-1', 'username': 'code_player',
                                        'method': 'code'})

    def test_a_spent_code_cannot_be_spent_again(self):
        row, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        studio_links.claim(row, self.player)
        self.assertIsNone(studio_links.find(code))

    def test_an_account_already_linked_to_somebody_else_is_refused(self):
        other, _ = a_user('code_other')
        StudioAccountLink.objects.create(game=self.game, user=other, external_id='acct-1',
                                         method='code')
        row, _ = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        with self.assertRaises(LinkRefused) as caught:
            studio_links.claim(row, self.player)
        self.assertEqual(caught.exception.code, 'ACCOUNT_TAKEN')

    def test_one_account_per_player_per_game(self):
        StudioAccountLink.objects.create(game=self.game, user=self.player, external_id='acct-9',
                                         method='code')
        row, _ = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        with self.assertRaises(LinkRefused) as caught:
            studio_links.claim(row, self.player)
        self.assertEqual(caught.exception.code, 'STUDIO_ALREADY_LINKED')

    def test_linking_the_same_account_again_changes_nothing(self):
        link = StudioAccountLink.objects.create(game=self.game, user=self.player,
                                                external_id='acct-1', method='code')
        row, _ = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        self.assertEqual(studio_links.claim(row, self.player), link)

    def test_a_sandbox_game_links_only_its_testers(self):
        _, sandbox = a_studio(self.owner, slug='sandbox-studio')
        row, _ = studio_links.issue(sandbox, 'acct-1', 'Ada#1')
        with self.assertRaises(LinkRefused) as caught:
            studio_links.claim(row, self.player)
        self.assertEqual(caught.exception.code, 'SANDBOX_TESTERS_ONLY')
        sandbox.testers = ['code_player']
        sandbox.save()
        row, _ = studio_links.issue(sandbox, 'acct-1', 'Ada#1')
        self.assertEqual(studio_links.claim(row, self.player).user, self.player)


@override_settings(STUDIO_DELIVERY_INLINE=True)
class StudioLinkApiTests(TestCase):
    def setUp(self):
        self.owner, _ = a_user('api_link_owner')
        self.partner, self.game = a_studio(self.owner, slug='api-link-studio')
        self.game.testers = ['api_player']
        self.game.save()
        self.player, _ = a_user('api_player')
        _, self.key = PartnerApiKey.issue(self.partner, scopes=SANDBOX_SCOPES, environment='sandbox')
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer %s' % self.key}
        self.base = '/api/v1/studio/games/%s' % self.game.slug

    def post(self, path, body):
        return self.client.post(self.base + path, data=body, content_type='application/json',
                                **self.auth)

    def test_the_game_asks_for_a_code(self):
        res = self.post('/link-codes/', {'account_id': 'acct-1', 'display_name': 'Ada#1'})
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()['data']
        self.assertRegex(data['code'], r'^\w{4}-\w{4}$')
        self.assertRegex(data['code_ref'], r'^lc_[0-9a-f]{12}$')
        self.assertTrue(data['link_page'].endswith('/link-game'))
        self.assertNotIn(data['code'], data['link_page'])
        self.assertIn('expires_at', data)

    def test_an_account_id_is_required(self):
        res = self.post('/link-codes/', {'display_name': 'Ada#1'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_INPUT')

    def test_the_game_asks_whether_its_code_was_used(self):
        made = self.post('/link-codes/', {'account_id': 'acct-1', 'display_name': 'Ada#1'}).json()['data']
        path = '%s/link-codes/%s/' % (self.base, made['code_ref'])
        self.assertEqual(self.client.get(path, **self.auth).json()['data']['status'], 'pending')
        studio_links.claim(studio_links.find(made['code']), self.player)
        data = self.client.get(path, **self.auth).json()['data']
        self.assertEqual(data['status'], 'linked')
        self.assertEqual(data['link']['username'], 'api_player')
        StudioLinkCode.objects.filter(ref=made['code_ref']).update(claimed_at=None, claimed_by=None,
                                                                   expires_at=timezone.now())
        self.assertEqual(self.client.get(path, **self.auth).json()['data']['status'], 'expired')

    def test_linking_with_a_sign_in_with_v_ent_token(self):
        token = 'oauth-token-123'
        OAuthAccessToken.objects.create(partner=self.partner, user=self.player,
                                        token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                        scopes=['identity'])
        res = self.post('/links/', {'access_token': token, 'account_id': 'acct-7',
                                    'display_name': 'Ada#7'})
        self.assertEqual(res.status_code, 201, res.content)
        link = StudioAccountLink.objects.get(game=self.game, external_id='acct-7')
        self.assertEqual((link.user, link.method), (self.player, 'oauth'))

    def test_another_partner_s_token_does_not_link(self):
        stranger, _ = a_user('token_stranger')
        other = Partner.objects.create(name='Other', slug='other-oauth', owner=stranger,
                                       contact_name='x', contact_email='x@x.test', status='approved')
        token = 'their-token'
        OAuthAccessToken.objects.create(partner=other, user=self.player,
                                        token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                        scopes=['identity'])
        res = self.post('/links/', {'access_token': token, 'account_id': 'acct-7'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'OAUTH_TOKEN_INVALID')

    def test_an_expired_token_does_not_link(self):
        token = 'old-token'
        row = OAuthAccessToken.objects.create(partner=self.partner, user=self.player,
                                              token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                              scopes=['identity'])
        OAuthAccessToken.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=2))
        res = self.post('/links/', {'access_token': token, 'account_id': 'acct-7'})
        self.assertEqual(res.json()['code'], 'OAUTH_TOKEN_INVALID')

    def test_the_game_looks_a_player_up(self):
        link = StudioAccountLink.objects.create(game=self.game, user=self.player,
                                                external_id='acct-1', external_name='Ada#1',
                                                method='code')
        by_account = self.client.get(self.base + '/links/?account_id=acct-1', **self.auth)
        self.assertEqual(by_account.json()['data']['link']['ref'], link.ref)
        by_name = self.client.get(self.base + '/links/?username=API_PLAYER', **self.auth)
        self.assertEqual(by_name.json()['data']['link']['account_id'], 'acct-1')
        missing = self.client.get(self.base + '/links/?account_id=nobody', **self.auth)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['code'], 'STUDIO_LINK_NOT_FOUND')

    def test_the_game_unlinks_and_says_so(self):
        link = StudioAccountLink.objects.create(game=self.game, user=self.player,
                                                external_id='acct-1', method='code')
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)) as post:
            with self.captureOnCommitCallbacks(execute=True):
                res = self.post('/links/%s/unlink/' % link.ref, {})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(StudioAccountLink.objects.filter(pk=link.pk).exists())
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(sent['event'], 'account_unlinked')
        self.assertEqual(sent['data']['by'], 'studio')

    def test_another_studio_s_link_cannot_be_reached(self):
        owner, _ = a_user('other_link_owner')
        _, other_game = a_studio(owner, slug='other-link-studio')
        theirs = StudioAccountLink.objects.create(game=other_game, user=self.player,
                                                  external_id='acct-1', method='code')
        res = self.post('/links/%s/unlink/' % theirs.ref, {})
        self.assertEqual(res.status_code, 404)
        self.assertTrue(StudioAccountLink.objects.filter(pk=theirs.pk).exists())

    def test_a_key_without_the_accounts_scope_cannot_link(self):
        self.partner.approved_scopes = ['tournaments:read', 'studio:sessions']
        self.partner.save()
        _, narrow = PartnerApiKey.issue(self.partner, scopes=['studio:sessions'], environment='sandbox')
        res = self.client.post(self.base + '/link-codes/', data={'account_id': 'a'},
                               content_type='application/json',
                               HTTP_AUTHORIZATION='Bearer %s' % narrow)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'SCOPE_REQUIRED')
