"""Granting premium from the console.

`is_premium` shipped on 9 September, eight features read it, and nothing in the
interface wrote it. So every one of those features was live in a state where the
only thing anybody could see was the refusal.

What these tests hold, beyond "it sets the field":

  * the permission is its OWN, and the roles that may ban or manage do not get
    it by accident;
  * granting asks for the reason in the same press, because a note added later
    is a note nobody adds;
  * revoking clears the note, because last year's reason on an account that no
    longer has premium reads as though it still applies;
  * every change writes an `AdminAction` carrying what it was and what it
    became.
"""
import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AdminAction, Organization, Users, UserWallet


def make_user(i, *, role='user', admin_role=None, two_factor=True):
    user = Users.objects.create(
        username='pc%s' % i, email='pc%s@test.co' % i,
        login_session_token='pct%s' % str(i).zfill(11),
        login_session_created_at=timezone.now(),
        is_active=True,
        is_staff=admin_role is not None,
        role=role, admin_role=admin_role,
    )
    if admin_role and two_factor:
        # The console door asks for the second factor, and every admin
        # endpoint asks whether it was given. An admin without it is not an
        # admin as far as these views are concerned.
        user.login_session_2fa_at = timezone.now()
        user.save(update_fields=['login_session_2fa_at'])
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user,
                              wallet_balance=0)
    return user


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return client


class GrantToAUserTests(TestCase):

    def setUp(self):
        self.admin = make_user(1, role='admin', admin_role='super_admin')
        self.client = client_for(self.admin)
        self.member = make_user(2)
        self.url = '/auth/admin/users/%s/premium/' % self.member.user_id

    def _patch(self, **body):
        return self.client.patch(self.url, body, format='json')

    def test_granting_turns_it_on_and_keeps_the_reason(self):
        res = self._patch(premium=True, note='Granted for the Rivalry season')
        self.assertEqual(res.status_code, 200, res.content)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_premium)
        self.assertEqual(self.member.premium_note, 'Granted for the Rivalry season')

    def test_the_reason_is_asked_for_in_the_same_press(self):
        res = self._patch(premium=True)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'NOTE_REQUIRED')
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_premium)

    def test_saying_nothing_at_all_is_refused_rather_than_read_as_false(self):
        """An absent field must never be taken as 'turn it off'."""
        self.member.is_premium = True
        self.member.save(update_fields=['is_premium'])
        res = self._patch(note='something')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PREMIUM_TRUE_FALSE_REQUIRED')
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_premium)

    def test_revoking_clears_the_note(self):
        self._patch(premium=True, note='For the season')
        res = self._patch(premium=False)
        self.assertEqual(res.status_code, 200, res.content)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_premium)
        self.assertEqual(self.member.premium_note, '')

    def test_every_change_is_written_down(self):
        self._patch(premium=True, note='For the season')
        row = AdminAction.objects.get(action_type='grant_premium')
        self.assertEqual(row.admin_id, self.admin.user_id)
        self.assertEqual(row.target_model, 'User')
        self.assertEqual(row.target_id, str(self.member.user_id))
        self.assertEqual(row.metadata['was'], False)
        self.assertEqual(row.metadata['now'], True)
        self.assertEqual(row.metadata['name'], self.member.username)
        self.assertIn('season', row.reason)

        self._patch(premium=False)
        back = AdminAction.objects.get(action_type='revoke_premium')
        self.assertEqual(back.metadata['was'], True)
        self.assertEqual(back.metadata['now'], False)
        # And what the reason USED to be, because that is the thing somebody
        # asks about afterwards.
        self.assertEqual(back.metadata['was_note'], 'For the season')

    def test_it_is_the_same_field_the_features_read(self):
        """The point of the whole row: a refusal becomes an answer."""
        from . import premium

        self.assertFalse(premium.has_premium(self.member))
        self._patch(premium=True, note='For the season')
        self.member.refresh_from_db()
        self.assertTrue(premium.has_premium(self.member))


class WhoMayGrantTests(TestCase):

    def setUp(self):
        self.member = make_user(10)
        self.url = '/auth/admin/users/%s/premium/' % self.member.user_id

    def _as(self, admin_role, **extra):
        admin = make_user(abs(hash(admin_role)) % 900 + 20, role='admin',
                          admin_role=admin_role, **extra)
        return client_for(admin).patch(
            self.url, {'premium': True, 'note': 'x'}, format='json')

    def test_a_super_admin_may(self):
        self.assertEqual(self._as('super_admin').status_code, 200)

    def test_the_financial_manager_may(self):
        """Giving something away for free sits with the money role."""
        self.assertEqual(self._as('finance_admin').status_code, 200)

    def test_a_moderator_may_not(self):
        """Somebody who may ban an abusive account has no claim on this."""
        self.assertEqual(self._as('mod_admin').status_code, 403)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_premium)

    def test_an_ordinary_admin_may_not(self):
        self.assertEqual(self._as('admin').status_code, 403)

    def test_support_may_not(self):
        self.assertEqual(self._as('support_admin').status_code, 403)

    def test_an_admin_who_never_met_the_authenticator_may_not(self):
        """The console door asks for the second factor wherever it is used."""
        self.assertEqual(
            self._as('super_admin', two_factor=False).status_code, 403)

    def test_somebody_who_is_not_an_admin_at_all_may_not(self):
        plain = make_user(90)
        res = client_for(plain).patch(
            self.url, {'premium': True, 'note': 'x'}, format='json')
        self.assertIn(res.status_code, (401, 403))


class ReadItBackTests(TestCase):

    def setUp(self):
        self.admin = make_user(30, role='admin', admin_role='super_admin')
        self.client = client_for(self.admin)

    def test_the_detail_carries_it_with_its_reason(self):
        member = make_user(31)
        member.is_premium = True
        member.premium_note = 'Granted for the Rivalry season'
        member.save(update_fields=['is_premium', 'premium_note'])

        res = self.client.get('/auth/admin/users/%s/' % member.user_id)
        self.assertEqual(res.status_code, 200)
        user = res.data['data']['user']
        self.assertTrue(user['is_premium'])
        self.assertEqual(user['premium_note'], 'Granted for the Rivalry season')

    def test_the_list_says_who_has_it(self):
        plain = make_user(32)
        paid = make_user(33)
        paid.is_premium = True
        paid.save(update_fields=['is_premium'])

        res = self.client.get('/auth/admin/users/')
        by_name = {row['username']: row for row in res.data['data']['results']}
        self.assertTrue(by_name[paid.username]['is_premium'])
        self.assertFalse(by_name[plain.username]['is_premium'])

    def test_the_list_can_be_narrowed_to_them(self):
        """"Who is on premium" has to be one question, not a scroll."""
        make_user(34)
        paid = make_user(35)
        paid.is_premium = True
        paid.save(update_fields=['is_premium'])

        res = self.client.get('/auth/admin/users/?status=premium')
        names = [row['username'] for row in res.data['data']['results']]
        self.assertEqual(names, [paid.username])


class OrganisationTests(TestCase):
    """The org half goes through the console's existing detail endpoint.

    That view takes an `action` on a POST rather than a PATCH, which is how
    verifying and renaming already work. Adding a fourth method to it for one
    action would have been a second shape for the same screen.
    """

    def setUp(self):
        self.admin = make_user(40, role='admin', admin_role='super_admin')
        self.client = client_for(self.admin)
        owner = make_user(41)
        self.org = Organization.objects.create(
            org_name='Vermillion Encore', org_creator=owner, org_owner=owner)
        self.url = '/auth/admin/organizations/%s/' % self.org.org_id

    def test_an_organisation_can_be_granted_it(self):
        res = self.client.post(self.url, {
            'action': 'set_premium', 'premium': True,
            'note': 'Partner for the season'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_premium)
        self.assertEqual(res.data['data']['premium_note'], 'Partner for the season')

    def test_it_carries_the_people_acting_for_it(self):
        """The reason `has_premium` prefers the organisation."""
        from . import premium
        from vent_tournament.models import Tournament

        self.client.post(self.url, {
            'action': 'set_premium', 'premium': True, 'note': 'Partner'},
            format='json')
        self.org.refresh_from_db()

        tournament = Tournament(tournament_organization=self.org,
                                tournament_creator=self.org.org_owner)
        self.assertTrue(premium.has_premium(tournament))

    def test_a_manager_who_may_verify_may_not_grant(self):
        """Administration and commerce are different decisions."""
        manager = make_user(45, role='admin', admin_role='admin')
        res = client_for(manager).post(self.url, {
            'action': 'set_premium', 'premium': True, 'note': 'x'},
            format='json')
        self.assertEqual(res.status_code, 403)
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_premium)

        # And the same person CAN still verify it, so the refusal is about this
        # action rather than about them.
        ok = client_for(manager).post(self.url, {
            'action': 'verify', 'verified': True}, format='json')
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_the_reason_is_asked_for_here_too(self):
        res = self.client.post(self.url, {
            'action': 'set_premium', 'premium': True}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'NOTE_REQUIRED')

    def test_it_is_written_down_against_the_organisation(self):
        self.client.post(self.url, {
            'action': 'set_premium', 'premium': True, 'note': 'Partner'},
            format='json')
        row = AdminAction.objects.get(action_type='grant_premium')
        self.assertEqual(row.target_model, 'Organization')
        self.assertEqual(row.target_id, str(self.org.org_id))
        self.assertEqual(row.metadata['name'], 'Vermillion Encore')
