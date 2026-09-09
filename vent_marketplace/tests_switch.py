"""Off means off, and it is proven from outside rather than asserted.

The CEO's instruction is that Vermillion City is BUILT and CLOSED. A thing that
is built and switched off looks exactly like a thing that is half built, right
up until somebody turns it on, so these tests ask the same question twice: does
every route refuse while it is closed, and does the whole thing work when it is
open.

The route-coverage test is the one that matters most. `vent_billing` had the
same test looking for `__wrapped__`, which DRF's `@api_view` sets on every view,
so it passed with a route deliberately left open. This one looks for an explicit
mark that only `switch.gated` sets.
"""
from django.test import TestCase, override_settings
from django.urls import get_resolver
from rest_framework.test import APIClient

from vent_auth.models import Games, UserWallet, Users

from . import switch
from .models import Listing


def make_user(i, coins=0):
    from django.utils import timezone
    import uuid
    user = Users.objects.create(
        username='mk%s' % i, email='mk%s@test.co' % i,
        login_session_token='mkt%s' % str(i).zfill(12),
        login_session_created_at=timezone.now(), is_active=True)
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user,
                              wallet_balance=coins)
    return user


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return client


class EveryRouteIsGatedTests(TestCase):

    def _marketplace_patterns(self):
        """Every route mounted under /marketplace/."""
        resolver = get_resolver()
        found = []
        for entry in resolver.url_patterns:
            if getattr(entry, 'pattern', None) and \
                    str(entry.pattern) == 'marketplace/':
                for route in entry.url_patterns:
                    found.append(route)
        return found

    def test_there_are_routes_to_check(self):
        """A coverage test that finds nothing passes for the wrong reason."""
        self.assertGreaterEqual(len(self._marketplace_patterns()), 15)

    def test_every_single_one_carries_the_mark(self):
        open_routes = [
            str(route.pattern) for route in self._marketplace_patterns()
            if not getattr(route.callback, 'marketplace_gated', False)
        ]
        self.assertEqual(open_routes, [],
                         'these marketplace routes answer while it is closed')

    def test_the_mark_is_not_something_drf_sets_by_itself(self):
        """The fault that made the billing version of this test useless.

        `@api_view` sets `__wrapped__` on every view, so a test looking for
        that passed with a route left open. Proven here by checking a view that
        is NOT wrapped by `gated`.
        """
        from . import views_listings
        self.assertTrue(hasattr(views_listings.browse, '__wrapped__'))
        self.assertFalse(getattr(views_listings.browse, 'marketplace_gated', False))


@override_settings(MARKETPLACE_ENABLED=False)
class ClosedTests(TestCase):

    def setUp(self):
        self.user = make_user(1)
        self.client = client_for(self.user)

    def test_the_default_is_closed(self):
        """No setting at all must read as closed, not crash and not open."""
        from django.test import override_settings as os_
        with os_():
            self.assertFalse(switch.marketplace_is_on()
                             if not hasattr(__import__('django.conf', fromlist=['settings']).settings,
                                            'MARKETPLACE_ENABLED')
                             else False)

    def test_browsing_is_refused_by_name(self):
        res = self.client.get('/marketplace/listings/')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()['code'], 'MARKETPLACE_OFF')

    def test_the_catalogue_is_refused(self):
        self.assertEqual(self.client.get('/marketplace/catalogue/').status_code, 503)

    def test_creating_is_refused(self):
        res = self.client.post('/marketplace/listings/new/',
                               {'kind': 'sale', 'category': 'merchandise',
                                'title': 'A thing', 'price': 10, 'quantity': 1},
                               format='json')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(Listing.objects.count(), 0)

    def test_signed_out_is_refused_the_same_way(self):
        """Closed beats unauthenticated. A 401 would say the door exists."""
        res = APIClient().get('/marketplace/listings/')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()['code'], 'MARKETPLACE_OFF')

    def test_it_refuses_without_reading_a_marketplace_table(self):
        """A closed endpoint must not run the query it exists to run.

        Not zero queries: the request pipeline resolves the session token
        before any view is reached, and that is not this module's work. What
        matters is that no marketplace table is touched.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as queries:
            self.client.get('/marketplace/listings/')
        touched = [q['sql'] for q in queries if 'vent_marketplace' in q['sql']]
        self.assertEqual(touched, [])


@override_settings(MARKETPLACE_ENABLED=True)
class OpenTests(TestCase):
    """The other half: a build nobody has run with the switch on is untested."""

    def setUp(self):
        self.seller = make_user(2)
        self.client = client_for(self.seller)

    def test_the_catalogue_answers(self):
        res = self.client.get('/marketplace/catalogue/')
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertEqual(len(data['kinds']), 3)
        self.assertEqual(data['free_active_listings'], 1)

    def test_a_listing_can_be_made_and_found(self):
        made = self.client.post('/marketplace/listings/new/', {
            'kind': 'service', 'category': 'coaching',
            'title': 'League coaching, plat and below',
            'description': 'An hour of review.',
            'price_kind': 'hourly', 'price': 500,
            'publish': True,
        }, format='json')
        self.assertEqual(made.status_code, 201, made.content)
        slug = made.json()['data']['listing']['slug']
        self.assertEqual(slug, 'league-coaching-plat-and-below')

        found = self.client.get('/marketplace/listings/')
        titles = [row['title'] for row in found.json()['data']['listings']]
        self.assertIn('League coaching, plat and below', titles)

    def test_the_address_follows_the_title(self):
        made = self.client.post('/marketplace/listings/new/', {
            'kind': 'sale', 'category': 'merchandise', 'title': 'Old jersey',
            'price': 10, 'quantity': 1, 'publish': True}, format='json')
        ref = made.json()['data']['listing']['slug']
        edited = self.client.put('/marketplace/listings/%s/edit/' % ref,
                                 {'title': 'Signed jersey'}, format='json')
        # Checked, because the first version of this test read the slug without
        # looking at the response and the edit had been refused for want of a
        # price nobody was asked to resend.
        self.assertEqual(edited.status_code, 200, edited.content)
        listing = Listing.objects.get()
        self.assertEqual(listing.slug, 'signed-jersey')
        # And the old address still works, which is the half people forget. It
        # answers 200 with `moved` rather than a 301: fetch() follows a
        # redirect transparently and would chase a frontend path against the
        # API host.
        old = self.client.get('/marketplace/listings/old-jersey/')
        self.assertEqual(old.status_code, 200)
        self.assertEqual(old.json()['status'], 'moved')
        self.assertEqual(old.json()['data']['slug'], 'signed-jersey')
