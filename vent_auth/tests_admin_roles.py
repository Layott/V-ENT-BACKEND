"""The seven roles the admin spec asks for, and the two lists that must agree.

CEO, 7 September 2026, from the admin dashboard spec: Super Admin, Admin,
Marketplace Manager, Wager Manager, Moderator, Tournament Organizer, Financial
Manager.

## The fault this file exists to stop

`Users.ADMIN_ROLE_CHOICES` and `decorators.ADMIN_ROLES` are two lists of the
same thing. Adding the three new roles changed the model and not the other, and
`view_dashboard` is `set(ADMIN_ROLES)` - so the new roles could not open the
console at all. The lists cannot be derived from each other, because
`decorators` is imported before the app registry is ready, so a test is what
keeps them in step.
"""
from django.test import TestCase

from .decorators import (ADMIN_ROLES, ROLE_LABEL, ROLE_PERMISSIONS,
                         ROLE_SHORT, ROLES_AWAITING_THEIR_FEATURE)
from .models import Users


class TheTwoListsAgreeTests(TestCase):
    def test_every_role_on_the_model_is_known_to_the_permission_table(self):
        on_model = {code for code, _label in Users.ADMIN_ROLE_CHOICES}
        self.assertEqual(on_model, set(ADMIN_ROLES))

    def test_every_role_has_a_short_name_and_a_label(self):
        """The frontend reads both; a role missing from either renders blank."""
        for role in ADMIN_ROLES:
            self.assertIn(role, ROLE_SHORT, role)
            self.assertIn(role, ROLE_LABEL, role)

    def test_no_permission_names_a_role_that_does_not_exist(self):
        """A permission granted to a typo is a permission granted to nobody,
        and it reads as though somebody has it."""
        for action, roles in ROLE_PERMISSIONS.items():
            for role in roles:
                self.assertIn(role, ADMIN_ROLES, '%s: %s' % (action, role))


class WhatEachRoleMayDoTests(TestCase):
    def test_the_spec_seven_all_exist(self):
        for role in ('super_admin', 'admin', 'finance_admin', 'mod_admin',
                     'tournament_admin', 'marketplace_admin', 'wager_admin'):
            self.assertIn(role, ADMIN_ROLES, role)

    def test_only_a_super_admin_manages_other_admins(self):
        """The whole difference the spec draws between Admin and Super Admin."""
        self.assertEqual(ROLE_PERMISSIONS['manage_admins'], {'super_admin'})
        self.assertEqual(ROLE_PERMISSIONS['set_user_roles'], {'super_admin'})
        self.assertEqual(ROLE_PERMISSIONS['delete_users'], {'super_admin'})

    def test_a_tournament_organizer_runs_tournaments_and_not_money(self):
        self.assertIn('tournament_admin', ROLE_PERMISSIONS['manage_tournaments'])
        self.assertIn('tournament_admin', ROLE_PERMISSIONS['manage_events'])
        self.assertNotIn('tournament_admin', ROLE_PERMISSIONS['approve_payouts'])
        self.assertNotIn('tournament_admin', ROLE_PERMISSIONS['transfer_funds'])

    def test_a_financial_manager_moves_money_and_does_not_ban_people(self):
        self.assertIn('finance_admin', ROLE_PERMISSIONS['transfer_funds'])
        self.assertIn('finance_admin', ROLE_PERMISSIONS['approve_payouts'])
        self.assertNotIn('finance_admin', ROLE_PERMISSIONS['ban_users'])

    def test_a_moderator_moderates_and_does_not_touch_money(self):
        self.assertIn('mod_admin', ROLE_PERMISSIONS['moderate_content'])
        self.assertIn('mod_admin', ROLE_PERMISSIONS['ban_users'])
        self.assertNotIn('mod_admin', ROLE_PERMISSIONS['transfer_funds'])

    def test_an_admin_does_everything_except_the_super_admin_things(self):
        for action in ('manage_organizations', 'manage_tournaments',
                       'manage_events', 'ban_users', 'view_transactions'):
            self.assertIn('admin', ROLE_PERMISSIONS[action], action)
        for action in ('manage_admins', 'set_user_roles', 'delete_users',
                       'export_audit_log'):
            self.assertNotIn('admin', ROLE_PERMISSIONS[action], action)


class RolesWaitingForTheirFeatureTests(TestCase):
    def test_marketplace_and_wager_grant_nothing_of_substance(self):
        """Neither feature is built. A role that can be assigned and grants
        nothing is honest; a console section for a feature that does not exist
        is not."""
        for role in ROLES_AWAITING_THEIR_FEATURE:
            granted = {action for action, roles in ROLE_PERMISSIONS.items()
                       if role in roles}
            # The console door and nothing else.
            self.assertLessEqual(granted, {'view_dashboard'},
                                 '%s grants %s' % (role, sorted(granted)))

    def test_they_do_not_see_everybody_accounts(self):
        for role in ROLES_AWAITING_THEIR_FEATURE:
            self.assertNotIn(role, ROLE_PERMISSIONS['view_users'], role)

    def test_the_permissions_they_are_waiting_for_exist_and_are_empty(self):
        """Named so the console can ASK about them. They gain their managers
        on the day the feature does, and nothing else changes."""
        self.assertEqual(ROLE_PERMISSIONS['manage_marketplace'], set())
        self.assertEqual(ROLE_PERMISSIONS['manage_wagers'], set())
        self.assertEqual(ROLE_PERMISSIONS['manage_shop'], set())
