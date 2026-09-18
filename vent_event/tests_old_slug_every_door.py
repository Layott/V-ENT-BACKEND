"""Every address an event has ever had keeps working, at EVERY door.

On 18 September 2026 a renamed event's public page followed the move and its
console did not: all twenty endpoints under /event/<old-slug>/ answered 404,
because sixteen views each resolved the slug themselves and none read the
history. This walks every route in `vent_event.urls` that takes an event
reference and compares the answer at the RETIRED slug with the answer at a
slug that never existed. If they are the same, that door is not following the
move. It never asserts a particular status, because a door may legitimately
refuse for another reason (method, role, missing body); it asserts only that
the retired slug is not treated as nothing.
"""
import re
import uuid

from django.test import TestCase
from django.urls import URLPattern
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event import urls as event_urls
from vent_event.models import Event

NEVER = 'never-was-an-event-xyz'


def organiser():
    user = Users.objects.create(
        username='old_slug_%s' % uuid.uuid4().hex[:5], email='old@vent.test',
        is_active=True, login_session_token=('tk%s' % uuid.uuid4().hex)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def routes_taking_an_event():
    """(pattern string) for every route with <str:event_id> in it."""
    out = []
    for entry in event_urls.urlpatterns:
        if not isinstance(entry, URLPattern):
            continue
        route = str(entry.pattern)
        if '<str:event_id>' in route:
            out.append(route)
    return out


def fill(route, ref):
    """Substitute the event ref and something plausible for every other part."""
    path = route.replace('<str:event_id>', ref)
    path = re.sub(r'<int:[^>]+>', '1', path)
    path = re.sub(r'<str:[^>]+>', 'x', path)
    path = re.sub(r'<slug:[^>]+>', 'x', path)
    return '/event/' + path


class OldSlugEveryDoorTests(TestCase):
    def setUp(self):
        self.user, self.auth = organiser()
        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.event = Event.objects.create(
            name='Before Rename %s' % uuid.uuid4().hex[:4], game=game,
            creator=self.user, event_type='physical', desc='x', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            start_date=timezone.now() + timezone.timedelta(days=10),
            end_date=timezone.now() + timezone.timedelta(days=10, hours=6))
        self.old = self.event.slug
        self.event.name = 'After Rename %s' % uuid.uuid4().hex[:4]
        self.event.save()
        self.assertNotEqual(self.old, self.event.slug)

    def shape(self, res):
        try:
            body = res.json()
        except ValueError:
            body = {}
        return (res.status_code, body.get('code'), body.get('message'))

    def test_the_route_list_is_not_empty(self):
        """A passing zero has two meanings; this tells them apart."""
        self.assertGreater(len(routes_taking_an_event()), 30)

    def test_every_door_follows_the_retired_slug(self):
        """Three answers per door: at the retired slug, at the live slug, at a
        slug that never was. A door FOLLOWS when the retired slug answers as
        the live one does. It FAILS when the retired slug answers as the
        never-was one does while the live slug answers differently. When all
        three agree (a sub-resource missing in every case) nothing can be
        told, and nothing is claimed."""
        failing = []
        for route in routes_taking_an_event():
            for method in ('get', 'post'):
                # Deleting the fixture mid-walk would make every door after it
                # answer 404 for the right reason.
                if method == 'post' and route.rstrip('/').endswith(('delete', 'restore')):
                    continue
                call = getattr(self.client, method)
                answers = {}
                for label, ref in (('old', self.old), ('live', self.event.slug), ('never', NEVER)):
                    res = call(fill(route, ref), data={}, content_type='application/json', **self.auth)
                    answers[label] = self.shape(res)
                if answers['never'][0] == 405:
                    continue
                if answers['old'] == answers['never'] and answers['old'] != answers['live']:
                    failing.append('%s %s -> %s (live: %s)' % (
                        method.upper(), fill(route, self.old), answers['old'], answers['live']))
        self.assertEqual(failing, [], 'doors answering a retired slug as nothing:\n' + '\n'.join(failing))

    def test_the_live_slug_and_the_id_still_answer(self):
        res = self.client.get('/event/%s/tiers/' % self.event.slug, **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        res = self.client.get('/event/%s/tiers/' % self.event.event_id, **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        res = self.client.get('/event/%s/tiers/' % self.old, **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
