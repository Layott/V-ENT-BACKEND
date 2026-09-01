"""Short addresses for ticket links.

CEO, 1 September: "add an option for people to be able to shorten their ticket
links, so you create very short versions of the ticket links."

Most of these tests are about the one way a link shortener goes wrong. A
shortener that will store any target somebody sends is an open redirect: an
address on v-ent.co that lands on a page the platform does not control, with
the platform's name lending it credibility. That is the test worth having.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users, UserWallet

from .models import Event, ShortLink, TicketTier
from .views_short_links import TOKEN_LENGTH


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('l-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('lw%s' % name)[:10], user=user, wallet_balance=0,
        pin_hash=make_password('1234'))
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ShortLinkTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('tslA')
        self.other, self.other_auth = a_user('tslB')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Lagos Anime Con', creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=6),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        TicketTier.objects.create(event=self.event, name='General',
                                  price=Decimal('0'), quantity=100)
        self.url = '/event/%s/short-links/' % self.event.event_id

    def make(self, body=None, auth=None):
        return self.client.post(self.url, data=body or {},
                                content_type='application/json',
                                **(auth if auth is not None else self.auth))

    # -------------------------------------------------------------- the link

    def test_it_shortens_the_ticket_link_with_no_target_given(self):
        """The button on the share card sends nothing and means "this event"."""
        res = self.make()
        self.assertEqual(res.status_code, 201, res.json())
        link = res.json()['data']['link']
        self.assertEqual(len(link['token']), TOKEN_LENGTH)
        self.assertTrue(link['target'].startswith('/events/'))
        self.assertIn('tab=tickets', link['target'])
        self.assertIn('/s/%s' % link['token'], link['url'])

    def test_the_token_avoids_the_characters_that_are_misread_aloud(self):
        """These get read out on a livestream. l, o, 0 and 1 are the whole problem."""
        for _ in range(20):
            ShortLink.objects.all().delete()
            token = self.make().json()['data']['link']['token']
            self.assertFalse(set(token) & set('lo01'), token)

    def test_asking_twice_returns_the_same_code(self):
        """A second press wants the code already printed, not a new one."""
        first = self.make().json()['data']['link']
        second = self.make().json()['data']['link']
        self.assertEqual(first['token'], second['token'])
        self.assertEqual(ShortLink.objects.filter(event=self.event).count(), 1)

    def test_a_label_tells_two_links_apart(self):
        a = self.make({'target': '/events/x?tab=tickets&ref=TEMI',
                       'label': 'Temi story'}).json()['data']['link']
        b = self.make({'target': '/events/x?tab=tickets&ref=RADIO',
                       'label': 'radio read'}).json()['data']['link']
        self.assertNotEqual(a['token'], b['token'])
        self.assertEqual(a['label'], 'Temi story')
        self.assertEqual(b['label'], 'radio read')

    # ------------------------------------------------------- the open redirect

    def test_an_absolute_url_is_refused(self):
        res = self.make({'target': 'https://evil.example/pay'})
        self.assertEqual(res.status_code, 400, res.json())
        self.assertEqual(res.json()['code'], 'INVALID_TARGET')
        self.assertEqual(ShortLink.objects.count(), 0)

    def test_a_protocol_relative_target_is_refused(self):
        """`//evil.example` is a host to a browser, not a path."""
        res = self.make({'target': '//evil.example/pay'})
        self.assertEqual(res.status_code, 400, res.json())
        self.assertEqual(ShortLink.objects.count(), 0)

    def test_a_backslash_target_is_refused(self):
        res = self.make({'target': '/\\evil.example'})
        self.assertEqual(res.status_code, 400, res.json())
        self.assertEqual(ShortLink.objects.count(), 0)

    def test_a_target_with_no_leading_slash_is_refused(self):
        res = self.make({'target': 'events/x'})
        self.assertEqual(res.status_code, 400, res.json())

    # ---------------------------------------------------------- resolving it

    def test_it_resolves_to_the_target_and_counts_the_arrival(self):
        token = self.make().json()['data']['link']['token']
        self.client.cookies.clear()
        res = self.client.get('/s/%s/' % token)
        self.assertEqual(res.status_code, 200, res.json())
        self.assertIn('tab=tickets', res.json()['data']['target'])
        self.assertEqual(ShortLink.objects.get(token=token).hits, 1)

    def test_resolving_needs_no_account(self):
        """A short link is handed to strangers. That is the entire point."""
        token = self.make().json()['data']['link']['token']
        res = self.client.get('/s/%s/' % token)
        self.assertEqual(res.status_code, 200)

    def test_an_unknown_token_is_a_404(self):
        res = self.client.get('/s/zzzzzz/')
        self.assertEqual(res.status_code, 404)

    def test_a_switched_off_link_stops_resolving(self):
        link = self.make().json()['data']['link']
        res = self.client.delete(
            '/event/%s/short-links/%s/' % (self.event.event_id, link['id']),
            **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.assertEqual(self.client.get('/s/%s/' % link['token']).status_code, 404)
        # Switched off, not deleted: the code stays claimed so it can never be
        # reissued pointing somewhere else.
        self.assertTrue(ShortLink.objects.filter(token=link['token']).exists())

    # ------------------------------------------------------------ who may

    def test_somebody_else_cannot_shorten_this_events_links(self):
        res = self.make(auth=self.other_auth)
        self.assertEqual(res.status_code, 403, res.json())
        self.assertEqual(ShortLink.objects.count(), 0)

    def test_the_organiser_sees_their_own_list(self):
        self.make({'target': '/events/x?tab=tickets', 'label': 'flyer'})
        res = self.client.get(self.url, **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.assertEqual(len(res.json()['data']['links']), 1)
        self.assertEqual(res.json()['data']['links'][0]['label'], 'flyer')
