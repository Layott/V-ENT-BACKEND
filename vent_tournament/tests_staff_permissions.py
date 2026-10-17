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
        migration = importlib.import_module('vent_tournament.migrations.0071_staff_permissions')
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
