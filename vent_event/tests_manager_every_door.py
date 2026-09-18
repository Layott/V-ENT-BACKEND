"""Every console door answers a manager the way it answers the creator.

`permissions.py` has said since 4 September 2026 that somebody who RUNS an
event (a named manager, or the organisation's events people) may do everything
except delete it. On 18 September `walk_manager` opened the console and the
Money tab answered 403: nine views still asked "is this the creator" in their
own words, and the Production tab's rule was a tenth. Each was the same
question answered the old way, and the class is what this walks.

It takes every route in `vent_event.urls` that carries an event reference and
calls it as the creator, a named manager, an organisation's events manager,
somebody on the door, and a signed-in stranger. It never asserts a particular
status (an empty body is refused for its own reasons); it asserts the SHAPE of
the answers:

- a manager is refused nowhere the creator is admitted, except the doors
  that are the creator's by design (delete, restore, handing out management);
- door staff reach the door and nothing else;
- a stranger reaches only what is public.

A passing zero has two meanings, so the route list is asserted non-empty and
the creator's admitted set is asserted to be most of the console.
"""
import re
import uuid

from django.test import TestCase
from django.urls import URLPattern
from django.utils import timezone

from vent_auth.models import Games, Organization, OrgMember, Users
from vent_event import urls as event_urls
from vent_event.models import Event, EventManager


def a_user(tag):
    user = Users.objects.create(
        username='%s_%s' % (tag, uuid.uuid4().hex[:5]),
        email='%s@vent.test' % uuid.uuid4().hex[:8], is_active=True,
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def routes_taking_an_event():
    out = []
    for entry in event_urls.urlpatterns:
        if not isinstance(entry, URLPattern):
            continue
        route = str(entry.pattern)
        if '<str:event_id>' in route:
            out.append(route)
    return out


def fill(route, ref):
    path = route.replace('<str:event_id>', ref)
    path = re.sub(r'<int:[^>]+>', '1', path)
    path = re.sub(r'<str:[^>]+>', 'x', path)
    path = re.sub(r'<slug:[^>]+>', 'x', path)
    return '/event/' + path


# The creator's alone, by design. A manager adding managers is how an event
# quietly acquires people nobody chose; deleting is the one thing the rule in
# permissions.py names as beyond RUN.
CREATOR_ONLY = {
    ('<str:event_id>/delete/', 'post'),
    ('<str:event_id>/restore/', 'post'),
    ('<str:event_id>/managers/', 'post'),
    ('<str:event_id>/managers/<int:manager_id>/', 'delete'),
}

# What somebody put on the door for the day may reach: the list, the numbers
# that go with it, and the metrics screen the door summary is drawn from.
DOOR_MAY = {
    '<str:event_id>/attendees/',
    '<str:event_id>/door-search/',
    '<str:event_id>/door-summary/',
    '<str:event_id>/door-lookups/',
    '<str:event_id>/metrics/',
    '<str:event_id>/metrics/export/',
    '<str:event_id>/managers/',           # reading who else is on the team
}

# Public to any signed-in person: the event page's own reads and the buyer's
# own actions. A stranger admitted anywhere else is a leak.
PUBLIC = {
    'view-event/<str:event_id>/',
    '<str:event_id>/slots/',
    '<str:event_id>/slots/<int:slot_id>/',
    '<str:event_id>/slots/<int:slot_id>/buy/',
    '<str:event_id>/vendors/',
    '<str:event_id>/vendors/create/',
    '<str:event_id>/vendor/<str:vendor_id>/',
    '<str:event_id>/ticket-types/',
    '<str:event_id>/quote/',
    '<str:event_id>/sessions/',
    '<str:event_id>/waitlist/',
    '<str:event_id>/waitlist/mine/',
    '<str:event_id>/overlay-feed/',
    '<str:event_id>/run-of-show/',
    '<str:event_id>/checkout-fields/',
    '<str:event_id>/guest-buy/',
    '<str:event_id>/buy-ticket/',
    '<str:event_id>/track/',
    '<str:event_id>/polls/',
    '<str:event_id>/polls/<int:poll_id>/vote/',
    '<str:event_id>/origins/',
    '<str:event_id>/ref/<str:code>/visit/',
    '<str:event_id>/tournaments/',
    '<str:event_id>/self-check-in/settings/',
    '<str:event_id>/managers/',           # answers 200 with can_add false
    '<str:event_id>/announcements/',      # what was sent is public information
}

ADMITTED = (200, 201, 204, 400, 404, 405, 409, 415, 422)


class ManagerEveryDoorTests(TestCase):
    def setUp(self):
        self.creator, self.creator_auth = a_user('md_creator')
        self.manager, self.manager_auth = a_user('md_manager')
        self.org_events, self.org_events_auth = a_user('md_orgevents')
        self.door, self.door_auth = a_user('md_door')
        self.stranger, self.stranger_auth = a_user('md_stranger')

        org_owner, _ = a_user('md_orgowner')
        self.org = Organization.objects.create(
            org_name='Door Walk Org %s' % uuid.uuid4().hex[:4],
            org_owner=org_owner, org_creator=org_owner)
        OrgMember.objects.create(org=self.org, user=self.org_events,
                                 role='manager', scopes=['events'])

        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Every Door %s' % uuid.uuid4().hex[:4], game=game,
            creator=self.creator, organization=self.org,
            event_type='physical', desc='x', entry_fee=0,
            reg_start_date=now, reg_end_date=now + timezone.timedelta(days=5),
            start_date=now + timezone.timedelta(days=10),
            end_date=now + timezone.timedelta(days=10, hours=6))
        EventManager.objects.create(event=self.event, user=self.manager,
                                    role='manager')
        EventManager.objects.create(event=self.event, user=self.door,
                                    role='door')

    def answer(self, auth, route, method):
        call = getattr(self.client, method)
        res = call(fill(route, self.event.slug), data={},
                   content_type='application/json', **auth)
        return res.status_code

    def walk(self):
        """{(route, method): {role: status}} for every door and method."""
        table = {}
        for route in routes_taking_an_event():
            for method in ('get', 'post', 'put', 'patch', 'delete'):
                # Deleting the fixture mid-walk would make every door after it
                # answer 404 for the right reason; those two are walked last,
                # by hand, in their own test.
                if route.rstrip('/').endswith(('delete', 'restore')):
                    continue
                # The first EventManager row in a fresh database is id 1,
                # which is the manager fixture: the creator's DELETE here
                # would strip them of the role and every door after it would
                # refuse them for the right reason. Walked by hand below.
                if (route, method) == ('<str:event_id>/managers/<int:manager_id>/', 'delete'):
                    continue
                creator = self.answer(self.creator_auth, route, method)
                if creator == 405:
                    continue
                table[(route, method)] = {
                    'creator': creator,
                    'manager': self.answer(self.manager_auth, route, method),
                    'org_events': self.answer(self.org_events_auth, route, method),
                    'door': self.answer(self.door_auth, route, method),
                    'stranger': self.answer(self.stranger_auth, route, method),
                }
        return table

    def test_the_route_list_is_not_empty(self):
        self.assertGreater(len(routes_taking_an_event()), 60)

    def test_the_creator_is_admitted_at_most_doors(self):
        """The differential below is only as good as the creator's side."""
        table = self.walk()
        admitted = [k for k, v in table.items() if v['creator'] in ADMITTED]
        self.assertGreater(len(admitted), 60, sorted(table.items())[:5])

    def test_a_manager_is_refused_nowhere_the_creator_is_admitted(self):
        table = self.walk()
        failing = []
        for (route, method), got in table.items():
            if got['creator'] in (401, 403):
                continue
            if (route, method) in CREATOR_ONLY:
                continue
            for who in ('manager', 'org_events'):
                if got[who] in (401, 403):
                    failing.append('%s %s: creator %s, %s %s' % (
                        method.upper(), route, got['creator'], who, got[who]))
        self.assertEqual(failing, [], 'doors still asking for the creator:\n'
                         + '\n'.join(failing))

    def test_the_creator_only_doors_still_refuse_a_manager(self):
        """The allow-list above is a claim; this is what holds it."""
        res = self.client.post(fill('<str:event_id>/managers/', self.event.slug),
                               data={'username': self.stranger.username},
                               content_type='application/json', **self.manager_auth)
        self.assertEqual(res.status_code, 403, res.content)
        res = self.client.post(fill('<str:event_id>/delete/', self.event.slug),
                               data={}, content_type='application/json',
                               **self.manager_auth)
        self.assertEqual(res.status_code, 403, res.content)
        row = EventManager.objects.get(event=self.event, user=self.door)
        res = self.client.delete(
            '/event/%s/managers/%d/' % (self.event.slug, row.id),
            content_type='application/json', **self.manager_auth)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertTrue(EventManager.objects.filter(pk=row.pk).exists())

    def test_door_staff_reach_the_door_and_nothing_else(self):
        table = self.walk()
        leaks = []
        for (route, method), got in table.items():
            # A door that answers the creator 404 (a sub-resource that does
            # not exist) tells nothing about who may reach it.
            if got['creator'] in (401, 403, 404):
                continue
            if route in DOOR_MAY or route in PUBLIC:
                continue
            if got['door'] not in (401, 403):
                leaks.append('%s %s: door %s (creator %s)' % (
                    method.upper(), route, got['door'], got['creator']))
        self.assertEqual(leaks, [], 'doors open to door staff:\n' + '\n'.join(leaks))

    def test_a_stranger_reaches_only_what_is_public(self):
        table = self.walk()
        leaks = []
        for (route, method), got in table.items():
            if got['creator'] in (401, 403):
                continue
            if route in PUBLIC:
                continue
            if got['stranger'] not in (401, 403, 404):
                leaks.append('%s %s: stranger %s (creator %s)' % (
                    method.upper(), route, got['stranger'], got['creator']))
        self.assertEqual(leaks, [], 'doors open to a stranger:\n' + '\n'.join(leaks))
