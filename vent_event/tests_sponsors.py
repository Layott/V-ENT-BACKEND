"""Changing who is behind an event, after it exists.

The model and the event page were both complete and there was no way to write:
a sponsor who signed on in week three could not be added, and one who pulled out
could not be removed. This pins the way in.
"""
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Users

from .models import Event, Sponsor


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('s-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


# A one-pixel PNG, so the upload path is exercised without shipping a fixture.
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
    '890000000a49444154789c6300010000050001' '0d0a2db4' '0000000049454e44ae426082')


class EventSponsorTests(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('sp_org')
        self.stranger, self.stranger_auth = a_user('sp_other')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Sponsor Probe', creator=self.organiser, event_type='physical',
            desc='x', entry_fee=0,
            start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))

    def url(self):
        return '/event/%s/sponsors/manage/' % self.event.event_id

    def one(self, sponsor_id):
        return '/event/%s/sponsors/%s/' % (self.event.event_id, sponsor_id)

    def test_an_organiser_adds_a_sponsor(self):
        res = self.client.post(self.url(), data={
            'name': 'CADE ESPORTS', 'website': 'https://cadeesport.com/',
        }, **self.auth)
        self.assertEqual(res.status_code, 201, res.json())
        row = res.json()['data']['sponsor']
        self.assertEqual(row['name'], 'CADE ESPORTS')
        self.assertEqual(row['kind'], 'sponsor')

    def test_a_partner_is_the_same_model_with_another_word(self):
        res = self.client.post(self.url(), data={'name': 'KON10DR',
                                                 'kind': 'partner'}, **self.auth)
        self.assertEqual(res.status_code, 201, res.json())
        self.assertEqual(res.json()['data']['sponsor']['kind'], 'partner')
        self.assertEqual(Sponsor.objects.get(name='KON10DR').kind, 'partner')

    def test_an_unknown_kind_falls_back_rather_than_refusing(self):
        res = self.client.post(self.url(), data={'name': 'X', 'kind': 'wizard'},
                               **self.auth)
        self.assertEqual(res.json()['data']['sponsor']['kind'], 'sponsor')

    def test_a_nameless_sponsor_is_refused_by_name(self):
        res = self.client.post(self.url(), data={'name': '  '}, **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['field'], 'name')

    def test_a_stranger_cannot_add_one(self):
        res = self.client.post(self.url(), data={'name': 'Mine now'},
                               **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'ONLY_EVENT_ORGANIZER_CAN')

    def test_a_stranger_cannot_read_the_editing_list(self):
        res = self.client.get(self.url(), **self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_they_come_back_in_the_order_they_were_added(self):
        for name in ('First', 'Second', 'Third'):
            self.client.post(self.url(), data={'name': name}, **self.auth)
        res = self.client.get(self.url(), **self.auth)
        self.assertEqual([s['name'] for s in res.json()['data']['sponsors']],
                         ['First', 'Second', 'Third'])

    def test_a_name_can_be_corrected(self):
        made = self.client.post(self.url(), data={'name': 'Bayse'},
                                **self.auth).json()['data']['sponsor']
        res = self.client.patch(self.one(made['id']),
                                data={'name': 'BAYSE MARKETS'},
                                content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.assertEqual(Sponsor.objects.get(pk=made['id']).name, 'BAYSE MARKETS')

    def test_correcting_a_name_does_not_wipe_the_logo(self):
        # A form that submits every field would otherwise clear the artwork
        # every time somebody fixed a spelling.
        made = self.client.post(self.url(), data={
            'name': 'Paga',
            'logo': SimpleUploadedFile('paga.png', PNG, content_type='image/png'),
        }, **self.auth).json()['data']['sponsor']
        self.assertTrue(made['logo'])

        self.client.patch(self.one(made['id']),
                          data={'name': 'PAGA', 'logo_url': ''},
                          content_type='application/json', **self.auth)
        self.assertTrue(Sponsor.objects.get(pk=made['id']).logo)

    def test_a_sponsor_can_be_removed(self):
        made = self.client.post(self.url(), data={'name': 'Gone'},
                                **self.auth).json()['data']['sponsor']
        res = self.client.delete(self.one(made['id']), **self.auth)
        self.assertEqual(res.status_code, 200, res.json())
        self.assertFalse(Sponsor.objects.filter(pk=made['id']).exists())

    def test_a_stranger_cannot_remove_one(self):
        made = self.client.post(self.url(), data={'name': 'Safe'},
                                **self.auth).json()['data']['sponsor']
        res = self.client.delete(self.one(made['id']), **self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Sponsor.objects.filter(pk=made['id']).exists())

    def test_a_sponsor_from_another_event_is_not_reachable(self):
        other, _ = a_user('sp_third')
        now = timezone.now()
        elsewhere = Event.objects.create(
            name='Elsewhere', creator=other, event_type='physical', desc='x',
            entry_fee=0, start_date=now + timedelta(days=5),
            end_date=now + timedelta(days=5, hours=4),
            reg_start_date=now - timedelta(days=1),
            reg_end_date=now + timedelta(days=4))
        theirs = Sponsor.objects.create(event=elsewhere, name='Theirs')
        res = self.client.delete(self.one(theirs.sponsor_id), **self.auth)
        self.assertEqual(res.status_code, 404)
        self.assertTrue(Sponsor.objects.filter(pk=theirs.sponsor_id).exists())
