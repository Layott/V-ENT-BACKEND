"""An unlisted event leaves the listing and stays open at its own address.

The edit page's "Listed publicly" switch flipped `is_active` until
18 September 2026, so turning it off took the page, the checkout and the stalls
down with the listing: the opposite of the sentence under the switch.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event.models import Event


def organiser():
    user = Users.objects.create(
        username='unl_%s' % uuid.uuid4().hex[:5], email='unl_%s@vent.test' % uuid.uuid4().hex[:5],
        is_active=True, login_session_token=('tk%s' % uuid.uuid4().hex)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class UnlistedEventTests(TestCase):
    def setUp(self):
        self.user, self.auth = organiser()
        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.event = Event.objects.create(
            name='Quiet Launch %s' % uuid.uuid4().hex[:4], game=game,
            creator=self.user, event_type='physical', desc='x', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            start_date=timezone.now() + timezone.timedelta(days=10),
            end_date=timezone.now() + timezone.timedelta(days=10, hours=6))

    def listed_slugs(self):
        res = self.client.get('/event/get-all-events/')
        return [e['slug'] for e in res.json()['data']['events']]

    def test_the_switch_leaves_the_listing_and_keeps_the_page(self):
        self.assertIn(self.event.slug, self.listed_slugs())
        res = self.client.put('/event/edit-event/%s/' % self.event.slug,
                              data={'is_listed': 'false'},
                              content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_listed)
        self.assertTrue(self.event.is_active)
        self.assertNotIn(self.event.slug, self.listed_slugs())
        # Still open by its link, for the people it was shared with.
        res = self.client.get('/event/view-event/%s/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['data']['event']['is_listed'])
        res = self.client.get('/event/%s/ticket-types/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content)
        res = self.client.get('/event/%s/vendors/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content)
        # And the organiser still finds it in their own list.
        res = self.client.get('/event/my-events/', **self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['data']
        rows = rows.get('results') if isinstance(rows, dict) else rows
        self.assertIn(self.event.slug, [r['slug'] for r in rows])

    def test_the_partner_feed_and_the_org_feed_skip_it(self):
        from vent_auth.models import Organization, OrgFollower
        from vent_partners.models import PartnerApiKey
        from vent_partners.tests_api import make_partner

        org = Organization.objects.create(org_name='Org %s' % uuid.uuid4().hex[:4],
                                          org_creator=self.user, org_owner=self.user)
        self.event.organization = org
        self.event.save(update_fields=['organization'])
        follower, follower_auth = organiser()
        OrgFollower.objects.create(org=org, user=follower)
        partner = make_partner(approved_scopes=['events:read'])
        _key, secret = PartnerApiKey.issue(partner, scopes=['events:read'])
        partner_auth = {'HTTP_AUTHORIZATION': 'Bearer %s' % secret}

        def seen():
            feed = self.client.get('/organization/following/feed/', **follower_auth).json()['data']['items']
            api = self.client.get('/api/v1/events/', **partner_auth).json()
            api_rows = api.get('data', api)
            api_rows = api_rows.get('events', api_rows.get('results', api_rows)) if isinstance(api_rows, dict) else api_rows
            return (self.event.slug in [i.get('slug') for i in feed],
                    self.event.slug in [r.get('slug') for r in api_rows])

        self.assertEqual(seen(), (True, True))
        self.event.is_listed = False
        self.event.save(update_fields=['is_listed'])
        self.assertEqual(seen(), (False, False))
