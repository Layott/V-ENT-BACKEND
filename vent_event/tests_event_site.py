"""An event's own website settings (inbox 360).

Read by everybody in the event payload, written only by the people who run the
event, and every value either stored as a known choice or refused naming the
field.
"""
import json
import secrets
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users
from vent_event import site
from vent_event.models import Event


def a_user(tag):
    user = Users.objects.create(username='site%s' % tag, email='%s@site.test' % tag,
                                is_active=True, login_session_token=secrets.token_hex(8))
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class EventSiteTests(TestCase):
    def setUp(self):
        self.organiser, self.as_organiser = a_user('org')
        self.stranger, self.as_stranger = a_user('str')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Site Walk Event', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=Decimal('0'), reg_start_date=now, reg_end_date=now,
            event_date=now.date(), start_time=now.time(), end_time=now.time())
        self.url = '/event/%s/site/' % self.event.slug

    def post(self, body, headers=None):
        return self.client.post(self.url, json.dumps(body), content_type='application/json',
                                **(headers or {}))

    def test_default_is_unpublished_with_every_section(self):
        body = self.client.get('/event/view-event/%s/' % self.event.slug).json()
        got = (body['data'].get('event') or body['data'])['site']
        self.assertFalse(got['enabled'])
        self.assertEqual(got['theme'], 'dark')
        self.assertEqual(got['layout'], 'poster')
        self.assertEqual([s['key'] for s in got['sections']], list(site.SECTIONS))

    def test_organiser_saves_and_the_page_reads_it(self):
        sections = [{'key': 'tickets', 'visible': True}, {'key': 'about', 'visible': False}]
        r = self.post({'enabled': True, 'headline': 'Two days of Free Fire',
                       'accent': '#E0A21B', 'theme': 'light', 'layout': 'split',
                       'sections': sections}, self.as_organiser)
        self.assertEqual(r.status_code, 200, r.content)
        self.event.refresh_from_db()
        got = site.public(self.event)
        self.assertTrue(got['enabled'])
        self.assertEqual(got['accent'], '#e0a21b')
        self.assertEqual(got['theme'], 'light')
        self.assertEqual(got['layout'], 'split')
        # The two saved first, in that order, and the rest after them switched on.
        self.assertEqual(got['sections'][:2], sections)
        self.assertEqual(len(got['sections']), len(site.SECTIONS))

    def test_a_partial_save_leaves_the_rest(self):
        self.post({'enabled': True, 'accent': '#112233'}, self.as_organiser)
        self.post({'headline': 'New line'}, self.as_organiser)
        self.event.refresh_from_db()
        self.assertTrue(self.event.site_enabled)
        self.assertEqual(self.event.site_accent, '#112233')
        self.assertEqual(self.event.site_headline, 'New line')

    def test_signed_out_and_stranger_are_refused(self):
        self.assertEqual(self.post({'enabled': True}).status_code, 401)
        self.assertEqual(self.post({'enabled': True}, self.as_stranger).status_code, 403)
        self.event.refresh_from_db()
        self.assertFalse(self.event.site_enabled)

    def test_bad_values_are_refused_naming_the_field(self):
        for body, field in [
            ({'accent': 'red'}, 'accent'),
            ({'accent': '#12345g'}, 'accent'),
            ({'theme': 'neon'}, 'theme'),
            ({'layout': 'bento'}, 'layout'),
            ({'headline': 'x' * 121}, 'headline'),
            ({'sections': [{'key': 'about'}, {'key': 'about'}]}, 'sections'),
            ({'sections': [{'key': 'scripts'}]}, 'sections'),
            ({'sections': 'about'}, 'sections'),
            ({'sections': [{'key': 'about', 'visible': 'yes'}]}, 'sections'),
            ({'enabled': 'maybe'}, 'enabled'),
        ]:
            r = self.post(body, self.as_organiser)
            self.assertEqual(r.status_code, 400, (body, r.content))
            self.assertEqual(r.json()['code'], 'INVALID_INPUT')
            self.assertEqual(r.json()['field'], field, body)

    def test_an_empty_accent_clears_it(self):
        self.post({'accent': '#112233'}, self.as_organiser)
        self.post({'accent': ''}, self.as_organiser)
        self.event.refresh_from_db()
        self.assertEqual(self.event.site_accent, '')

    def test_stored_rubbish_is_never_drawn(self):
        self.event.site_sections = [{'key': 'scripts'}, 'about', {'key': 'venue', 'visible': False}]
        self.event.site_theme = 'neon'
        self.event.save()
        got = site.public(self.event)
        self.assertEqual(got['theme'], 'dark')
        self.assertEqual(got['sections'][0], {'key': 'venue', 'visible': False})
        self.assertEqual(len(got['sections']), len(site.SECTIONS))
