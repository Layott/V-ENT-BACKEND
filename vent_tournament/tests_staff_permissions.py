# -*- coding: utf-8 -*-
"""Granular roles on a tournament (P7, design section 16).

The design's row for P7: "granular roles (structure, report, place entrants,
standings, caster)". Until now a helper on a tournament was a scorekeeper and
nothing else: somebody who could enter results. Real events split the work
further (the research, Toornament's eight permission attributes and
Challengermode's caster role), so a helper now holds a list of permissions and
every door asks for the one it needs. The organiser and an admin hold them all.

This file pins the model (ModelTests), what a viewer holds and what every
screen is told (AccessTests), naming helpers (StaffApiTests) and the doors
(DoorMatrixTests).
"""
import importlib

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import RoleInvite, Users

from . import access
from .models import TournamentStaff
from .tests import client_for, make_tournament, make_user


def two_factor(user):
    """An admin session that met the authenticator, as may_override asks."""
    user.login_session_2fa_at = timezone.now()
    user.save(update_fields=['login_session_2fa_at'])
    return user


class ModelTests(TestCase):
    def setUp(self):
        self.organiser = make_user(7101)
        self.helper = make_user(7102)
        self.t = make_tournament(self.organiser)

    def test_existing_rows_become_report(self):
        # Every row written before 17 October 2026 was a scorekeeper, and a
        # scorekeeper could enter results and nothing else.
        row = TournamentStaff.objects.create(tournament=self.t, user=self.helper, permissions=[])
        migration = importlib.import_module('vent_tournament.migrations.0072_staff_permissions')
        migration.scorekeepers_report(django_apps, None)
        row.refresh_from_db()
        self.assertEqual(row.permissions, ['report'])

    def test_clean_orders_and_refuses(self):
        self.assertEqual(access.clean_permissions(['report', 'structure']), ['structure', 'report'])
        self.assertEqual(access.clean_permissions(['caster', 'caster']), ['caster'])
        for raw, code in (([], 'PERMISSIONS_REQUIRED'), (None, 'PERMISSIONS_REQUIRED'),
                          (['boss'], 'UNKNOWN_PERMISSION'), ('report', 'UNKNOWN_PERMISSION'),
                          ([3], 'UNKNOWN_PERMISSION')):
            with self.assertRaises(access.PermissionsError) as caught:
                access.clean_permissions(raw)
            self.assertEqual(caught.exception.code, code, raw)

    def test_an_accepted_invite_writes_its_permissions(self):
        from vent_auth import invites
        invite = invites.offer_role(RoleInvite.KIND_TOURNAMENT, self.t, 'placer@example.com',
                                    'staff', self.organiser, scopes=['place'])
        self.assertEqual(invite.scopes, ['place'])
        person = Users.objects.create(username='placer', email='placer@example.com',
                                      is_active=True, login_session_token='placer-tok')
        invites.answer(invite, person, True)
        self.assertEqual(TournamentStaff.objects.get(tournament=self.t, user=person).permissions,
                         ['place'])

    def test_an_old_scorekeeper_invite_still_grants_report(self):
        from vent_auth import invites
        invite = RoleInvite.objects.create(kind=RoleInvite.KIND_TOURNAMENT, tournament=self.t,
                                           email='old@example.com', role='scorekeeper',
                                           invited_by=self.organiser, token='ri_oldscorekeeper01')
        person = Users.objects.create(username='oldkeeper', email='old@example.com',
                                      is_active=True, login_session_token='old-tok')
        invites.answer(invite, person, True)
        self.assertEqual(TournamentStaff.objects.get(tournament=self.t, user=person).permissions,
                         ['report'])


class AccessTests(TestCase):
    def setUp(self):
        self.organiser = make_user(7111)
        self.helper = make_user(7112)
        self.stranger = make_user(7113)
        self.t = make_tournament(self.organiser)

    def test_permissions_of_each_kind_of_viewer(self):
        everything = frozenset(access.PERMISSIONS)
        self.assertEqual(access.permissions_of(self.organiser, self.t), everything)
        self.assertEqual(access.permissions_of(self.stranger, self.t), frozenset())
        self.assertEqual(access.permissions_of(None, self.t), frozenset())
        TournamentStaff.objects.create(tournament=self.t, user=self.helper,
                                       permissions=['standings', 'caster'])
        self.assertEqual(access.permissions_of(self.helper, self.t),
                         frozenset({'standings', 'caster'}))
        self.assertTrue(access.may(self.helper, self.t, 'caster'))
        self.assertFalse(access.may(self.helper, self.t, 'report'))
        self.assertFalse(access.may(self.helper, self.t, 'manage'))
        self.assertTrue(access.may(self.organiser, self.t, 'manage'))

    def test_an_admin_holds_everything(self):
        admin = two_factor(make_user(7114, staff=True))
        admin.admin_role = 'tournament_admin'
        admin.save(update_fields=['admin_role'])
        self.assertEqual(access.permissions_of(admin, self.t), frozenset(access.PERMISSIONS))
        self.assertEqual(access.role_of(admin, self.t), 'admin')

    def test_a_stored_name_nobody_knows_grants_nothing(self):
        TournamentStaff.objects.create(tournament=self.t, user=self.helper,
                                       permissions=['report', 'owner'])
        self.assertEqual(access.permissions_of(self.helper, self.t), frozenset({'report'}))

    def test_access_payload_lists_permissions(self):
        TournamentStaff.objects.create(tournament=self.t, user=self.helper,
                                       permissions=['caster', 'structure'])
        res = client_for(self.helper).get('/tournament/%s/access/' % self.t.slug)
        self.assertEqual(res.json()['data'], {
            'role': 'staff', 'can_manage': False, 'can_record_results': False,
            'is_entrant': False, 'permissions': ['structure', 'caster']})
        res = client_for(self.organiser).get('/tournament/%s/access/' % self.t.slug)
        self.assertEqual(res.json()['data']['permissions'], list(access.PERMISSIONS))
        res = self.client.get('/tournament/%s/access/' % self.t.slug)
        self.assertEqual(res.json()['data']['permissions'], [])

    def test_a_report_holder_records_results(self):
        TournamentStaff.objects.create(tournament=self.t, user=self.helper, permissions=['report'])
        self.assertTrue(access.may_record_results(self.helper, self.t))
        self.assertFalse(access.may_manage(self.helper, self.t))


class StaffApiTests(TestCase):
    def setUp(self):
        self.organiser = make_user(7121)
        self.helper = make_user(7122)
        self.other = make_user(7123)
        self.t = make_tournament(self.organiser)
        self.base = '/tournament/%s/staff/' % self.t.slug

    def name(self, body, who=None):
        return client_for(who or self.organiser).post(self.base, body, format='json')

    def patch(self, user, body, who=None):
        return client_for(who or self.organiser).patch(
            '%s%s/' % (self.base, user.user_id), body, format='json')

    def test_named_with_permissions(self):
        res = self.name({'username': self.helper.username, 'permissions': ['caster', 'place']})
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = res.json()['data']['staff'][0]
        self.assertEqual(row['username'], self.helper.username)
        self.assertEqual(row['permissions'], ['place', 'caster'])
        self.assertEqual(TournamentStaff.objects.get(tournament=self.t, user=self.helper).permissions,
                         ['place', 'caster'])

    def test_absent_permissions_means_report(self):
        # What every screen before P7 sent: a username and nothing else.
        res = self.name({'username': self.helper.username})
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['staff'][0]['permissions'], ['report'])

    def test_unknown_permission_is_refused(self):
        res = self.name({'username': self.helper.username, 'permissions': ['report', 'owner']})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'UNKNOWN_PERMISSION')
        self.assertFalse(TournamentStaff.objects.filter(tournament=self.t).exists())

    def test_an_empty_list_is_refused(self):
        res = self.name({'username': self.helper.username, 'permissions': []})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'PERMISSIONS_REQUIRED')
        self.name({'username': self.helper.username})
        res = self.patch(self.helper, {'permissions': []})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'PERMISSIONS_REQUIRED')
        self.assertEqual(TournamentStaff.objects.get(tournament=self.t, user=self.helper).permissions,
                         ['report'])

    def test_patch_changes_what_they_hold(self):
        self.name({'username': self.helper.username})
        res = self.patch(self.helper, {'permissions': ['standings', 'structure']})
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['staff'][0]['permissions'], ['structure', 'standings'])
        self.assertEqual(access.permissions_of(self.helper, self.t),
                         frozenset({'structure', 'standings'}))

    def test_patch_for_somebody_not_staff_is_404(self):
        res = self.patch(self.other, {'permissions': ['report']})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'STAFF_NOT_FOUND')

    def test_a_helper_cannot_patch_anybody(self):
        TournamentStaff.objects.create(tournament=self.t, user=self.helper,
                                       permissions=list(access.PERMISSIONS))
        TournamentStaff.objects.create(tournament=self.t, user=self.other, permissions=['report'])
        res = self.patch(self.other, {'permissions': access.PERMISSIONS}, who=self.helper)
        self.assertEqual(res.status_code, 403)
        res = self.client.patch('%s%s/' % (self.base, self.other.user_id),
                                {'permissions': ['report']}, content_type='application/json')
        self.assertEqual(res.status_code, 401)
        self.assertEqual(TournamentStaff.objects.get(tournament=self.t, user=self.other).permissions,
                         ['report'])

    def test_invite_by_email_carries_permissions(self):
        res = self.name({'username': 'caster@example.com', 'permissions': ['caster']})
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(res.json()['data']['awaiting_signup'])
        self.assertEqual(res.json()['data']['invite']['permissions'], ['caster'])
        invite = RoleInvite.objects.get(tournament=self.t)
        self.assertEqual((invite.role, invite.scopes), ('staff', ['caster']))
        listing = client_for(self.organiser).get(self.base).json()['data']
        self.assertEqual(listing['invites'][0]['permissions'], ['caster'])


#: Every door a helper may be given, by the permission it asks for. An id that
#: names nothing (999999) answers 404 to somebody allowed and 403 to somebody
#: who is not, because who you are is asked before what you named (R88).
DOORS = {
    'structure': [
        ('put', 'stages/set/', {'stages': []}),
        ('post', 'stages/adopt/', {}),
        ('post', 'stages/999999/reformat/', {'format': 'single_elimination'}),
        ('post', 'matches/999999/rules/', {}),
        ('post', 'br/ensure/', {}),
        ('put', 'br/999999/settings/', {}),
        ('patch', 'br/999999/lobbies/999999/', {'name': 'A'}),
        ('post', 'br/999999/lobbies/999999/matches/', {}),
        ('delete', 'br/matches/999999/', None),
    ],
    'place': [
        ('post', 'stages/999999/draw/', {}),
        ('get', 'stages/999999/advance/preview/', None),
        ('post', 'stages/999999/advance/', {}),
        ('post', 'stages/999999/lines/999999/pull/', {}),
        ('post', 'stages/999999/undraw/', {}),
        ('post', 'stages/999999/reopen/', {}),
        ('post', 'bracket/swap/', {}),
        ('post', 'bracket/replace/', {}),
        ('get', 'bracket/edits/', None),
        ('post', 'generate-bracket/', {}),
        ('post', 'br/999999/seats/move/', {}),
        ('post', 'br/999999/finish/', {}),
    ],
    'report': [
        ('post', '/tournament/update-bracket/{ref}/', {}),
        ('post', 'matches/999999/reopen/', {}),
        ('patch', 'br/matches/999999/', {'room_code': 'ROOM1'}),
    ],
    'standings': [
        ('post', 'league-adjustment/', {}),
        ('post', 'stat-settings/', {}),
        ('post', 'league-rules/', {}),
        ('put', 'metrics/', {}),
        # A match's stats are public to read, so which matches exist is no
        # secret: this door is knocked on with a real one.
        ('post', 'matches/{match}/stats/', {}),
        ('post', 'mvp/award/', {}),
    ],
    'caster': [
        ('post', 'studio/sessions/', {'name': 'Matrix'}),
        ('get', 'overlays/', None),
        ('get', 'studio/assets/', None),
    ],
}


class DoorMatrixTests(TestCase):
    def setUp(self):
        from .models import BracketMatch
        from .tests import register
        self.organiser = make_user(7131)
        self.t = make_tournament(self.organiser)
        self.match = BracketMatch.objects.create(
            tournament=self.t, round_number=1, match_number=1,
            participant_1=register(self.t, make_user(7181)),
            participant_2=register(self.t, make_user(7182)))
        self._next = 7140

    def helper(self, permissions):
        self._next += 1
        user = make_user(self._next)
        TournamentStaff.objects.create(tournament=self.t, user=user, permissions=list(permissions))
        return user

    def knock(self, user, method, path, body):
        path = path.format(ref=self.t.slug, match=self.match.pk)
        url = path if path.startswith('/') else '/tournament/%s/%s' % (self.t.slug, path)
        client = client_for(user)
        if method == 'get':
            return client.get(url)
        return getattr(client, method)(url, body, format='json')

    def test_each_permission_opens_its_doors_and_only_those(self):
        for permission, doors in DOORS.items():
            holder = self.helper([permission])
            without = self.helper([p for p in access.PERMISSIONS if p != permission])
            for method, path, body in doors:
                res = self.knock(holder, method, path, body)
                self.assertNotEqual(res.status_code, 403, '%s holder refused at %s %s: %s'
                                    % (permission, method, path, res.content[:200]))
                self.assertNotEqual(res.status_code, 401, (permission, path))
                res = self.knock(without, method, path, body)
                self.assertEqual(res.status_code, 403, '%s without it let through %s %s (%s)'
                                 % (permission, method, path, res.status_code))

    def test_caster_reaches_the_studio_only(self):
        caster = self.helper(['caster'])
        res = self.knock(caster, 'post', 'studio/sessions/', {'name': 'Caster desk'})
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        for permission in ('structure', 'place', 'report', 'standings'):
            method, path, body = DOORS[permission][0]
            self.assertEqual(self.knock(caster, method, path, body).status_code, 403, path)

    def test_settings_money_and_staff_stay_the_organisers(self):
        everything = self.helper(access.PERMISSIONS)
        for method, path, body in (
                ('post', 'staff/', {'username': 'player7131'}),
                ('get', 'earnings/', None),
                ('post', 'cancel/', {}),
                ('put', '/tournament/edit-tournament/{ref}/', {'tournament_title': 'Mine now'})):
            res = self.knock(everything, method, path, body)
            self.assertIn(res.status_code, (403, 404), (path, res.status_code, res.content[:200]))

    def test_stage_payload_lists_permissions(self):
        placer = self.helper(['place'])
        data = client_for(placer).get('/tournament/%s/stages/' % self.t.slug).json()['data']
        self.assertEqual(data['permissions'], ['place'])
        self.assertFalse(data['can_manage'])

    def test_battle_royale_payload_lists_permissions(self):
        shaper = self.helper(['structure'])
        data = client_for(shaper).get('/tournament/%s/br/' % self.t.slug).json()['data']
        self.assertEqual(data['permissions'], ['structure'])

    def test_match_payload_lists_permissions(self):
        reporter = self.helper(['report'])
        data = client_for(reporter).get('/tournament/match/%s/' % self.match.pk).json()['data']
        self.assertEqual(data['permissions'], ['report'])
