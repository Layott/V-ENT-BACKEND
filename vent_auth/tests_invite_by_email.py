"""Inviting somebody who is not on V-ENT yet.

CEO, 7 September 2026: "For anything about invites on the platform, you should
be able to type in peoples emails and it shows users or just even people who
dont have accounts and they receive invites to the website and to the org."

`OrgInvite.user` was a required foreign key, and its own docstring argued the
case for that: "an invite to an email address nobody has claimed is a signup
funnel rather than a membership". The CEO's answer is that the signup funnel is
the point. An organiser knows the caterer's email, not their handle.

The half that is easy to build and easy to get wrong is the SECOND half: an
invitation sent to an address, sitting in a table, while the person it was for
signs up and sees nothing. Nobody involved can tell that has happened - the
organiser sent it, the person made an account, and the two never meet. Most of
this file is about that.
"""
import uuid

from django.test import TestCase
from django.utils import timezone as tz
from rest_framework.test import APIClient

from .invites import claim_pending, invitee_for, normalise_email
from .models import OrgInvite, OrgMember, Organization, Users


def a_user(name='inv', email=None):
    u = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email=email or '%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16], is_active=True)
    u.login_session_created_at = tz.now()
    u.save()
    return u


def auth(u):
    return {'HTTP_AUTHORIZATION': 'Bearer %s' % u.login_session_token}


class ResolvingWhoWasTypedTests(TestCase):
    def test_an_email_belonging_to_an_account_finds_the_account(self):
        person = a_user('found', email='Found.Person@Example.com')
        user, email, problem = invitee_for('found.person@example.com')
        self.assertIsNone(problem)
        self.assertEqual(user, person)
        self.assertEqual(email, 'found.person@example.com')

    def test_an_email_belonging_to_nobody_is_still_usable(self):
        """The whole point. Not an error, an invitation to somebody new."""
        user, email, problem = invitee_for('nobody@example.com')
        self.assertIsNone(problem)
        self.assertIsNone(user)
        self.assertEqual(email, 'nobody@example.com')

    def test_a_username_still_works_because_the_forms_take_one(self):
        person = a_user('handle')
        user, _, problem = invitee_for('@%s' % person.username)
        self.assertIsNone(problem)
        self.assertEqual(user, person)

    def test_something_that_is_neither_is_refused_by_name(self):
        _, _, problem = invitee_for('not an email or a handle')
        self.assertIsNotNone(problem)

    def test_nothing_typed_is_refused(self):
        self.assertIsNotNone(invitee_for('')[2])

    def test_case_and_spacing_do_not_make_a_different_address(self):
        self.assertEqual(normalise_email('  Mixed.Case@Example.COM '),
                         'mixed.case@example.com')


class InvitingByEmailTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = a_user('owner')
        self.org = Organization.objects.create(
            org_name='Cade %s' % uuid.uuid4().hex[:5],
            org_creator=self.owner, org_owner=self.owner)
        OrgMember.objects.get_or_create(
            org=self.org, user=self.owner,
            defaults={'role': OrgMember.ROLE_OWNER})

    def invite(self, who, role='member'):
        return self.client.post(
            '/organization/%s/invite/' % self.org.slug,
            {'username': who, 'role': role}, format='json', **auth(self.owner))

    def test_somebody_with_no_account_can_be_invited(self):
        res = self.invite('newcomer@example.com')
        self.assertEqual(res.status_code, 200, res.content[:300])
        invite = OrgInvite.objects.get()
        self.assertIsNone(invite.user)
        self.assertEqual(invite.email, 'newcomer@example.com')
        self.assertEqual(invite.status, OrgInvite.STATUS_PENDING)

    def test_an_email_that_matches_an_account_invites_the_account(self):
        person = a_user('known', email='known@example.com')
        self.invite('known@example.com')
        invite = OrgInvite.objects.get()
        self.assertEqual(invite.user, person)
        self.assertEqual(invite.email, 'known@example.com')

    def test_inviting_the_same_address_twice_updates_rather_than_duplicates(self):
        """Re-inviting is how a role is corrected before they answer, not a
        second invitation they then have to choose between."""
        self.invite('twice@example.com', role='member')
        self.invite('twice@example.com', role='manager')
        self.assertEqual(OrgInvite.objects.count(), 1)
        self.assertEqual(OrgInvite.objects.get().role, 'manager')

    def test_an_existing_member_is_refused(self):
        member = a_user('already', email='already@example.com')
        OrgMember.objects.create(org=self.org, user=member, role='member')
        res = self.invite('already@example.com')
        self.assertEqual(res.json()['code'], 'ALREADY_MEMBER')

    def test_nonsense_is_refused_rather_than_stored(self):
        res = self.invite('this is not an address')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(OrgInvite.objects.count(), 0)


class ClaimingOnSignupTests(TestCase):
    """The half nobody notices is missing.

    An invitation addressed to an address, accepted by nobody, sitting in a
    table while the person it was for signs up and sees nothing. The organiser
    sent it; the person made an account; the two never meet.
    """

    def setUp(self):
        self.owner = a_user('owner')
        self.org = Organization.objects.create(
            org_name='Cade %s' % uuid.uuid4().hex[:5],
            org_creator=self.owner, org_owner=self.owner)
        self.invite = OrgInvite.objects.create(
            org=self.org, user=None, email='later@example.com',
            invited_by=self.owner, role='member')

    def test_signing_up_with_that_address_attaches_the_invitation(self):
        newcomer = a_user('later', email='later@example.com')
        self.assertEqual(claim_pending(newcomer), 1)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.user, newcomer)

    def test_a_different_address_claims_nothing(self):
        someone = a_user('other', email='different@example.com')
        self.assertEqual(claim_pending(someone), 0)
        self.invite.refresh_from_db()
        self.assertIsNone(self.invite.user)

    def test_the_match_ignores_case(self):
        newcomer = a_user('shouty', email='LATER@EXAMPLE.COM')
        self.assertEqual(claim_pending(newcomer), 1)

    def test_an_account_with_no_email_claims_nothing_and_does_not_raise(self):
        nameless = a_user('nomail')
        nameless.email = ''
        nameless.save(update_fields=['email'])
        self.assertEqual(claim_pending(nameless), 0)

    def test_claiming_twice_does_not_double_count(self):
        newcomer = a_user('twice', email='later@example.com')
        claim_pending(newcomer)
        self.assertEqual(claim_pending(newcomer), 0)

    def test_a_vendor_invitation_becomes_a_real_stall(self):
        """The invitation was offering a stall, so claiming it produces one -
        the same object a bought pitch produces, so everything downstream
        cannot tell which way somebody came in."""
        from vent_auth.models import Games
        from vent_event.models import Event, Vendor, VendorInvite

        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        event = Event.objects.create(
            name='Invite Fest %s' % uuid.uuid4().hex[:4], game=game,
            creator=self.owner, event_type='physical', desc='x', entry_fee=0,
            reg_start_date=tz.now(),
            reg_end_date=tz.now() + tz.timedelta(days=2),
            start_date=tz.now() + tz.timedelta(days=8),
            end_date=tz.now() + tz.timedelta(days=8, hours=5))
        VendorInvite.objects.create(event=event, name='Mama T Kitchen',
                                    email='cook@example.com', booth='A4')

        cook = a_user('cook', email='cook@example.com')
        claim_pending(cook)

        stall = Vendor.objects.get(event=event, owner=cook)
        self.assertEqual(stall.name, 'Mama T Kitchen')
        self.assertEqual(stall.booth, 'A4')
        # Invited by the organiser, so there is nobody left to approve it.
        self.assertEqual(stall.status, 'approved')

    def test_claiming_a_vendor_invitation_twice_makes_one_stall(self):
        from vent_auth.models import Games
        from vent_event.models import Event, Vendor, VendorInvite

        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        event = Event.objects.create(
            name='Invite Fest %s' % uuid.uuid4().hex[:4], game=game,
            creator=self.owner, event_type='physical', desc='x', entry_fee=0,
            reg_start_date=tz.now(),
            reg_end_date=tz.now() + tz.timedelta(days=2),
            start_date=tz.now() + tz.timedelta(days=8),
            end_date=tz.now() + tz.timedelta(days=8, hours=5))
        VendorInvite.objects.create(event=event, name='Twice',
                                    email='twice@example.com')
        cook = a_user('cook2', email='twice@example.com')
        claim_pending(cook)
        claim_pending(cook)
        self.assertEqual(Vendor.objects.filter(event=event, owner=cook).count(), 1)
