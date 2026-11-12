"""Each kind of event carries only the options it uses (inbox 588).

CEO, 11 November 2026: "certain options showing when you select a type of
event where they shouldnt show for, not being able to edit somethings or change
somethings". The wizard sent a venue typed before switching to Virtual, the
event page then showed a map for an online event, and the game's edition and
the event's own links could be set once and never again.
"""
import json
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, GameSeries, Users

from .models import Event, SocialLink


def a_user(name):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tok-%s' % name)[:16],
    )
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save(update_fields=['login_session_created_at', 'login_session_2fa_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class EventKindTests(TestCase):
    def setUp(self):
        self.owner, self.auth = a_user('kinds')
        self.fc, _ = Games.objects.get_or_create(game_title='EA FC 25')
        self.tekken, _ = Games.objects.get_or_create(game_title='Tekken 8')
        self.fc25 = GameSeries.objects.create(game=self.fc, name='EA FC 25', release_year=2025)
        self.fc26 = GameSeries.objects.create(game=self.fc, name='EA FC 26', release_year=2026)
        now = timezone.now()
        self.event = Event.objects.create(
            name='Kinds Night', game=self.fc, creator=self.owner,
            event_type='physical', category='esports', desc='A night.',
            location='Landmark Centre, Lagos', capacity=100,
            start_date=now + timedelta(days=10), end_date=now + timedelta(days=10, hours=6),
        )

    def url(self):
        return '/event/edit-event/%s/' % self.event.event_id

    def put(self, data):
        return self.client.put(self.url(), data=json.dumps(data),
                               content_type='application/json', **self.auth)

    def card(self):
        res = self.client.get('/event/view-event/%s/' % self.event.event_id, **self.auth)
        return res.json()['data']['event']

    # ------------------------------------------------------- creating one
    def create(self, **extra):
        start = timezone.now() + timedelta(days=30)
        body = {
            'name': 'Kind %s' % uuid.uuid4().hex[:4], 'description': 'x',
            'category': 'esports', 'start_date': start.isoformat(),
            'end_date': (start + timedelta(hours=4)).isoformat(), 'capacity': 50,
            'ticket_types': [],
        }
        body.update(extra)
        res = self.client.post('/event/create-event/', data=json.dumps(body),
                               content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 201, res.content)
        return Event.objects.get(event_id=res.json()['data']['event_id'])

    def test_a_virtual_event_does_not_keep_a_venue_typed_before_the_switch(self):
        event = self.create(event_type='virtual', location='Typed before switching',
                            virtual_link='https://twitch.tv/vent')
        self.assertIsNone(event.location)
        self.assertEqual(event.event_link, 'https://twitch.tv/vent')

    def test_a_physical_event_does_not_keep_a_stray_stream_link(self):
        event = self.create(event_type='physical', location='Eko Hotel, Lagos',
                            virtual_link='https://twitch.tv/stray')
        self.assertIsNone(event.event_link)
        self.assertEqual(event.location, 'Eko Hotel, Lagos')

    # ------------------------------------------------------- the kind
    def test_an_unknown_kind_is_refused(self):
        res = self.put({'event_type': 'party'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_EVENT_TYPE')

    def test_hybrid_without_a_link_is_refused_like_publish(self):
        res = self.put({'event_type': 'hybrid'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'LINK_REQUIRED')
        self.event.refresh_from_db()
        self.assertEqual(self.event.event_type, 'physical')

    def test_physical_without_a_venue_is_refused(self):
        res = self.put({'location': ''})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'LOCATION_REQUIRED')

    def test_going_online_hides_the_venue_without_losing_it(self):
        res = self.put({'event_type': 'virtual', 'event_link': 'https://youtube.com/live'})
        self.assertEqual(res.status_code, 200, res.content)
        card = self.card()
        self.assertIsNone(card['location'])
        self.assertIsNone(card['map_search_url'])
        self.assertEqual(card['virtual_link'], 'https://youtube.com/live')
        # Kept in the row, so switching back brings the venue back.
        self.event.refresh_from_db()
        self.assertEqual(self.event.location, 'Landmark Centre, Lagos')
        self.assertEqual(self.put({'event_type': 'physical'}).status_code, 200)
        self.assertEqual(self.card()['location'], 'Landmark Centre, Lagos')
        self.assertIsNone(self.card()['virtual_link'])

    def test_an_event_already_breaking_the_rule_can_still_be_renamed(self):
        Event.objects.filter(pk=self.event.pk).update(event_type='hybrid', event_link=None)
        res = self.put({'name': 'Renamed Night'})
        self.assertEqual(res.status_code, 200, res.content)

    # ------------------------------------------------------- the category
    def test_an_unknown_category_is_refused(self):
        res = self.put({'category': 'gaming'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_CATEGORY')

    def test_a_legacy_category_sent_back_unchanged_is_kept(self):
        Event.objects.filter(pk=self.event.pk).update(category='Gaming Night')
        res = self.put({'category': 'Gaming Night', 'name': 'Still Here'})
        self.assertEqual(res.status_code, 200, res.content)

    # ------------------------------------------------------- the edition
    def test_the_edition_can_be_set_after_creation(self):
        res = self.put({'series': self.fc26.series_id})
        self.assertEqual(res.status_code, 200, res.content)
        card = self.card()
        self.assertEqual(card['series_id'], self.fc26.series_id)
        self.assertEqual(card['series_name'], 'EA FC 26')

    def test_an_edition_of_another_game_is_refused(self):
        other = GameSeries.objects.create(game=self.tekken, name='Tekken 8 S2')
        res = self.put({'series': other.series_id})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'SERIES_NOT_OF_GAME')

    def test_changing_the_game_drops_an_edition_that_no_longer_fits(self):
        self.put({'series': self.fc25.series_id})
        res = self.put({'game': 'Tekken 8'})
        self.assertEqual(res.status_code, 200, res.content)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.series_id)

    def test_the_edition_can_be_taken_off(self):
        self.put({'series': self.fc25.series_id})
        self.assertEqual(self.put({'series': ''}).status_code, 200)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.series_id)

    # ------------------------------------------------------- the links
    def test_social_links_can_be_set_changed_and_removed(self):
        res = self.put({'social_links': {'twitter': 'https://x.com/vent',
                                         'instagram': 'https://instagram.com/vent'}})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.card()['social_links'],
                         {'twitter': 'https://x.com/vent', 'instagram': 'https://instagram.com/vent'})
        self.put({'social_links': {'twitter': ''}})
        self.assertEqual(self.card()['social_links'], {'instagram': 'https://instagram.com/vent'})
        self.assertEqual(SocialLink.objects.filter(event=self.event).count(), 1)

    def test_a_link_that_is_not_a_web_address_is_refused(self):
        res = self.put({'social_links': {'twitter': 'javascript:alert(1)'}})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_LINK')
