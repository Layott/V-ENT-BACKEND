"""Inviting somebody with no account to help run an event, a tournament or an
organisation, and them accepting after they sign up (inbox 455).

CEO, 13 October 2026: "when trying to add someone to help you manage an even or
a tournament or organization, even if the person does not have an account they
should be able to get the mail in their email and then, they can create an
account using the link in their mail and still accept the invite."

Before: the event door emailed "join V-ENT" and the organiser had to add the
person again once they had an account; the tournament door refused an email;
the organisation email linked to a page that needs a sign in. Pinned here, all
three through one page: the email links to /invitations/<token> (never an
address in a URL), signing up attaches the invitation and says so, the
addressee alone accepts, and accepting writes the real role.
"""
import uuid
from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.utils import timezone as tz
from rest_framework.test import APIClient

from vent_event.models import Event, EventManager
from vent_tournament.models import Tournament, TournamentStaff

from .invites import claim_pending
from .models import (Games, Notification, OrgInvite, OrgMember, Organization, RoleInvite,
                     Users)
from .tests_invite_by_email import a_user, auth


class Base(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = a_user('ri_owner')
        self.game = Games.objects.create(game_title='RI %s' % uuid.uuid4().hex[:5])
        now = tz.now()
        self.event = Event.objects.create(
            name='Invite Fest', creator=self.owner, event_type='physical', desc='x',
            entry_fee=0, start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=6),
            reg_start_date=now - timedelta(days=1), reg_end_date=now + timedelta(days=4))
        self.tournament = Tournament.objects.create(
            tournament_title='Invite Cup', tournament_game=self.game,
            tournament_creator=self.owner, start_date_and_time=now + timedelta(days=5),
            end_date_and_time=now + timedelta(days=6), is_draft=False,
            tournament_access='individual', entry_fee='Free', entry_fee_price=0)

    def sign_up(self, email):
        """What signing up and verifying does: an account with this address,
        then every invitation addressed to it attached."""
        person = a_user('ri_new', email=email)
        claim_pending(person)
        return person

    def answer(self, token, person, accept=True):
        return self.client.post('/auth/invitations/%s/respond/' % token,
                                {'accept': accept}, format='json', **auth(person))


class EventManagerByEmailTests(Base):
    def invite(self, who='door.helper@example.com', role='door'):
        return self.client.post('/event/%s/managers/' % self.event.slug,
                                {'username': who, 'role': role}, format='json', **auth(self.owner))

    def test_the_email_carries_a_link_and_never_the_address(self):
        res = self.invite()
        self.assertEqual(res.status_code, 201, res.content[:300])
        invite = RoleInvite.objects.get()
        self.assertEqual((invite.kind, invite.role, invite.user), ('event', 'door', None))
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body + ''.join(a[0] for a in getattr(mail.outbox[0], 'alternatives', []))
        self.assertIn('/invitations/%s' % invite.token, body)
        self.assertNotIn('signup?email=', body)
        # The organiser sees who is still to arrive.
        res = self.client.get('/event/%s/managers/' % self.event.slug, **auth(self.owner))
        self.assertEqual([i['email'] for i in res.json()['data']['invites']], ['door.helper@example.com'])

    def test_signing_up_attaches_it_and_says_so_and_accepting_makes_them_staff(self):
        self.invite()
        token = RoleInvite.objects.get().token
        person = self.sign_up('Door.Helper@Example.com')
        invite = RoleInvite.objects.get()
        self.assertEqual(invite.user, person)
        self.assertTrue(Notification.objects.filter(user=person, link='/invitations/%s' % token).exists())
        res = self.answer(token, person)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(EventManager.objects.filter(event=self.event, user=person, role='door').exists())
        # Door staff are refused by the console, so "Open" goes to the scanner.
        self.assertEqual(res.json()['data']['invitation']['path'],
                         '/events/scan?event=%s' % self.event.slug)
        self.assertEqual(self.answer(token, person).json()['code'], 'ALREADY_ANSWERED')

    def test_only_the_person_it_was_sent_to_can_accept(self):
        self.invite()
        token = RoleInvite.objects.get().token
        self.sign_up('door.helper@example.com')
        stranger = a_user('ri_stranger')
        res = self.answer(token, stranger)
        self.assertEqual((res.status_code, res.json()['code']), (403, 'INVITE_FOR_ANOTHER_ACCOUNT'))
        self.assertFalse(EventManager.objects.filter(user=stranger).exists())

    def test_the_page_shows_what_it_is_without_the_address(self):
        self.invite()
        token = RoleInvite.objects.get().token
        res = self.client.get('/auth/invitations/%s/' % token)
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']['invitation']
        self.assertEqual((data['kind'], data['name'], data['role'], data['invited_by']),
                         ('event', 'Invite Fest', 'door', self.owner.username))
        self.assertEqual(data['email_hint'], 'd***@example.com')
        self.assertNotIn('door.helper', str(res.content))

    def test_the_organiser_withdraws_it_and_a_stranger_cannot(self):
        self.invite()
        token = RoleInvite.objects.get().token
        stranger = a_user('ri_x')
        res = self.client.post('/auth/invitations/%s/cancel/' % token, {}, format='json', **auth(stranger))
        self.assertEqual(res.status_code, 404)
        res = self.client.post('/auth/invitations/%s/cancel/' % token, {}, format='json', **auth(self.owner))
        self.assertEqual(res.status_code, 200)
        person = self.sign_up('door.helper@example.com')
        self.assertEqual(self.answer(token, person).json()['code'], 'ALREADY_ANSWERED')

    def test_an_account_already_here_is_still_added_at_once(self):
        person = a_user('ri_here', email='here@example.com')
        res = self.invite('here@example.com', role='manager')
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        self.assertTrue(EventManager.objects.filter(event=self.event, user=person).exists())
        self.assertFalse(RoleInvite.objects.exists())


class ScorekeeperByEmailTests(Base):
    def test_a_tournament_takes_an_email_now(self):
        res = self.client.post('/tournament/%s/staff/' % self.tournament.slug,
                               {'username': 'scorer@example.com'}, format='json', **auth(self.owner))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(res.json()['data']['awaiting_signup'])
        token = RoleInvite.objects.get(kind='tournament').token
        person = self.sign_up('scorer@example.com')
        self.assertEqual(self.answer(token, person).status_code, 200)
        self.assertTrue(TournamentStaff.objects.filter(tournament=self.tournament, user=person).exists())


class OrganisationOnTheSamePageTests(Base):
    def test_the_organisation_email_opens_the_same_page_and_accepts_there(self):
        org = Organization.objects.create(org_name='Cade %s' % uuid.uuid4().hex[:5],
                                          org_creator=self.owner, org_owner=self.owner)
        OrgMember.objects.get_or_create(org=org, user=self.owner, defaults={'role': OrgMember.ROLE_OWNER})
        res = self.client.post('/organization/%s/invite/' % org.slug,
                               {'username': 'crew@example.com', 'role': 'member'},
                               format='json', **auth(self.owner))
        self.assertEqual(res.status_code, 200, res.content[:300])
        invite = OrgInvite.objects.get()
        self.assertIn('/invitations/%s' % invite.token, mail.outbox[-1].body
                      + ''.join(a[0] for a in getattr(mail.outbox[-1], 'alternatives', [])))
        person = self.sign_up('crew@example.com')
        mine = self.client.get('/auth/invitations/mine/', **auth(person)).json()['data']
        self.assertEqual([i['kind'] for i in mine['invitations']], ['organization'])
        self.assertEqual(self.answer(invite.token, person).status_code, 200)
        self.assertTrue(OrgMember.objects.filter(org=org, user=person).exists())

    def test_a_signed_in_account_with_the_address_can_claim_at_the_page(self):
        """Signed up with Google, say, so the claim at verification never ran."""
        self.client.post('/event/%s/managers/' % self.event.slug,
                         {'username': 'late@example.com'}, format='json', **auth(self.owner))
        token = RoleInvite.objects.get().token
        person = a_user('ri_late', email='late@example.com')     # no claim_pending
        self.assertEqual(self.answer(token, person).status_code, 200)
        self.assertTrue(EventManager.objects.filter(event=self.event, user=person).exists())


class NobodySignedOutAnswersTests(Base):
    def test_answering_and_listing_need_an_account(self):
        self.client.post('/event/%s/managers/' % self.event.slug,
                         {'username': 'x@example.com'}, format='json', **auth(self.owner))
        token = RoleInvite.objects.get().token
        anon = APIClient()
        self.assertEqual(anon.post('/auth/invitations/%s/respond/' % token, {'accept': True},
                                   format='json').status_code, 401)
        self.assertEqual(anon.get('/auth/invitations/mine/').status_code, 401)
        self.assertEqual(anon.get('/auth/invitations/%s/' % token).status_code, 200)
        self.assertEqual(anon.get('/auth/invitations/ri_nothing/').status_code, 404)
