""""Tell me when it opens" on the roadmap pages (inbox 421)."""
import io
import uuid

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ModuleInterest, Notification, Users


def a_user(name):
    user = Users.objects.create(username='%s_%s' % (name, uuid.uuid4().hex[:4]),
                                email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
                                login_session_token=('tk%s' % uuid.uuid4().hex)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class InterestTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user, self.auth = a_user('waiter')

    def test_a_stranger_reads_none_and_cannot_ask(self):
        self.assertEqual(self.client.get('/roadmap/interest/').data['data']['modules'], [])
        res = self.client.post('/roadmap/interest/', {'module': 'anime'}, format='json')
        self.assertEqual(res.status_code, 401)
        self.assertEqual(ModuleInterest.objects.count(), 0)

    def test_asking_twice_is_one_row_and_it_can_be_undone(self):
        for _ in range(2):
            res = self.client.post('/roadmap/interest/', {'module': 'anime'}, format='json', **self.auth)
            self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['modules'], ['anime'])
        self.assertEqual(ModuleInterest.objects.count(), 1)
        res = self.client.delete('/roadmap/interest/', {'module': 'anime'}, format='json', **self.auth)
        self.assertEqual(res.data['data']['modules'], [])

    def test_an_unknown_module_is_refused(self):
        res = self.client.post('/roadmap/interest/', {'module': 'casino'}, format='json', **self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'UNKNOWN_MODULE')


class NotifyTests(TestCase):
    def test_everybody_who_asked_is_told_once(self):
        a, _ = a_user('a')
        b, _ = a_user('b')
        ModuleInterest.objects.create(user=a, module='anime')
        ModuleInterest.objects.create(user=b, module='shop')
        out = io.StringIO()
        call_command('notify_module_open', 'anime', stdout=out)
        self.assertIn('1 told', out.getvalue())
        self.assertEqual(Notification.objects.filter(user=a, category='roadmap').count(), 1)
        self.assertEqual(Notification.objects.filter(user=b).count(), 0)
        call_command('notify_module_open', 'anime', stdout=io.StringIO())
        self.assertEqual(Notification.objects.filter(user=a, category='roadmap').count(), 1)
