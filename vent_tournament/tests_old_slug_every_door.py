"""Every address a tournament has ever had keeps working, at EVERY door.

The tournament twin of vent_event/tests_old_slug_every_door.py: sixteen
private copies of "by slug or by id" in this app missed the slug history the
same way (18 September 2026). Differential, not absolute: a door fails only
when it answers a RETIRED slug exactly as it answers a slug that never was.
"""
import re
import uuid

from django.test import TestCase
from django.urls import URLPattern
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_tournament import urls as tournament_urls
from vent_tournament.models import Tournament

NEVER = 'never-was-a-tournament-xyz'


def organiser():
    user = Users.objects.create(
        username='old_t_%s' % uuid.uuid4().hex[:5], email='oldt@vent.test',
        is_active=True, login_session_token=('tk%s' % uuid.uuid4().hex)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def routes_taking_a_tournament():
    out = []
    for entry in tournament_urls.urlpatterns:
        if not isinstance(entry, URLPattern):
            continue
        route = str(entry.pattern)
        if '<str:tournament_id>' in route:
            out.append(route)
    return out


def fill(route, ref):
    path = route.replace('<str:tournament_id>', ref)
    path = re.sub(r'<int:[^>]+>', '1', path)
    path = re.sub(r'<str:[^>]+>', 'x', path)
    path = re.sub(r'<slug:[^>]+>', 'x', path)
    return '/tournament/' + path


class OldSlugEveryDoorTests(TestCase):
    def setUp(self):
        self.user, self.auth = organiser()
        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.tournament = Tournament.objects.create(
            tournament_title='Before Rename %s' % uuid.uuid4().hex[:4],
            tournament_game=game, tournament_creator=self.user,
            start_date_and_time=timezone.now() + timezone.timedelta(days=10),
            end_date_and_time=timezone.now() + timezone.timedelta(days=11),
            is_draft=False)
        self.old = self.tournament.slug
        self.tournament.tournament_title = 'After Rename %s' % uuid.uuid4().hex[:4]
        self.tournament.save()
        self.assertNotEqual(self.old, self.tournament.slug)

    def shape(self, res):
        try:
            body = res.json()
        except ValueError:
            body = {}
        return (res.status_code, body.get('code'), body.get('message'))

    def test_the_route_list_is_not_empty(self):
        self.assertGreater(len(routes_taking_a_tournament()), 30)

    def test_every_door_follows_the_retired_slug(self):
        """Three answers per door: at the retired slug, at the live slug, at a
        slug that never was. A door FOLLOWS when the retired slug answers as
        the live one does. It FAILS when the retired slug answers as the
        never-was one does while the live slug answers differently. When all
        three agree (a sub-resource missing in every case) nothing can be
        told, and nothing is claimed."""
        failing = []
        for route in routes_taking_a_tournament():
            for method in ('get', 'post'):
                # Deleting the fixture mid-walk would make every door after it
                # answer 404 for the right reason.
                if method == 'post' and route.rstrip('/').endswith(('delete', 'restore')):
                    continue
                call = getattr(self.client, method)
                answers = {}
                for label, ref in (('old', self.old), ('live', self.tournament.slug), ('never', NEVER)):
                    res = call(fill(route, ref), data={}, content_type='application/json', **self.auth)
                    answers[label] = self.shape(res)
                if answers['never'][0] == 405:
                    continue
                if answers['old'] == answers['never'] and answers['old'] != answers['live']:
                    failing.append('%s %s -> %s (live: %s)' % (
                        method.upper(), fill(route, self.old), answers['old'], answers['live']))
        self.assertEqual(failing, [], 'doors answering a retired slug as nothing:\n' + '\n'.join(failing))
