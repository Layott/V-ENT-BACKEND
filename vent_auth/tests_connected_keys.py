"""Connected keys: the organiser brings their own key (inbox 551, 25 October 2026).

"let create this in a way where they have to connect their own API keys to use
this features." The key is checked with Google before it is kept, encrypted at
rest, never sent back, and the battle royale reader uses the tournament's
organisation's key, else its creator's, and nobody else's.
"""
import itertools
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from vent_tournament import br_ocr
from vent_tournament.tests import client_for, make_tournament, make_user

from .connected_keys import key_for_tournament
from .models import ConnectedKey, Organization

_N = itertools.count(4900000)
KEY = 'AIza' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r'
OTHER = 'AIza' + 'Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i'


def person():
    return make_user(next(_N))


def google(status_code):
    """requests.get answering like Google with this status."""
    return mock.patch('requests.get', return_value=mock.Mock(status_code=status_code))


class ConnectTests(TestCase):
    def setUp(self):
        self.me = person()
        self.c = client_for(self.me)

    def test_a_good_key_is_kept_encrypted_and_never_sent_back(self):
        with google(200):
            res = self.c.post('/auth/connected-keys/', {'provider': 'gemini', 'key': KEY}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['data']['key']['last4'], KEY[-4:])
        self.assertNotIn(KEY, res.content.decode())
        row = ConnectedKey.objects.get(user=self.me)
        self.assertNotIn(KEY, row.secret)
        listed = self.c.get('/auth/connected-keys/')
        self.assertNotIn(KEY, listed.content.decode())
        self.assertEqual(listed.json()['data']['keys'][0]['provider'], 'gemini')

    def test_a_key_google_refuses_is_not_kept(self):
        with google(400):
            res = self.c.post('/auth/connected-keys/', {'provider': 'gemini', 'key': KEY}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'KEY_REJECTED')
        self.assertFalse(ConnectedKey.objects.exists())

    def test_a_key_of_the_wrong_shape_never_reaches_google(self):
        with mock.patch('requests.get') as called:
            res = self.c.post('/auth/connected-keys/', {'provider': 'gemini', 'key': 'sk-123'}, format='json')
        self.assertEqual(res.json()['code'], 'KEY_SHAPE_WRONG')
        called.assert_not_called()

    def test_google_unreachable_is_said_and_nothing_kept(self):
        import requests
        with mock.patch('requests.get', side_effect=requests.ConnectionError):
            res = self.c.post('/auth/connected-keys/', {'provider': 'gemini', 'key': KEY}, format='json')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()['code'], 'KEY_CHECK_UNAVAILABLE')
        self.assertFalse(ConnectedKey.objects.exists())

    def test_connecting_again_replaces_and_removing_removes(self):
        with google(200):
            self.c.post('/auth/connected-keys/', {'provider': 'gemini', 'key': KEY}, format='json')
            self.c.post('/auth/connected-keys/', {'provider': 'gemini', 'key': OTHER}, format='json')
        self.assertEqual(ConnectedKey.objects.filter(user=self.me).count(), 1)
        self.assertEqual(ConnectedKey.objects.get(user=self.me).last4, OTHER[-4:])
        self.assertEqual(self.c.delete('/auth/connected-keys/gemini/').status_code, 200)
        self.assertFalse(ConnectedKey.objects.exists())

    def test_nobody_signed_in_is_refused(self):
        self.assertIn(APIClient().get('/auth/connected-keys/').status_code, (401, 403))


class OrganisationKeyTests(TestCase):
    def setUp(self):
        self.owner = person()
        self.org = Organization.objects.create(org_name='Keys Org %d' % next(_N),
                                               org_creator=self.owner, org_owner=self.owner)

    def test_a_stranger_cannot_connect_or_remove_an_organisations_key(self):
        stranger = person()
        with google(200):
            res = client_for(stranger).post('/auth/connected-keys/', {
                'provider': 'gemini', 'key': KEY, 'organization': self.org.slug}, format='json')
        self.assertEqual(res.status_code, 403)
        with google(200):
            client_for(self.owner).post('/auth/connected-keys/', {
                'provider': 'gemini', 'key': KEY, 'organization': self.org.slug}, format='json')
        res = client_for(stranger).delete('/auth/connected-keys/gemini/?organization=%s' % self.org.slug)
        self.assertEqual(res.status_code, 403)
        self.assertTrue(ConnectedKey.objects.filter(organization=self.org).exists())
        self.assertNotIn(KEY[-4:], client_for(stranger).get('/auth/connected-keys/').content.decode())

    def test_the_tournament_reads_with_its_organisations_key_then_its_creators(self):
        creator = person()
        t = make_tournament(creator)
        self.assertIsNone(key_for_tournament(t, 'gemini'))
        self.assertFalse(br_ocr.available(t))
        with google(200):
            client_for(creator).post('/auth/connected-keys/', {'provider': 'gemini', 'key': OTHER}, format='json')
        self.assertEqual(key_for_tournament(t, 'gemini'), OTHER)
        self.assertEqual(br_ocr.engine_name(t), 'gemini')
        t.tournament_organization = self.org
        t.save(update_fields=['tournament_organization'])
        with google(200):
            client_for(self.owner).post('/auth/connected-keys/', {
                'provider': 'gemini', 'key': KEY, 'organization': self.org.slug}, format='json')
        self.assertEqual(key_for_tournament(t, 'gemini'), KEY)

    def test_somebody_elses_key_is_never_used(self):
        t = make_tournament(person())
        with google(200):
            client_for(person()).post('/auth/connected-keys/', {'provider': 'gemini', 'key': KEY}, format='json')
        self.assertIsNone(key_for_tournament(t, 'gemini'))
