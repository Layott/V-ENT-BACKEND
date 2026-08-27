"""Sponsors and partners: same table, different word, both linkable.

An event has partners as well as sponsors, and a logo that goes nowhere is a
picture of a name. Both carry a website and as many socials as the organisation
actually has.

They share one model on purpose. With two tables every screen, serializer and
admin control that shows one would be written twice, and the first field added
to sponsors would silently be missing from partners.
"""
import json
import uuid

from django.test import TestCase
from datetime import timedelta

from django.utils import timezone

from vent_auth.models import Games, Users

from .models import Event, Sponsor, SponsorLink


def an_organiser():
    user = Users.objects.create(
        username='org_%s' % uuid.uuid4().hex[:6],
        email='org_%s@vent.test' % uuid.uuid4().hex[:6],
        login_session_token='org-token-1',
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class EventSupportersTests(TestCase):
    def setUp(self):
        self.user, self.auth = an_organiser()
        Games.objects.get_or_create(game_title='EA FC')

    def _create(self, **extra):
        now = timezone.now()
        payload = {
            'name': 'Lagos Meetup',
            'event_type': 'physical',
            'description': 'A day of brackets and panels.',
            'location': 'Landmark Centre',
            'capacity': 100,
            'start_date': (now + timedelta(days=10)).isoformat(),
            'end_date': (now + timedelta(days=11)).isoformat(),
        }
        payload.update(extra)
        return self.client.post('/event/create-event/', data=json.dumps(payload),
                                content_type='application/json', **self.auth)

    def test_a_partner_is_stored_as_a_partner(self):
        res = self._create(partners=[{'name': 'CADE Esports'}])
        self.assertEqual(res.status_code, 201, res.content)
        row = Sponsor.objects.get(name='CADE Esports')
        self.assertEqual(row.kind, 'partner')

    def test_a_sponsor_is_still_a_sponsor(self):
        res = self._create(sponsors=[{'name': 'Big Brand'}])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Sponsor.objects.get(name='Big Brand').kind, 'sponsor')

    def test_both_can_exist_on_one_event(self):
        res = self._create(sponsors=[{'name': 'Brand A'}],
                           partners=[{'name': 'Club B'}])
        self.assertEqual(res.status_code, 201, res.content)
        kinds = sorted(Sponsor.objects.values_list('kind', flat=True))
        self.assertEqual(kinds, ['partner', 'sponsor'])

    def test_a_website_and_socials_are_kept(self):
        res = self._create(sponsors=[{
            'name': 'Linked Brand',
            'website': 'https://example.com',
            'links': {'twitter': 'https://x.com/brand',
                      'instagram': 'https://instagram.com/brand'},
        }])
        self.assertEqual(res.status_code, 201, res.content)
        row = Sponsor.objects.get(name='Linked Brand')
        self.assertEqual(row.website, 'https://example.com')
        self.assertEqual(
            sorted(SponsorLink.objects.filter(sponsor=row)
                   .values_list('platform', flat=True)),
            ['instagram', 'twitter'])

    def test_an_empty_social_is_not_stored(self):
        """A blank box in the form must not become a link that goes nowhere."""
        res = self._create(sponsors=[{'name': 'Half Filled',
                                      'links': {'twitter': '', 'youtube': '  '}}])
        self.assertEqual(res.status_code, 201, res.content)
        row = Sponsor.objects.get(name='Half Filled')
        self.assertEqual(SponsorLink.objects.filter(sponsor=row).count(), 0)

    def test_the_detail_payload_separates_them(self):
        """The page shows two headings, so it should not have to filter."""
        self._create(sponsors=[{'name': 'Brand A'}], partners=[{'name': 'Club B'}])
        event = Event.objects.get(name='Lagos Meetup')
        res = self.client.get('/event/view-event/%s/' % (event.slug or event.event_id))
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        payload = data.get('event', data)
        self.assertEqual([s['name'] for s in payload['sponsors']], ['Brand A'])
        self.assertEqual([s['name'] for s in payload['partners']], ['Club B'])

    def test_a_supporter_with_no_name_is_skipped(self):
        res = self._create(partners=[{'name': '   '}, {'name': 'Real One'}])
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(list(Sponsor.objects.values_list('name', flat=True)),
                         ['Real One'])
