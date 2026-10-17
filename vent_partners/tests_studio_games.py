"""A studio's game, its secret, and the test keys it builds with.

Design section 14, item 1: "Register a game: webhook addresses, a signing
secret (encrypted at rest, R63), a result schema (JSON Schema), a scoring
expression, sandbox or live." Item 8: "a sandbox and test keys".

A sandbox key carries the studio scopes without a review, because a sandbox
game only ever reaches its own studio's test tournaments and its own testers.
Going live is what an admin reviews.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_partners.models import (
    PartnerApiKey, SANDBOX_SCOPES, StudioGame,
)
from vent_partners.tests_api import make_partner


def make_game(partner=None, **extra):
    partner = partner or make_partner()
    defaults = dict(partner=partner, name='Arena Clash', created_by=partner.owner)
    defaults.update(extra)
    game = StudioGame(**defaults)
    secret = game.set_secret()
    game.save()
    return game, secret


class SandboxKeyTests(TestCase):
    def setUp(self):
        self.partner = make_partner(approved_scopes=['tournaments:read'])

    def test_a_sandbox_key_says_so_in_its_name(self):
        key, plaintext = PartnerApiKey.issue(
            self.partner, scopes=SANDBOX_SCOPES, environment='sandbox')
        self.assertTrue(plaintext.startswith('vent_pk_test_'), plaintext)
        self.assertEqual(key.environment, 'sandbox')

    def test_a_sandbox_key_carries_the_studio_scopes_without_a_review(self):
        key, _ = PartnerApiKey.issue(self.partner, scopes=SANDBOX_SCOPES, environment='sandbox')
        self.assertEqual(sorted(key.scopes), sorted(SANDBOX_SCOPES))
        self.assertTrue(key.allows('studio:sessions'))
        self.assertTrue(key.allows('studio:accounts'))

    def test_a_sandbox_key_never_carries_a_reviewed_read_scope_the_partner_lacks(self):
        key, _ = PartnerApiKey.issue(
            self.partner, scopes=SANDBOX_SCOPES + ['tournaments:brackets:read'],
            environment='sandbox')
        self.assertNotIn('tournaments:brackets:read', key.scopes)
        self.assertFalse(key.allows('tournaments:brackets:read'))

    def test_a_live_key_does_not_get_studio_scopes_for_free(self):
        key, _ = PartnerApiKey.issue(self.partner, scopes=SANDBOX_SCOPES)
        self.assertEqual(key.scopes, [])
        self.assertFalse(key.allows('studio:sessions'))

    def test_suspending_the_partner_stops_the_sandbox_key(self):
        key, _ = PartnerApiKey.issue(self.partner, scopes=SANDBOX_SCOPES, environment='sandbox')
        self.partner.status = 'suspended'
        self.partner.save(update_fields=['status'])
        key.refresh_from_db()
        self.assertFalse(key.allows('studio:sessions'))

    def test_the_sandbox_key_signs_in_like_any_other(self):
        _, plaintext = PartnerApiKey.issue(self.partner, scopes=SANDBOX_SCOPES,
                                           environment='sandbox')
        res = self.client.get('/api/v1/whoami/', HTTP_AUTHORIZATION='Bearer %s' % plaintext)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(sorted(res.json()['data']['scopes']), sorted(SANDBOX_SCOPES))

    def test_existing_keys_are_live(self):
        key, plaintext = PartnerApiKey.issue(self.partner, scopes=['tournaments:read'])
        self.assertEqual(key.environment, 'live')
        self.assertFalse(plaintext.startswith('vent_pk_test_'))


class StudioGameTests(TestCase):
    def test_the_secret_is_stored_locked(self):
        game, secret = make_game()
        game.refresh_from_db()
        self.assertTrue(secret.startswith('vent_whsec_'))
        self.assertNotIn(secret, game.signing_secret_encrypted)
        self.assertEqual(game.signing_secrets(), [secret])
        self.assertEqual(game.secret_hint, secret[-4:])

    def test_a_new_game_is_a_sandbox_game(self):
        game, _ = make_game()
        self.assertEqual(game.mode, 'sandbox')
        self.assertEqual(game.live_status, 'none')
        self.assertFalse(game.is_live)
        self.assertEqual(game.environment, 'sandbox')
        self.assertEqual(game.scoring_expression, 'team.score')

    def test_a_rotation_keeps_the_old_secret_for_a_day(self):
        game, old = make_game()
        new = game.rotate_secret()
        game.refresh_from_db()
        self.assertNotEqual(old, new)
        self.assertEqual(game.signing_secrets(), [new, old])
        later = timezone.now() + timedelta(hours=24, minutes=1)
        self.assertEqual(game.signing_secrets(now=later), [new])

    def test_rotating_twice_forgets_the_first(self):
        game, first = make_game()
        second = game.rotate_secret()
        third = game.rotate_secret()
        self.assertEqual(game.signing_secrets(), [third, second])
        self.assertNotIn(first, game.signing_secrets())

    def test_the_slug_follows_the_name_and_the_old_one_still_resolves(self):
        from vent_auth.slugs import resolve_or_redirect

        game, _ = make_game(name='Arena Clash')
        self.assertEqual(game.slug, 'arena-clash')
        game.name = 'Arena Clash Mobile'
        game.save()
        self.assertEqual(game.slug, 'arena-clash-mobile')
        found, moved = resolve_or_redirect('arena-clash', entity_type='studio_game',
                                           id_field='id', model=StudioGame)
        self.assertIsNone(found)
        self.assertEqual(moved, 'arena-clash-mobile')

    def test_live_means_approved_and_switched_on(self):
        game, _ = make_game(mode='live', live_status='approved')
        self.assertTrue(game.is_live)
        self.assertEqual(game.environment, 'live')
        game.live_status = 'requested'
        self.assertFalse(game.is_live)

    def test_who_may_test_a_sandbox_game(self):
        game, _ = make_game()
        tester = Users.objects.create(username='tester1', email='t1@vent.test')
        stranger = Users.objects.create(username='stranger1', email='s1@vent.test')
        game.testers = ['tester1']
        self.assertTrue(game.may_test(game.partner.owner))
        self.assertTrue(game.may_test(tester))
        self.assertFalse(game.may_test(stranger))
        self.assertFalse(game.may_test(None))

    def test_an_unreadable_secret_signs_nothing(self):
        game, _ = make_game()
        game.signing_secret_encrypted = 'not-a-token'
        self.assertEqual(game.signing_secrets(), [])

    def test_a_game_can_name_the_v_ent_game_it_serves(self):
        title = Games.objects.get_or_create(game_title='Free Fire')[0]
        game, _ = make_game(game=title)
        self.assertEqual(game.game.game_title, 'Free Fire')
