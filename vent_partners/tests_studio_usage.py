"""Usage a studio and a super admin can read (P6f).

Design section 14, item 8: "Usage (monthly active players, sessions) is
counted from day one so either pricing model can be chosen later."
"""
from django.test import TestCase, override_settings

from vent_partners import studio_sessions
from vent_partners.models import StudioAccountLink, StudioTournamentLink
from vent_partners.tests_admin_controls import an_admin
from vent_partners.tests_self_serve import a_user
from vent_partners.tests_studio_sessions import Fixture


@override_settings(STUDIO_DELIVERY_INLINE=True)
class UsageTests(TestCase):
    def setUp(self):
        self.f = Fixture().build()
        self.game = self.f.game
        self.owner_auth = {'HTTP_AUTHORIZATION': 'Bearer %s' % self.f.studio_owner.login_session_token}
        link = StudioTournamentLink.objects.create(tournament=self.f.tournament, game=self.game)
        StudioAccountLink.objects.create(game=self.game, user=self.f.p1, external_id='a1', method='code')
        StudioAccountLink.objects.create(game=self.game, user=self.f.p2, external_id='a2', method='code')
        studio_sessions.create(link, self.f.match, 1, 1)
        studio_sessions.create(link, self.f.match, 1, 2)

    def test_the_studio_reads_its_months(self):
        res = self.client.get('/partners/studios/%s/usage/' % self.game.slug, **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)
        months = res.json()['data']['months']
        self.assertEqual(len(months), 1)
        self.assertEqual((months[0]['active_players'], months[0]['sessions']), (2, 2))

    def test_a_stranger_gets_the_missing_answer(self):
        _, stranger = a_user('usage_stranger')
        res = self.client.get('/partners/studios/%s/usage/' % self.game.slug, **stranger)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'STUDIO_NOT_FOUND')

    def test_the_review_list_shows_this_month(self):
        _, admin_auth = an_admin()
        res = self.client.get('/partners/admin/studios/', **admin_auth)
        row = res.json()['data']['games'][0]
        self.assertEqual((row['active_players_this_month'], row['sessions_this_month']), (2, 2))
