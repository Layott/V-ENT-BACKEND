"""A cancelled event keeps its page and refuses the till by name.

The admin console cancels an event by clearing `is_active` and promises
"its page keeps answering so ticket holders find out what happened". Until
18 September 2026 every public reader treated an inactive event as one that
never existed: the page was a 404 and the buy doors said "Event not found."
to somebody holding a ticket.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event.models import Event, TicketTier


def a_user():
    user = Users.objects.create(
        username='cx_%s' % uuid.uuid4().hex[:5], email='cx_%s@vent.test' % uuid.uuid4().hex[:5],
        is_active=True, login_session_token=('tk%s' % uuid.uuid4().hex)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class CancelledEventTests(TestCase):
    def setUp(self):
        self.user, self.auth = a_user()
        game, _ = Games.objects.get_or_create(game_title='EA FC 26')
        self.event = Event.objects.create(
            name='Called Off %s' % uuid.uuid4().hex[:4], game=game,
            creator=self.user, event_type='physical', desc='x', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            start_date=timezone.now() + timezone.timedelta(days=10),
            end_date=timezone.now() + timezone.timedelta(days=10, hours=6),
            is_active=False)
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=1000, quantity=10)

    def test_the_page_keeps_answering_and_says_cancelled(self):
        res = self.client.get('/event/view-event/%s/' % self.event.slug)
        self.assertEqual(res.status_code, 200, res.content)
        event = res.json()['data']['event']
        self.assertEqual(event['status'], 'cancelled')
        self.assertFalse(event['is_active'])

    def test_it_is_off_the_listing(self):
        res = self.client.get('/event/get-all-events/')
        self.assertNotIn(self.event.slug, [e['slug'] for e in res.json()['data']['events']])

    def test_the_till_refuses_it_by_name(self):
        for path in ('/event/%s/ticket-types/', '/event/%s/quote/?tier=%d' % ('%s', self.tier.id)):
            res = self.client.get(path % self.event.slug)
            self.assertEqual(res.status_code, 409, (path, res.content))
            self.assertEqual(res.json()['code'], 'EVENT_CANCELLED')
        res = self.client.post('/event/%s/buy-ticket/' % self.event.slug,
                               data={'tier_id': self.tier.id, 'quantity': 1},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'EVENT_CANCELLED')
        res = self.client.post('/event/%s/guest-buy/' % self.event.slug,
                               data={'tier_id': self.tier.id, 'quantity': 1,
                                     'email': 'a@example.com'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'EVENT_CANCELLED')

    def test_an_event_that_never_was_is_still_not_found(self):
        res = self.client.get('/event/view-event/never-was-xyz/')
        self.assertEqual(res.status_code, 404)
        res = self.client.get('/event/never-was-xyz/ticket-types/')
        self.assertEqual(res.status_code, 404)

    def test_a_deleted_event_stays_gone(self):
        self.event.deleted_at = timezone.now()
        self.event.save(update_fields=['deleted_at'])
        res = self.client.get('/event/view-event/%s/' % self.event.slug)
        self.assertEqual(res.status_code, 404)
