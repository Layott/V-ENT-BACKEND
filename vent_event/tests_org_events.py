# -*- coding: utf-8 -*-
"""An organisation's own people run its events.

CEO, 4 September 2026, on the morning of a live show: "URGENTLY I NEED TO BBE
ABLE TO ADD PEOPLE TO MANAGE AN EVENT", then "but there is no way to add events
to an organization", then the decision:

    "dont ulock it, instead do a way to add events to an oganizatio and the whe
    ou add people to your organization you can then have them manage events ad
    they will see everyrthing"

So a personal event stays personal, and the route is: put the event in an
organisation, put people in the organisation, and they run its events.

The awkward part, and what most of this file is about: the same question was
answered in SIX places, each asking `EventManager` directly and each with its
own idea of which roles counted. Adding the organisation to five of them would
have left one screen refusing somebody every other screen had just admitted.
`vent_event/permissions.py` is the one rule now, and these tests walk every
surface that reads it rather than the helper alone: a helper that is right and
a screen that does not call it is the failure being prevented.
"""
import json
import uuid
from datetime import date, time, timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Organization, OrgMember, Users

from .models import Event, EventManager, Ticket, TicketTier
from .views_tickets import _new_code


def a_user(name):
    """The same shape tests_promos uses.

    `login_session_created_at` is the part worth copying rather than writing
    fresh: without it every request answers SESSION_TOKEN_EXPIRED, which reads
    as a permission fault and is not one.
    """
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        full_name=name.title(), is_active=True,
        login_session_token=uuid.uuid4().hex[:16])
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


class OrganisationRunsItsEventsTests(TestCase):

    def setUp(self):
        self.owner, self.owner_auth = a_user('orgowner')
        self.admin, self.admin_auth = a_user('orgadmin')
        self.events_manager, self.events_auth = a_user('eventsmgr')
        self.other_manager, self.other_auth = a_user('teamsmgr')
        self.member, self.member_auth = a_user('plainmember')
        self.stranger, self.stranger_auth = a_user('stranger')

        self.game = Games.objects.create(game_title='EA FC 26 ORG %s'
                                                    % uuid.uuid4().hex[:4])
        self.org = Organization.objects.create(
            org_name='Vermillion %s' % uuid.uuid4().hex[:5],
            org_creator=self.owner, org_owner=self.owner)

        OrgMember.objects.create(org=self.org, user=self.admin, role='admin')
        OrgMember.objects.create(org=self.org, user=self.events_manager,
                                 role='manager', scopes=['events'])
        OrgMember.objects.create(org=self.org, user=self.other_manager,
                                 role='manager', scopes=['tournaments'])
        OrgMember.objects.create(org=self.org, user=self.member, role='member')

        self.event = Event.objects.create(
            name='Org Event %s' % uuid.uuid4().hex[:4], game=self.game,
            creator=self.owner, organization=self.org, event_type='physical',
            desc='An event the organisation runs', entry_fee=0,
            reg_start_date=timezone.now(), reg_end_date=timezone.now() + timedelta(days=1),
            start_date=date.today() + timedelta(days=2),
            start_time=time(10, 0), end_time=time(18, 0))
        self.ref = self.event.slug or self.event.event_id

    # ------------------------------------------------------- the shared rule

    def test_the_rule_is_the_same_one_everywhere(self):
        """Every surface reads `permissions`, so they cannot disagree.

        Six screens each had their own copy of this question. This asserts the
        helper's answer, and the tests below assert that the screens actually
        call it.
        """
        from .permissions import may_run_event, may_work_the_door

        for who in (self.owner, self.admin, self.events_manager):
            self.assertTrue(may_run_event(who, self.event),
                            '%s should run this event' % who.username)
            self.assertTrue(may_work_the_door(who, self.event))

        for who in (self.other_manager, self.member, self.stranger):
            self.assertFalse(may_run_event(who, self.event),
                             '%s should NOT run this event' % who.username)

    def test_a_manager_of_another_area_does_not_inherit_the_event(self):
        """A tournaments manager must not get the door list.

        The scopes are the whole point of the manager role: an organisation
        with one person on tournaments and another on events is exactly what
        it is for.
        """
        res = self.client.get('/event/%s/managers/' % self.ref, **self.other_auth)
        self.assertEqual(res.status_code, 403, res.content)

    def test_a_plain_member_runs_nothing(self):
        res = self.client.get('/event/%s/managers/' % self.ref, **self.member_auth)
        self.assertEqual(res.status_code, 403, res.content)

    # ---------------------------------------------------------- the surfaces

    def test_the_events_manager_reaches_the_promos_screen(self):
        res = self.client.get('/event/%s/promos/' % self.ref, **self.events_auth)
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_events_manager_reaches_the_metrics(self):
        res = self.client.get('/event/%s/metrics/' % self.ref, **self.events_auth)
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_events_manager_can_be_given_management_of_one_event(self):
        """The org route and the per event route both still work."""
        helper, _ = a_user('doorstaff')
        res = self.client.post('/event/%s/managers/' % self.ref,
                               data=json.dumps({'username': helper.username,
                                                'role': 'door'}),
                               content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(EventManager.objects.filter(
            event=self.event, user=helper, role='door').exists())

    def test_the_events_manager_can_work_the_door(self):
        """The list the scanner downloads, and the check in itself."""
        tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=10)
        ticket = Ticket.objects.create(
            event=self.event, tier=tier, code=_new_code(),
            attendee_name='A Guest', attendee_email='guest@vent.test',
            status='valid')

        res = self.client.get('/event/%s/attendees/' % self.ref, **self.events_auth)
        self.assertEqual(res.status_code, 200, res.content)

        res = self.client.post('/event/ticket/%s/check-in/' % ticket.code,
                               data=json.dumps({}), content_type='application/json',
                               **self.events_auth)
        self.assertEqual(res.status_code, 200, res.content)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.checked_in_at)

    def test_a_stranger_still_cannot_work_the_door(self):
        tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=10)
        ticket = Ticket.objects.create(
            event=self.event, tier=tier, code=_new_code(),
            attendee_name='Another Guest', attendee_email='guest2@vent.test',
            status='valid')

        res = self.client.post('/event/ticket/%s/check-in/' % ticket.code,
                               data=json.dumps({}), content_type='application/json',
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 403, res.content)

    # ------------------------------------------------------ seeing the event

    def test_the_events_manager_sees_it_in_their_own_list(self):
        """An event somebody may run and cannot find is one they cannot run."""
        res = self.client.get('/event/my-events/', **self.events_auth)
        self.assertEqual(res.status_code, 200, res.content)
        ids = [row['id'] for row in res.json()['data']['results']]
        self.assertIn(self.event.event_id, ids)

    def test_a_manager_of_another_area_does_not_see_it(self):
        res = self.client.get('/event/my-events/', **self.other_auth)
        self.assertEqual(res.status_code, 200, res.content)
        ids = [row['id'] for row in res.json()['data']['results']]
        self.assertNotIn(self.event.event_id, ids)

    # ------------------------------------------- putting an event in the org

    def test_the_owner_can_move_their_event_into_the_organisation(self):
        """The step that was missing, and the reason the rest was a trap."""
        personal = Event.objects.create(
            name='Personal %s' % uuid.uuid4().hex[:4], game=self.game,
            creator=self.owner, event_type='physical', desc='Mine alone',
            entry_fee=0, reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timedelta(days=1),
            start_date=date.today() + timedelta(days=3),
            start_time=time(10, 0), end_time=time(18, 0))
        self.assertIsNone(personal.organization_id)

        res = self.client.put(
            '/event/edit-event/%s/' % (personal.slug or personal.event_id),
            data=json.dumps({'organization': self.org.org_id}),
            content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content)

        personal.refresh_from_db()
        self.assertEqual(personal.organization_id, self.org.org_id)

        # And now it can be shared, which is the whole point of moving it.
        helper, _ = a_user('newdoor')
        res = self.client.post(
            '/event/%s/managers/' % (personal.slug or personal.event_id),
            data=json.dumps({'username': helper.username, 'role': 'door'}),
            content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 201, res.content)

    def test_somebody_cannot_move_an_event_into_an_organisation_they_do_not_run(self):
        res = self.client.put(
            '/event/edit-event/%s/' % self.ref,
            data=json.dumps({'organization': self.org.org_id}),
            content_type='application/json', **self.stranger_auth)
        self.assertIn(res.status_code, (403, 404), res.content)
