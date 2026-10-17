"""The Studios section of the partner portal, and the review that makes a game live.

Design section 14: "A new Studios section in the Partners portal, on top of the
existing partner API keys (9 scopes) and Sign in with V-ENT." Item 1, register a
game. Item 8, "a sandbox and test keys".
"""
from django.test import TestCase

from vent_auth.models import AdminAction, Games
from vent_partners.models import Partner, PartnerApiKey, StudioGame
from vent_partners.tests_admin_controls import an_admin
from vent_partners.tests_self_serve import a_user

SAMPLE = {
    'lineups': [{'side': 1, 'score': 13}, {'side': 2, 'score': 7}],
    'competitors': [{'account_id': 'a1', 'side': 1, 'kills': 3}],
}


class StudioPortalTests(TestCase):
    def setUp(self):
        self.owner, self.auth = a_user('studio_owner')
        self.partner = Partner.objects.create(
            name='Arena Studio', slug='arena-studio', owner=self.owner,
            contact_name='Ada', contact_email='ada@arena.test',
            status='approved', approved_scopes=['tournaments:read'])
        self.title = Games.objects.get_or_create(game_title='Free Fire')[0]

    def post(self, path, body=None, auth=None):
        return self.client.post(path, data=body or {}, content_type='application/json',
                                **(self.auth if auth is None else auth))

    def register(self, **extra):
        body = {'name': 'Arena Clash', 'game_id': self.title.pk,
                'session_webhook_url': 'https://api.arena.test/vent/sessions',
                'events_webhook_url': 'https://api.arena.test/vent/events'}
        body.update(extra)
        return self.post('/partners/studios/register/', body)

    # ------------------------------------------------------------ register

    def test_an_approved_partner_registers_a_game_and_sees_the_secret_once(self):
        res = self.register()
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()['data']
        secret = data['signing_secret']
        self.assertTrue(secret.startswith('vent_whsec_'))
        self.assertEqual(data['game']['slug'], 'arena-clash')
        self.assertEqual(data['game']['mode'], 'sandbox')
        self.assertEqual(data['game']['game'], {'id': self.title.pk, 'title': 'Free Fire'})
        self.assertNotIn('signing_secret_encrypted', data['game'])
        game = StudioGame.objects.get(slug='arena-clash')
        self.assertNotIn(secret, game.signing_secret_encrypted)
        # Never again after issue.
        again = self.client.get('/partners/studios/arena-clash/', **self.auth)
        self.assertNotIn(secret, again.content.decode())
        self.assertEqual(again.json()['data']['game']['secret_hint'], secret[-4:])

    def test_a_partner_waiting_for_review_cannot_register(self):
        self.partner.status = 'pending'
        self.partner.save(update_fields=['status'])
        res = self.register()
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'STUDIO_PARTNER_NOT_APPROVED')

    def test_somebody_who_is_not_a_partner_is_told_so(self):
        _, auth = a_user('nobody')
        res = self.post('/partners/studios/register/', {'name': 'X'}, auth=auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_A_PARTNER')
        listing = self.client.get('/partners/studios/', **auth)
        self.assertEqual(listing.status_code, 200)
        self.assertFalse(listing.json()['data']['can_register'])
        self.assertEqual(listing.json()['data']['refusal_code'], 'NOT_A_PARTNER')

    def test_a_name_is_required(self):
        res = self.register(name='')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'NAME_REQUIRED')

    def test_a_private_webhook_address_is_refused(self):
        res = self.register(session_webhook_url='https://10.0.0.1/hook')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'URL_PRIVATE_HOST')
        self.assertEqual(res.json()['field'], 'session_webhook_url')

    def test_a_schema_keyword_we_cannot_check_is_refused(self):
        res = self.register(result_schema={'type': 'object', 'pattern': 'x'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'SCHEMA_UNKNOWN_KEYWORD')
        self.assertEqual(res.json()['data']['problems'], ['SCHEMA_UNKNOWN_KEYWORD:pattern'])

    def test_an_expression_that_does_not_parse_is_refused_with_where(self):
        res = self.register(scoring_expression='team.score +* 2')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'EXPRESSION_SYNTAX')
        self.assertEqual(res.json()['data']['position'], 12)
        self.assertEqual(res.json()['position'], 12)

    def test_an_unknown_game_is_refused(self):
        res = self.register(game_id=99999)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'GAME_NOT_FOUND')

    def test_an_unknown_event_is_refused(self):
        res = self.register(subscribed_events=['result_confirmed', 'match_teleported'])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'UNKNOWN_EVENT')

    def test_ten_games_is_the_limit(self):
        for n in range(10):
            self.assertEqual(self.register(name='Game %d' % n).status_code, 201)
        res = self.register(name='One too many')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'STUDIO_LIMIT')
        self.assertEqual(res.json()['limit'], 10)

    def test_a_name_that_would_shadow_a_route_gets_another_address(self):
        res = self.register(name='Register')
        self.assertEqual(res.status_code, 201)
        self.assertNotEqual(res.json()['data']['game']['slug'], 'register')

    def test_testers_must_be_real_accounts(self):
        a_user('tester_ok')
        res = self.register(testers=['tester_ok', 'ghost_person'])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'TESTER_NOT_FOUND')
        ok = self.register(name='With testers', testers=['tester_ok'])
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()['data']['game']['testers'], ['tester_ok'])

    # -------------------------------------------------------------- reading

    def test_the_listing_shows_my_games_and_that_i_may_register(self):
        self.register()
        res = self.client.get('/partners/studios/', **self.auth)
        data = res.json()['data']
        self.assertTrue(data['can_register'])
        self.assertEqual([g['slug'] for g in data['games']], ['arena-clash'])
        self.assertIn('studio:sessions', data['scopes'])
        self.assertIn('result_confirmed', data['events'])

    def test_signed_out_every_route_answers_401(self):
        self.register()
        for method, path in (('get', '/partners/studios/'),
                             ('post', '/partners/studios/register/'),
                             ('get', '/partners/studios/arena-clash/'),
                             ('post', '/partners/studios/arena-clash/update/'),
                             ('post', '/partners/studios/arena-clash/rotate-secret/'),
                             ('post', '/partners/studios/arena-clash/request-live/'),
                             ('post', '/partners/studios/arena-clash/try-scoring/'),
                             ('post', '/partners/studios/sandbox-key/')):
            res = getattr(self.client, method)(path)
            self.assertEqual(res.status_code, 401, path)

    def test_a_stranger_gets_the_same_answer_as_a_missing_game(self):
        self.register()
        _, stranger = a_user('stranger')
        for method, path in (('get', '/partners/studios/arena-clash/'),
                             ('post', '/partners/studios/arena-clash/update/'),
                             ('post', '/partners/studios/arena-clash/rotate-secret/'),
                             ('post', '/partners/studios/arena-clash/request-live/'),
                             ('post', '/partners/studios/arena-clash/try-scoring/')):
            theirs = getattr(self.client, method)(path, **stranger)
            missing = getattr(self.client, method)(path.replace('arena-clash', 'no-such-game'),
                                                   **stranger)
            self.assertEqual(theirs.status_code, 404, path)
            self.assertEqual(theirs.json()['code'], 'STUDIO_NOT_FOUND')
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.json()['code'], 'STUDIO_NOT_FOUND')

    def test_a_renamed_game_answers_moved_at_its_old_address(self):
        self.register()
        self.post('/partners/studios/arena-clash/update/', {'name': 'Arena Clash Mobile'})
        res = self.client.get('/partners/studios/arena-clash/', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'moved')
        self.assertEqual(res.json()['data']['slug'], 'arena-clash-mobile')

    # -------------------------------------------------------------- changing

    def test_update_takes_named_fields_and_nothing_else(self):
        self.register()
        res = self.post('/partners/studios/arena-clash/update/', {
            'scoring_expression': 'competitors.sum(it.kills)',
            'mode': 'live', 'live_status': 'approved', 'partner': 99,
        })
        self.assertEqual(res.status_code, 200, res.content)
        game = StudioGame.objects.get(slug='arena-clash')
        self.assertEqual(game.scoring_expression, 'competitors.sum(it.kills)')
        self.assertEqual(game.mode, 'sandbox')
        self.assertEqual(game.live_status, 'none')
        self.assertEqual(game.partner_id, self.partner.pk)

    def test_rotating_the_secret_shows_the_new_one_once(self):
        first = self.register().json()['data']['signing_secret']
        res = self.post('/partners/studios/arena-clash/rotate-secret/')
        self.assertEqual(res.status_code, 200)
        second = res.json()['data']['signing_secret']
        self.assertNotEqual(first, second)
        self.assertTrue(res.json()['data']['previous_valid_until'])
        game = StudioGame.objects.get(slug='arena-clash')
        self.assertEqual(game.signing_secrets(), [second, first])

    def test_try_scoring_answers_both_sides(self):
        self.register()
        res = self.post('/partners/studios/arena-clash/try-scoring/', {'result': SAMPLE})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['scores'], [13, 7])
        self.assertEqual(res.json()['data']['envelope_errors'], [])

    def test_try_scoring_an_unsaved_expression(self):
        self.register()
        res = self.post('/partners/studios/arena-clash/try-scoring/', {
            'result': SAMPLE, 'scoring_expression': 'competitors.sum(it.kills)'})
        self.assertEqual(res.json()['data']['scores'], [3, 0])

    def test_try_scoring_says_what_is_wrong_with_the_result(self):
        self.register(result_schema={'type': 'object', 'required': ['general']})
        res = self.post('/partners/studios/arena-clash/try-scoring/', {'result': SAMPLE})
        self.assertEqual(res.json()['data']['schema_errors'], [{'path': '$', 'code': 'REQUIRED'}])
        bad = self.post('/partners/studios/arena-clash/try-scoring/',
                        {'result': {'lineups': [{'side': 1}]}})
        self.assertEqual(bad.json()['data']['envelope_errors'],
                         [{'path': '$.lineups', 'code': 'TWO_LINEUPS'}])

    def test_try_scoring_reports_an_expression_failure_as_a_code(self):
        self.register()
        res = self.post('/partners/studios/arena-clash/try-scoring/', {
            'result': SAMPLE, 'scoring_expression': 'team.score / 0'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'EXPRESSION_DIVIDE_BY_ZERO')

    def test_going_live_needs_the_pieces_first(self):
        self.register(session_webhook_url='', game_id=None)
        res = self.post('/partners/studios/arena-clash/request-live/')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'STUDIO_NOT_READY')
        self.assertEqual(res.json()['data']['missing'], ['game', 'session_webhook_url'])

    def test_asking_to_go_live(self):
        self.register()
        res = self.post('/partners/studios/arena-clash/request-live/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['game']['live_status'], 'requested')
        self.assertEqual(StudioGame.objects.get(slug='arena-clash').mode, 'sandbox')

    def test_a_sandbox_key_comes_from_here(self):
        res = self.post('/partners/studios/sandbox-key/')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(res.json()['data']['secret'].startswith('vent_pk_test_'))
        key = PartnerApiKey.objects.get(partner=self.partner, environment='sandbox')
        self.assertTrue(key.allows('studio:sessions'))


class StudioReviewTests(TestCase):
    def setUp(self):
        self.admin, self.admin_auth = an_admin()
        self.owner, self.auth = a_user('studio_owner2')
        self.partner = Partner.objects.create(
            name='Arena Studio', slug='arena-studio-2', owner=self.owner,
            contact_name='Ada', contact_email='ada@arena.test',
            status='approved', approved_scopes=['tournaments:read'])
        self.game = StudioGame(partner=self.partner, name='Arena Clash', live_status='requested',
                               session_webhook_url='https://api.arena.test/vent')
        self.game.set_secret()
        self.game.save()

    def review(self, decision, auth=None):
        return self.client.post('/partners/admin/studios/%s/review/' % self.game.slug,
                                data={'decision': decision, 'note': 'Checked the integration'},
                                content_type='application/json', **(auth or self.admin_auth))

    def test_a_super_admin_lists_games_waiting(self):
        res = self.client.get('/partners/admin/studios/', **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['counts']['requested'], 1)
        self.assertEqual(res.json()['data']['games'][0]['partner']['name'], 'Arena Studio')

    def test_approving_makes_it_live_and_grants_the_studio_scopes(self):
        res = self.review('approved')
        self.assertEqual(res.status_code, 200, res.content)
        self.game.refresh_from_db()
        self.partner.refresh_from_db()
        self.assertTrue(self.game.is_live)
        self.assertEqual(self.game.reviewed_by, self.admin)
        self.assertIn('studio:sessions', self.partner.approved_scopes)
        self.assertIn('studio:accounts', self.partner.approved_scopes)
        self.assertIn('tournaments:read', self.partner.approved_scopes)
        self.assertTrue(AdminAction.objects.filter(action_type='review_studio_game',
                                                   target_id=str(self.game.pk)).exists())

    def test_rejecting_keeps_it_in_the_sandbox(self):
        self.review('rejected')
        self.game.refresh_from_db()
        self.assertEqual(self.game.live_status, 'rejected')
        self.assertEqual(self.game.mode, 'sandbox')
        self.assertEqual(self.game.review_note, 'Checked the integration')

    def test_suspending_a_live_game_takes_it_off_live(self):
        self.review('approved')
        self.review('suspended')
        self.game.refresh_from_db()
        self.assertFalse(self.game.is_live)
        self.assertEqual(self.game.mode, 'sandbox')

    def test_a_decision_must_be_one_of_three(self):
        res = self.review('maybe')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'STUDIO_BAD_DECISION')

    def test_an_ordinary_admin_may_not_review(self):
        _, ordinary = an_admin(role='admin')
        res = self.review('approved', auth=ordinary)
        self.assertEqual(res.status_code, 403)

    def test_a_member_of_the_public_may_not_review(self):
        res = self.review('approved', auth=self.auth)
        self.assertIn(res.status_code, (401, 403))
        self.game.refresh_from_db()
        self.assertFalse(self.game.is_live)
