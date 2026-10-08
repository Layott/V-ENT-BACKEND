""""Indexable in search" does what its switch says (inbox 399).

It was saved as `search_indexable` and read by nothing. Now: the public profile
carries `indexable` for the page to set noindex, and user search leaves the
person out unless their exact username is typed, so a friend can still find
them to message or pay.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import UserSetting, Users


def _user(username):
    user = Users.objects.create(
        username=username, email='%s@vent.test' % username, full_name=username.title(),
        login_session_token=('tk-%s' % username)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class IndexableTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.hidden = _user('quietplayer')
        self.shown = _user('quietlyloud')
        self.viewer = _user('lookerx')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.hidden.login_session_token)
        res = self.client.post('/setting/privacy/update/', {'indexable': False}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % self.viewer.login_session_token)

    def names(self, q):
        res = self.client.get('/user/search/', {'q': q})
        self.assertEqual(res.status_code, 200, res.data)
        return [u['username'] for u in res.data['data']['users']]

    def test_the_profile_says_so(self):
        self.assertFalse(self.client.get('/user/quietplayer/profile/').data['data']['indexable'])
        self.assertTrue(self.client.get('/user/quietlyloud/profile/').data['data']['indexable'])

    def test_search_leaves_them_out_unless_the_exact_name_is_typed(self):
        self.assertEqual(self.names('quiet'), ['quietlyloud'])
        self.assertIn('quietplayer', self.names('@quietplayer'))

    def test_a_name_picker_finds_them_from_a_rough_spelling(self):
        """Inbox 416: a teammate typing a name finds them; the switch is about search."""
        res = self.client.get('/user/search/', {'q': 'quiet', 'purpose': 'pick'})
        self.assertIn('quietplayer', [u['username'] for u in res.data['data']['users']])
        res = self.client.get('/user/search/', {'q': 'quietplayr', 'purpose': 'pick'})
        self.assertIn('quietplayer', [u['username'] for u in res.data['data']['users']])

    def test_a_stranger_asking_as_a_picker_gets_the_public_rule(self):
        self.client.credentials()
        res = self.client.get('/user/search/', {'q': 'quiet', 'purpose': 'pick'})
        self.assertNotIn('quietplayer', [u['username'] for u in res.data['data']['users']])

    def test_a_row_saved_under_the_old_name_is_obeyed(self):
        UserSetting.objects.filter(user=self.hidden).update(data={'privacy': {'search_indexable': False}})
        self.assertFalse(self.client.get('/user/quietplayer/profile/').data['data']['indexable'])
