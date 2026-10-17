"""The delivery log a studio reads, resending, the test ping, and the studio API.

Design section 14, item 5: "a delivery log the studio can read". Readable two
ways: in the portal with the studio's own session, and by key, so a studio's
own tooling can watch it.
"""
import json
from unittest import mock

from django.test import TestCase, override_settings

from vent_partners import studio_delivery
from vent_partners.models import Partner, PartnerApiKey, SANDBOX_SCOPES, StudioGame
from vent_partners.tests_self_serve import a_user
from vent_partners.tests_studio_delivery import POST, PUBLIC, answer


def studio(owner, name='Arena Studio', slug='arena-studio'):
    partner = Partner.objects.create(
        name=name, slug=slug, owner=owner, contact_name='Ada', contact_email='ada@arena.test',
        status='approved', approved_scopes=['tournaments:read'])
    game = StudioGame(partner=partner, name='%s Game' % name, created_by=owner,
                      session_webhook_url='https://api.arena.test/vent/sessions')
    game.set_secret()
    game.save()
    return partner, game


@override_settings(STUDIO_DELIVERY_INLINE=True)
class PortalDeliveryTests(TestCase):
    def setUp(self):
        self.owner, self.auth = a_user('log_owner')
        self.partner, self.game = studio(self.owner)
        self.slug = self.game.slug

    def test_the_log_lists_what_was_sent_newest_first(self):
        first = studio_delivery.queue(self.game, 'event', {'n': 1}, event='ping')
        second = studio_delivery.queue(self.game, 'event', {'n': 2}, event='stage_closed')
        res = self.client.get('/partners/studios/%s/deliveries/' % self.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['data']['deliveries']
        self.assertEqual([r['ref'] for r in rows], [second.ref, first.ref])
        self.assertEqual(set(rows[0]), {
            'ref', 'kind', 'event', 'status', 'attempts', 'last_status_code', 'last_error',
            'response_excerpt', 'created_at', 'delivered_at', 'next_attempt_at'})
        self.assertNotIn('payload', rows[0])

    def test_the_log_filters_by_status(self):
        studio_delivery.queue(self.game, 'event', {}, event='ping')
        res = self.client.get('/partners/studios/%s/deliveries/?status=failed' % self.slug, **self.auth)
        self.assertEqual(res.json()['data']['deliveries'], [])
        bad = self.client.get('/partners/studios/%s/deliveries/?status=sideways' % self.slug, **self.auth)
        self.assertEqual(bad.status_code, 400)

    def test_resend_makes_a_new_row(self):
        first = studio_delivery.queue(self.game, 'event', {'n': 1}, event='ping')
        with mock.patch(PUBLIC, return_value=True), mock.patch(POST, return_value=answer(200)):
            with self.captureOnCommitCallbacks(execute=True):
                res = self.client.post('/partners/studios/%s/deliveries/%s/resend/' % (self.slug, first.ref),
                                       content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertNotEqual(res.json()['data']['delivery']['ref'], first.ref)
        self.assertEqual(res.json()['data']['delivery']['status'], 'delivered')

    def test_a_ping_is_sent_now_and_its_answer_shown(self):
        with mock.patch(PUBLIC, return_value=True), \
                mock.patch(POST, return_value=answer(200, 'pong')) as post:
            res = self.client.post('/partners/studios/%s/ping/' % self.slug,
                                   content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        row = res.json()['data']['delivery']
        self.assertEqual(row['status'], 'delivered')
        self.assertEqual(row['event'], 'ping')
        self.assertEqual(row['response_excerpt'], 'pong')
        self.assertEqual(json.loads(post.call_args.kwargs['data'])['event'], 'ping')

    def test_a_ping_with_no_address_says_so(self):
        self.game.session_webhook_url = ''
        self.game.save()
        res = self.client.post('/partners/studios/%s/ping/' % self.slug,
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'STUDIO_NO_ADDRESS')

    def test_a_stranger_sees_none_of_it(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        _, stranger = a_user('log_stranger')
        for method, path in (
                ('get', '/partners/studios/%s/deliveries/' % self.slug),
                ('post', '/partners/studios/%s/deliveries/%s/resend/' % (self.slug, delivery.ref)),
                ('post', '/partners/studios/%s/ping/' % self.slug)):
            res = getattr(self.client, method)(path, **stranger)
            self.assertEqual(res.status_code, 404, path)

    def test_a_delivery_of_another_game_cannot_be_resent_from_mine(self):
        other_owner, _ = a_user('other_owner')
        _, other_game = studio(other_owner, name='Other Studio', slug='other-studio')
        theirs = studio_delivery.queue(other_game, 'event', {}, event='ping')
        res = self.client.post('/partners/studios/%s/deliveries/%s/resend/' % (self.slug, theirs.ref),
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'DELIVERY_NOT_FOUND')


class StudioApiTests(TestCase):
    def setUp(self):
        self.owner, _ = a_user('api_owner')
        self.partner, self.game = studio(self.owner)
        _, self.test_key = PartnerApiKey.issue(self.partner, scopes=SANDBOX_SCOPES,
                                               environment='sandbox')

    def get(self, path, key=None):
        return self.client.get(path, HTTP_AUTHORIZATION='Bearer %s' % (key or self.test_key))

    def test_the_catalogue_needs_no_key(self):
        res = self.client.get('/api/v1/studio/')
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertIn('result_confirmed', data['events'])
        self.assertIn('create_game_session', data['delivery_types'])
        self.assertEqual(data['signature']['window_seconds'], 300)
        self.assertIn('team', data['expression']['names'])
        self.assertIn('event', data['backoff_seconds'])

    def test_the_studio_reads_its_log_by_key(self):
        delivery = studio_delivery.queue(self.game, 'event', {}, event='ping')
        res = self.get('/api/v1/studio/games/%s/deliveries/' % self.game.slug)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['deliveries'][0]['ref'], delivery.ref)

    def test_no_key_is_refused(self):
        res = self.client.get('/api/v1/studio/games/%s/deliveries/' % self.game.slug)
        self.assertEqual(res.status_code, 401)

    def test_another_partner_s_key_gets_the_missing_answer(self):
        owner, _ = a_user('api_other')
        other, _ = studio(owner, name='Other Studio', slug='other-studio-api')
        _, their_key = PartnerApiKey.issue(other, scopes=SANDBOX_SCOPES, environment='sandbox')
        res = self.get('/api/v1/studio/games/%s/deliveries/' % self.game.slug, key=their_key)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'STUDIO_NOT_FOUND')

    def test_a_test_key_cannot_reach_a_live_game(self):
        self.game.mode, self.game.live_status = 'live', 'approved'
        self.game.save()
        res = self.get('/api/v1/studio/games/%s/deliveries/' % self.game.slug)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'STUDIO_ENVIRONMENT_MISMATCH')

    def test_a_live_key_cannot_reach_a_sandbox_game(self):
        self.partner.approved_scopes = ['tournaments:read'] + SANDBOX_SCOPES
        self.partner.save(update_fields=['approved_scopes'])
        _, live_key = PartnerApiKey.issue(self.partner, scopes=SANDBOX_SCOPES)
        res = self.get('/api/v1/studio/games/%s/deliveries/' % self.game.slug, key=live_key)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'STUDIO_ENVIRONMENT_MISMATCH')

    def test_a_key_without_studio_scopes_is_refused(self):
        _, plain = PartnerApiKey.issue(self.partner, scopes=['tournaments:read'])
        res = self.get('/api/v1/studio/games/%s/deliveries/' % self.game.slug, key=plain)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'SCOPE_REQUIRED')
