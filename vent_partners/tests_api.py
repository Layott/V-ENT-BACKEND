"""The scoped partner API.

What matters here is refusal: a key must open exactly what it was granted and
nothing beside it, and it must stop working the moment its partner does.
"""
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Teams, Users
from vent_event.models import Event
from vent_tournament.models import Tournament

from vent_partners.models import Partner, PartnerApiKey


def make_partner(**kwargs):
    owner = kwargs.pop('owner', None) or Users.objects.create(
        username=f'owner{Users.objects.count()}', email=f'o{Users.objects.count()}@vent.test',
    )
    defaults = dict(
        name='Test Partner', slug=f'test-partner-{Partner.objects.count()}', owner=owner,
        contact_name='Contact', contact_email='partner@vent.test',
        status='approved', approved_scopes=['tournaments:read'],
    )
    defaults.update(kwargs)
    return Partner.objects.create(**defaults)


class KeyAuthTests(TestCase):
    def setUp(self):
        self.partner = make_partner()
        self.key, self.secret = PartnerApiKey.issue(
            self.partner, scopes=['tournaments:read'],
        )

    def auth(self, token=None):
        return {'HTTP_AUTHORIZATION': f'Bearer {token or self.secret}'}

    def test_the_index_needs_no_key(self):
        res = self.client.get('/api/v1/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('tournaments:read', res.json()['data']['scopes'])

    def test_no_key_is_refused(self):
        res = self.client.get('/api/v1/tournaments/')
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()['code'], 'MISSING_KEY')

    def test_a_made_up_key_is_refused(self):
        res = self.client.get('/api/v1/tournaments/', **self.auth('vent_pk_deadbeef.nonsense'))
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()['code'], 'INVALID_KEY')

    def test_a_key_with_the_wrong_secret_is_refused(self):
        wrong = f'{PartnerApiKey.PREFIX}{self.key.key_id}.not-the-secret'
        res = self.client.get('/api/v1/tournaments/', **self.auth(wrong))
        self.assertEqual(res.status_code, 401)

    def test_whoami_reports_the_partner_and_scopes(self):
        res = self.client.get('/api/v1/whoami/', **self.auth())
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertEqual(data['partner'], 'Test Partner')
        self.assertEqual(data['scopes'], ['tournaments:read'])

    def test_the_secret_is_never_stored_in_the_clear(self):
        self.assertNotIn(self.secret.split('.')[-1], self.key.secret_hash)
        self.assertEqual(len(self.key.secret_hash), 64)

    def test_a_revoked_key_stops_working(self):
        self.key.revoked_at = timezone.now()
        self.key.save(update_fields=['revoked_at'])
        res = self.client.get('/api/v1/tournaments/', **self.auth())
        self.assertEqual(res.status_code, 401)

    def test_suspending_the_partner_kills_the_key(self):
        self.partner.status = 'suspended'
        self.partner.save(update_fields=['status'])
        res = self.client.get('/api/v1/tournaments/', **self.auth())
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'PARTNER_INACTIVE')


class ScopeTests(TestCase):
    def setUp(self):
        self.partner = make_partner(approved_scopes=['tournaments:read'])
        self.key, self.secret = PartnerApiKey.issue(self.partner, scopes=['tournaments:read'])
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.secret}'}

        game = Games.objects.get_or_create(game_title='Free Fire')[0]
        creator = Users.objects.create(username='creator', email='c@vent.test')
        self.tournament = Tournament.objects.create(
            tournament_title='Open Cup', tournament_game=game, tournament_creator=creator,
            start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
            tournament_visibility='public', is_draft=False, bracket_type='battle_royale',
            max_number_of_teams=8,
        )
        self.private = Tournament.objects.create(
            tournament_title='Invite Only', tournament_game=game, tournament_creator=creator,
            start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
            tournament_visibility='private', is_draft=False,
        )
        self.draft = Tournament.objects.create(
            tournament_title='Unfinished', tournament_game=game, tournament_creator=creator,
            start_date_and_time=timezone.now(), end_date_and_time=timezone.now(),
            tournament_visibility='public', is_draft=True,
        )
        Event.objects.create(
            name='Lagos Meetup', game=game, creator=creator, event_type='physical',
            desc='A meetup', entry_fee=0, reg_start_date=timezone.now(),
            reg_end_date=timezone.now(), event_date=timezone.now().date(),
            start_time=timezone.now().time(), end_time=timezone.now().time(),
        )
        Teams.objects.create(
            team_name='Lagos Rangers', game=game, description='', team_creator=creator,
            team_owner=creator, penalty_points=0, number_of_members=1,
        )

    def test_a_granted_scope_opens_its_endpoint(self):
        res = self.client.get('/api/v1/tournaments/', **self.auth)
        self.assertEqual(res.status_code, 200)
        titles = [r['title'] for r in res.json()['data']['results']]
        self.assertIn('Open Cup', titles)

    def test_drafts_and_private_tournaments_are_never_served(self):
        res = self.client.get('/api/v1/tournaments/', **self.auth)
        titles = [r['title'] for r in res.json()['data']['results']]
        self.assertNotIn('Invite Only', titles)
        self.assertNotIn('Unfinished', titles)

    def test_a_scope_that_was_not_granted_is_refused(self):
        for path in ('/api/v1/events/', '/api/v1/teams/', '/api/v1/players/creator/',
                     '/api/v1/rankings/'):
            with self.subTest(path=path):
                res = self.client.get(path, **self.auth)
                self.assertEqual(res.status_code, 403, path)
                self.assertEqual(res.json()['code'], 'SCOPE_REQUIRED')

    def test_participants_need_their_own_scope(self):
        res = self.client.get(f'/api/v1/tournaments/{self.tournament.pk}/participants/', **self.auth)
        self.assertEqual(res.status_code, 403)

    def test_a_key_cannot_be_issued_beyond_what_the_partner_holds(self):
        key, _ = PartnerApiKey.issue(self.partner, scopes=['tournaments:read', 'players:read'])
        self.assertEqual(key.scopes, ['tournaments:read'])

    def test_granting_more_scopes_opens_more(self):
        self.partner.approved_scopes = ['tournaments:read', 'events:read']
        self.partner.save(update_fields=['approved_scopes'])
        key, secret = PartnerApiKey.issue(
            self.partner, scopes=['tournaments:read', 'events:read'],
        )
        res = self.client.get('/api/v1/events/', HTTP_AUTHORIZATION=f'Bearer {secret}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['results'][0]['name'], 'Lagos Meetup')

    def test_removing_a_scope_from_the_partner_closes_it_for_existing_keys(self):
        self.partner.approved_scopes = []
        self.partner.save(update_fields=['approved_scopes'])
        res = self.client.get('/api/v1/tournaments/', **self.auth)
        self.assertEqual(res.status_code, 403)

    def test_player_stats_are_a_separate_scope(self):
        self.partner.approved_scopes = ['players:read']
        self.partner.save(update_fields=['approved_scopes'])
        _, secret = PartnerApiKey.issue(self.partner, scopes=['players:read'])
        res = self.client.get('/api/v1/players/creator/', HTTP_AUTHORIZATION=f'Bearer {secret}')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('stats', res.json()['data'])

        self.partner.approved_scopes = ['players:read', 'players:stats:read']
        self.partner.save(update_fields=['approved_scopes'])
        _, secret2 = PartnerApiKey.issue(
            self.partner, scopes=['players:read', 'players:stats:read'],
        )
        res2 = self.client.get('/api/v1/players/creator/', HTTP_AUTHORIZATION=f'Bearer {secret2}')
        self.assertIn('stats', res2.json()['data'])

    def test_pagination_is_bounded(self):
        res = self.client.get('/api/v1/tournaments/?page_size=5000', **self.auth)
        self.assertLessEqual(res.json()['data']['page_size'], 100)


class BrandTests(TestCase):
    """A partner showing V-ENT data needs something to show it as.

    Without this they take a logo off the website at whatever size they find it,
    or they show nothing and the data reads as theirs.
    """

    def test_the_index_carries_the_marks_and_how_to_use_them(self):
        res = self.client.get('/api/v1/')
        self.assertEqual(res.status_code, 200, res.content)
        brand = res.json()['data']['brand']
        self.assertEqual(brand['name'], 'V-ENT')
        self.assertTrue(brand['logo'].endswith('.png'))
        self.assertTrue(brand['logo_svg'].endswith('.svg'))
        self.assertTrue(brand['attribution'])
        self.assertTrue(brand['usage'])

    def test_the_marks_need_no_key(self):
        """Somebody deciding whether to integrate has not got a key yet."""
        res = self.client.get('/api/v1/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('brand', res.json()['data'])
