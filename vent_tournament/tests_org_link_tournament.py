# -*- coding: utf-8 -*-
"""A tournament says which organisation runs it, and a screen can set it.

CEO, 4 September 2026: "how to add events or tournaments to an organization? i
dont see that path". The event half was built the same day; this is the other.

`Tournament.tournament_organization` and `org_link.resolve` have both existed
the whole time, and the create and edit endpoints have accepted the field. What
was missing was the detail payload ever SAYING what it was set to, so no screen
could show it and the edit screen had nothing to fill a picker from. A column
nothing reads and nothing writes is a column that does not exist.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Organization, OrgMember, Users

from .models import Tournament


def a_user(name):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        full_name=name.title(), is_active=True,
        login_session_token=uuid.uuid4().hex[:16])
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


class TournamentCarriesItsOrganisationTests(TestCase):

    def setUp(self):
        self.owner, self.owner_auth = a_user('torgowner')
        self.stranger, self.stranger_auth = a_user('tstranger')
        self.game = Games.objects.create(game_title='EA FC 26 TORG %s'
                                                    % uuid.uuid4().hex[:4])
        self.org = Organization.objects.create(
            org_name='Vermillion T %s' % uuid.uuid4().hex[:5],
            org_creator=self.owner, org_owner=self.owner)
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Org Tournament %s' % uuid.uuid4().hex[:4],
            tournament_game=self.game, tournament_creator=self.owner,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='individual',
            entry_fee='Free', is_draft=False, bracket_type='single_elimination')
        self.ref = self.tournament.slug or self.tournament.tournament_id

    def detail(self, auth=None):
        res = self.client.get('/tournament/view-tournament/%s/' % self.ref,
                              **(auth or {}))
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def test_a_personal_tournament_says_so_rather_than_omitting_the_key(self):
        """A key that is sometimes absent is a key every screen has to guess at."""
        data = self.detail()
        self.assertIn('organization', data)
        self.assertIsNone(data['organization'])

    def test_the_payload_names_the_organisation_once_it_has_one(self):
        self.tournament.tournament_organization = self.org
        self.tournament.save(update_fields=['tournament_organization'])

        org = self.detail()['organization']
        self.assertEqual(org['id'], self.org.org_id)
        self.assertEqual(org['name'], self.org.org_name)
        self.assertIn('slug', org)

    def test_the_owner_can_set_it_from_the_edit_endpoint(self):
        res = self.client.put(
            '/tournament/edit-tournament/%s/' % self.tournament.tournament_id,
            data='{"organization": %s}' % self.org.org_id,
            content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.tournament_organization_id, self.org.org_id)
        self.assertEqual(self.detail()['organization']['id'], self.org.org_id)

    def test_and_can_take_it_off_again(self):
        self.tournament.tournament_organization = self.org
        self.tournament.save(update_fields=['tournament_organization'])

        res = self.client.put(
            '/tournament/edit-tournament/%s/' % self.tournament.tournament_id,
            data='{"organization": ""}',
            content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.tournament.refresh_from_db()
        self.assertIsNone(self.tournament.tournament_organization_id)

    def test_somebody_who_does_not_run_that_organisation_is_refused(self):
        """Refused rather than ignored.

        Dropping it silently would create the tournament under their own name
        and tell them it worked, and they would find out when it never appeared
        on the organisation.
        """
        OrgMember.objects.create(org=self.org, user=self.stranger, role='member')
        theirs = Tournament.objects.create(
            tournament_title='Theirs %s' % uuid.uuid4().hex[:4],
            tournament_game=self.game, tournament_creator=self.stranger,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='individual',
            entry_fee='Free', is_draft=False, bracket_type='single_elimination')

        res = self.client.put(
            '/tournament/edit-tournament/%s/' % theirs.tournament_id,
            data='{"organization": %s}' % self.org.org_id,
            content_type='application/json', **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content[:300])
        self.assertEqual(res.json()['code'], 'ORG_NOT_YOURS')
        theirs.refresh_from_db()
        self.assertIsNone(theirs.tournament_organization_id)
