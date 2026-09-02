"""Every tournament route answers a slug, because that is what the console has.

CEO, 2 September 2026, with a screenshot of his own tournament console:

    Pending BE deploy - this action activates once the backend endpoint ships.
    (Cancel & Refund)

The endpoint was not missing. It had existed for months. The console addresses
a tournament by SLUG - the slug rule says no numeric id appears in an address a
person can see - and `<int:tournament_id>` does not match a slug. Django
answered 404, and the frontend reads a 404 as "not deployed yet".

Measured on production before the fix:

    POST /tournament/rivalvry-series-s2/cancel/   404
    POST /tournament/26/cancel/                   409   (a real answer)

Twenty-six routes were `<int:>` against thirty-one `<str:>`, so every one of
those actions was unreachable from the console that offers it. This file walks
the whole list in both spellings, so a route added later in the wrong one fails
here rather than on somebody's console.
"""
from django.test import TestCase
from django.urls import get_resolver
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_tournament.models import Tournament


class SlugOrIdTests(TestCase):
    def setUp(self):
        self.owner = Users.objects.create(
            username='slugOwner', email='sl@vent.test',
            login_session_token='slug-owner-tok'[:16], is_active=True)
        self.owner.login_session_created_at = timezone.now()
        self.owner.save()
        self.auth = {'HTTP_AUTHORIZATION':
                     'Bearer %s' % self.owner.login_session_token}
        game = Games.objects.create(game_title='Slug FC')
        self.tournament = Tournament.objects.create(
            tournament_title='Slug Cup', tournament_game=game,
            tournament_creator=self.owner,
            start_date_and_time=timezone.now(),
            end_date_and_time=timezone.now(), is_draft=False)

    def test_no_tournament_route_takes_an_integer_id(self):
        """The rule, stated once against the URL conf itself rather than
        against a list somebody has to remember to update."""
        patterns = []

        def walk(resolver, prefix=''):
            for entry in resolver.url_patterns:
                if hasattr(entry, 'url_patterns'):
                    walk(entry, prefix + str(entry.pattern))
                else:
                    patterns.append(prefix + str(entry.pattern))

        walk(get_resolver())
        # Scoped to the routes the organiser console calls. The admin console
        # under `auth/admin/` is fed ids from its own list and never sees a
        # slug, so an int there is honest rather than a trap. If the admin
        # console ever starts addressing tournaments the way the rest of the
        # platform does, this scope is the line to widen.
        # Two deliberate exemptions, both because the caller is handed an id
        # by us and never sees a slug:
        #   auth/admin/  the admin console, fed ids from its own list
        #   api/v1/      the partner API, a documented external contract where
        #                changing the address shape is a breaking change
        # If either ever starts addressing tournaments the way the rest of the
        # platform does, these are the lines to delete.
        exempt = ('auth/', 'api/v1/')
        offenders = [p for p in patterns
                     if '<int:tournament_id>' in p
                     and not p.startswith(exempt)]
        self.assertEqual(
            offenders, [],
            'these routes cannot be called with a slug, which is all the '
            'console has: %s' % offenders)

    def test_the_resolver_finds_a_tournament_by_either(self):
        from vent_tournament import lookup

        self.assertEqual(lookup.find(self.tournament.slug), self.tournament)
        self.assertEqual(lookup.find(self.tournament.tournament_id), self.tournament)
        self.assertEqual(lookup.find(str(self.tournament.tournament_id)), self.tournament)

    def test_the_resolver_says_no_rather_than_raising(self):
        from vent_tournament import lookup

        for missing in (None, '', '   ', 'no-such-tournament', 999999):
            self.assertIsNone(lookup.find(missing), missing)

    def test_cancel_answers_a_slug_rather_than_404(self):
        """The one the CEO reported. A 404 here is what the console renders as
        "Pending BE deploy"."""
        res = self.client.post(
            '/tournament/%s/cancel/' % self.tournament.slug,
            data={'reason': 'probe'}, content_type='application/json',
            **self.auth)
        self.assertNotEqual(res.status_code, 404, res.content[:200])

    def test_the_reads_answer_a_slug(self):
        for path in ('/tournament/%s/rules/',
                     '/tournament/%s/standings/',
                     '/tournament/%s/requirements/',
                     '/tournament/%s/check-in/status/',
                     '/tournament/%s/stages/'):
            res = self.client.get(path % self.tournament.slug, **self.auth)
            self.assertNotEqual(res.status_code, 404,
                                '%s -> 404' % (path % self.tournament.slug))

    def test_the_same_routes_still_answer_an_id(self):
        """Anything already holding an id keeps working. A fix that trades one
        spelling for the other is the same bug facing the other way."""
        for path in ('/tournament/%s/rules/',
                     '/tournament/%s/standings/',
                     '/tournament/%s/requirements/',
                     '/tournament/%s/check-in/status/',
                     '/tournament/%s/stages/'):
            res = self.client.get(path % self.tournament.tournament_id, **self.auth)
            self.assertNotEqual(res.status_code, 404,
                                '%s -> 404' % (path % self.tournament.tournament_id))

    def test_a_tournament_that_does_not_exist_is_still_a_404(self):
        """Accepting a slug must not turn a genuine miss into a 200."""
        res = self.client.get('/tournament/no-such-tournament/rules/')
        self.assertEqual(res.status_code, 404)
