"""The read API a studio's client builds on (P6e).

Design section 14, item 6: "Read API and embeds: tournaments, the structure,
brackets, standings, 'my next match'; ... rate limited (R59)." And the
synthesis takeaway 14: rate limit headers on the partner API so a partner can
back off correctly.
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games
from vent_partners.models import Partner, PartnerApiKey, SANDBOX_SCOPES, StudioAccountLink
from vent_partners.tests_self_serve import a_user
from vent_partners.tests_studio_sessions import Fixture, a_studio
from vent_tournament import stage_engine
from vent_tournament.models import BracketMatch, Tournament, TournamentStage


class RateHeaderTests(TestCase):
    def setUp(self):
        cache.clear()
        owner, _ = a_user('rate_owner')
        self.partner = Partner.objects.create(
            name='Rate', slug='rate', owner=owner, contact_name='x', contact_email='x@x.test',
            status='approved', approved_scopes=['tournaments:read'])
        self.key, self.secret = PartnerApiKey.issue(self.partner, scopes=['tournaments:read'],
                                                    rate_limit_per_minute=3)
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer %s' % self.secret}

    def test_every_answer_says_how_much_is_left(self):
        res = self.client.get('/api/v1/tournaments/', **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['X-Rate-Limit-Limit'], '3')
        self.assertEqual(res['X-Rate-Limit-Remaining'], '2')
        self.assertTrue(0 < int(res['X-Rate-Limit-Reset']) <= 60)

    def test_the_answer_past_the_limit_says_when_to_come_back(self):
        for _ in range(3):
            self.client.get('/api/v1/tournaments/', **self.auth)
        res = self.client.get('/api/v1/tournaments/', **self.auth)
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()['code'], 'RATE_LIMITED')
        self.assertEqual(res['X-Rate-Limit-Remaining'], '0')
        self.assertTrue(0 < int(res['Retry-After']) <= 60)


class StructureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.f = Fixture().build()
        self.partner = self.f.game.partner
        self.partner.approved_scopes = ['tournaments:read', 'tournaments:structure:read']
        self.partner.save()
        _, secret = PartnerApiKey.issue(self.partner, scopes=['tournaments:structure:read'])
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer %s' % secret}
        self.groups = TournamentStage.objects.create(tournament=self.f.tournament, order=1,
                                                     label='Groups', format='round_robin', groups=2)
        self.playoff = TournamentStage.objects.create(tournament=self.f.tournament, order=2,
                                                      label='Playoff', format='single_elimination',
                                                      advances=2)

    def test_the_structure_lists_the_stages_in_order(self):
        res = self.client.get('/api/v1/tournaments/%s/structure/' % self.f.tournament.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        stages = res.json()['data']['stages']
        self.assertEqual([s['label'] for s in stages], ['Groups', 'Playoff'])
        self.assertEqual(stages[0]['groups'], 2)
        self.assertEqual(stages[1]['format'], 'single_elimination')
        self.assertIn('matches_total', stages[0])

    def test_standings_are_the_one_ranking_function_s_rows(self):
        res = self.client.get('/api/v1/tournaments/%s/standings/' % self.f.tournament.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        by_label = {s['label']: s['rows'] for s in res.json()['data']['stages']}
        self.assertEqual(by_label['Groups'], stage_engine.standings(self.groups))

    def test_a_private_or_draft_tournament_is_not_found(self):
        Tournament.objects.filter(pk=self.f.tournament.pk).update(tournament_visibility='private')
        res = self.client.get('/api/v1/tournaments/%s/structure/' % self.f.tournament.slug, **self.auth)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'TOURNAMENT_NOT_FOUND')

    def test_the_scope_is_needed(self):
        _, plain = PartnerApiKey.issue(self.partner, scopes=['tournaments:read'])
        res = self.client.get('/api/v1/tournaments/%s/structure/' % self.f.tournament.slug,
                              HTTP_AUTHORIZATION='Bearer %s' % plain)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'SCOPE_REQUIRED')

    def test_the_scope_grants_itself(self):
        from vent_partners.models import SELF_SERVE_SCOPES
        self.assertIn('tournaments:structure:read', SELF_SERVE_SCOPES)


class NextMatchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.f = Fixture().build()
        self.game = self.f.game
        self.partner = self.game.partner
        self.partner.approved_scopes = ['tournaments:read'] + SANDBOX_SCOPES
        self.partner.save()
        _, secret = PartnerApiKey.issue(self.partner, scopes=SANDBOX_SCOPES)
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer %s' % secret}
        StudioAccountLink.objects.create(game=self.game, user=self.f.p1, external_id='acct-one',
                                         method='code')
        self.path = '/api/v1/studio/games/%s/accounts/acct-one/next-match/' % self.game.slug

    def test_the_linked_player_s_next_match(self):
        BracketMatch.objects.filter(pk=self.f.match.pk).update(
            scheduled_at=timezone.now() + timedelta(hours=1))
        # The opponent is named as every screen names a person: the full
        # name, never the bare username (display names catcher, 17 Oct 2026).
        self.f.p2.full_name = 'Side Two Person'
        self.f.p2.save(update_fields=['full_name'])
        res = self.client.get(self.path, **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']['next_match']
        self.assertEqual(data['tournament']['slug'], self.f.tournament.slug)
        self.assertEqual(data['side'], 1)
        self.assertEqual(data['opponent']['name'], 'Side Two Person')
        self.assertEqual(data['match']['round'], 1)
        self.assertIsNone(data['session'])

    def test_nothing_ahead_is_null(self):
        BracketMatch.objects.filter(pk=self.f.match.pk).update(status='completed')
        res = self.client.get(self.path, **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()['data']['next_match'])

    def test_an_unlinked_account_is_refused(self):
        res = self.client.get(self.path.replace('acct-one', 'acct-nobody'), **self.auth)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'ACCOUNT_NOT_LINKED')

    def test_another_studio_s_player_is_not_reachable(self):
        owner, _ = a_user('rival_reader')
        title = Games.objects.get(game_title='Free Fire')
        rival = a_studio(owner, 'rival-reader', title, mode='live', live_status='approved')
        rival.partner.approved_scopes = ['tournaments:read'] + SANDBOX_SCOPES
        rival.partner.save()
        _, their_key = PartnerApiKey.issue(rival.partner, scopes=SANDBOX_SCOPES)
        res = self.client.get(self.path, HTTP_AUTHORIZATION='Bearer %s' % their_key)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'STUDIO_NOT_FOUND')
