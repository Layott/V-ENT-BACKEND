"""The player's side of linking: type the code from the game, see whose
account it is, confirm, and unlink later (P6c).

The confirm step shows the game account's name before anything is linked,
because a code somebody else made and sent you would otherwise tie their game
account to your V-ENT account.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings

from vent_partners import studio_links
from vent_partners.models import StudioAccountLink
from vent_partners.tests_self_serve import a_user
from vent_partners.tests_studio_links import a_studio


@override_settings(STUDIO_DELIVERY_INLINE=True)
class PlayerLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, _ = a_user('pl_owner')
        self.partner, self.game = a_studio(self.owner, slug='pl-studio', mode='live',
                                           live_status='approved')
        self.player, self.auth = a_user('pl_player')

    def post(self, path, body=None, auth=None):
        return self.client.post(path, data=body or {}, content_type='application/json',
                                **(self.auth if auth is None else auth))

    def test_preview_names_the_game_and_the_account_before_anything_links(self):
        _, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        res = self.post('/partners/studios/link/preview/', {'code': code.lower()})
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(data['account']['display_name'], 'Ada#1')
        self.assertEqual(data['game']['name'], self.game.name)
        self.assertEqual(data['game']['studio'], 'Link Studio')
        self.assertFalse(StudioAccountLink.objects.exists())

    def test_confirm_links(self):
        _, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        res = self.post('/partners/studios/link/confirm/', {'code': code})
        self.assertEqual(res.status_code, 201, res.content)
        link = StudioAccountLink.objects.get(game=self.game)
        self.assertEqual(link.user, self.player)
        self.assertEqual(res.json()['data']['link']['display_name'], 'Ada#1')

    def test_a_wrong_code_is_not_found(self):
        res = self.post('/partners/studios/link/preview/', {'code': 'AAAA-AAAA'})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'LINK_CODE_NOT_FOUND')

    def test_a_refusal_carries_its_code(self):
        other, _ = a_user('pl_other')
        StudioAccountLink.objects.create(game=self.game, user=other, external_id='acct-1',
                                         method='code')
        _, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        res = self.post('/partners/studios/link/confirm/', {'code': code})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'ACCOUNT_TAKEN')

    @override_settings(AUTH_THROTTLE_ENABLED=True)
    def test_guessing_codes_is_cut_off(self):
        answers = [self.post('/partners/studios/link/preview/', {'code': 'BBBB-BBB%d' % (n % 8 + 2)})
                   for n in range(11)]
        self.assertEqual(answers[-1].status_code, 429)
        self.assertEqual(answers[-1].json()['code'], 'TOO_MANY_ATTEMPTS')

    def test_signed_out_is_refused(self):
        _, code = studio_links.issue(self.game, 'acct-1', 'Ada#1')
        for path in ('/partners/studios/link/preview/', '/partners/studios/link/confirm/'):
            res = self.client.post(path, data={'code': code}, content_type='application/json')
            self.assertEqual(res.status_code, 401, path)
        self.assertEqual(self.client.get('/partners/studios/my-links/').status_code, 401)

    def test_my_links_and_unlinking(self):
        link = StudioAccountLink.objects.create(game=self.game, user=self.player,
                                                external_id='acct-1', external_name='Ada#1',
                                                method='code')
        mine = self.client.get('/partners/studios/my-links/', **self.auth)
        self.assertEqual(mine.status_code, 200)
        self.assertEqual([r['ref'] for r in mine.json()['data']['links']], [link.ref])
        res = self.post('/partners/studios/my-links/%s/unlink/' % link.ref)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(StudioAccountLink.objects.filter(pk=link.pk).exists())

    def test_somebody_else_s_link_answers_as_missing(self):
        other, other_auth = a_user('pl_intruder')
        link = StudioAccountLink.objects.create(game=self.game, user=self.player,
                                                external_id='acct-1', method='code')
        res = self.post('/partners/studios/my-links/%s/unlink/' % link.ref, auth=other_auth)
        missing = self.post('/partners/studios/my-links/ln_000000000000/unlink/', auth=other_auth)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(res.json()['code'], missing.json()['code'])
        self.assertTrue(StudioAccountLink.objects.filter(pk=link.pk).exists())
