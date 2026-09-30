"""A person sets their own date of birth, once (inbox 351)."""
import datetime

from django.test import TestCase

from .models import UserProfile
from .tests_dm_policy import a_user


class BirthdayTests(TestCase):

    def setUp(self):
        self.me, self.auth = a_user('bday')
        self.other, _ = a_user('bdayother')

    def post(self, value, **extra):
        return self.client.post('/setting/birthday/', data=dict({'date_of_birth': value}, **extra),
                                content_type='application/json', **self.auth)

    def test_set_once_then_locked(self):
        res = self.post('2004-05-06')
        self.assertEqual((res.status_code, res.json()['data']['locked']), (200, True))
        again = self.post('1990-01-01')
        self.assertEqual((again.status_code, again.json()['code']), (409, 'BIRTHDAY_LOCKED'))
        self.assertEqual(UserProfile.objects.get(user=self.me).date_of_birth, datetime.date(2004, 5, 6))

    def test_read_back(self):
        self.post('2001-02-03')
        data = self.client.get('/setting/birthday/', **self.auth).json()['data']
        self.assertEqual(data, {'date_of_birth': '2001-02-03', 'locked': True})

    def test_impossible_dates_are_refused(self):
        future = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        for value in (future, '1850-01-01', 'yesterday', ''):
            res = self.post(value)
            self.assertEqual(res.json()['code'], 'BIRTHDAY_INVALID', value)

    def test_only_your_own(self):
        self.post('2000-01-01', user_id=self.other.user_id)
        self.assertIsNone(UserProfile.objects.filter(user=self.other).values_list(
            'date_of_birth', flat=True).first())

    def test_signed_out_is_refused(self):
        res = self.client.post('/setting/birthday/', data={'date_of_birth': '2000-01-01'},
                               content_type='application/json')
        self.assertIn(res.status_code, (401, 403))
