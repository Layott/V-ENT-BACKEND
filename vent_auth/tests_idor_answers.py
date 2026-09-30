"""A private record answers a stranger exactly as a missing one does (R88, inbox 398).

The sweep of every id-taking address on 30 September 2026 (tools/idor-probe.py)
found three kinds of private record that answered 403 for a real key and 404
for a made-up one, which tells a stranger which keys are real, and one door
that read the message before checking whose conversation it was.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Conversation, DirectMessage, KYCDocument, Users


def _user(username):
    user = Users.objects.create(
        username=username, email='%s@vent.test' % username, full_name=username.title(),
        login_session_token=('tk-%s' % username)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class PrivateRecordAnswerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ada, self.bem, self.cy = _user('ada'), _user('bem'), _user('cy')

    def as_(self, user):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)

    def test_a_strangers_conversation_is_not_found_before_the_message_is_read(self):
        self.as_(self.ada)
        made = self.client.post('/dm/new/send/', {'username': 'bem', 'body': 'hi'}, format='json')
        self.assertEqual(made.status_code, 201, made.data)
        key = Conversation.objects.get().slug
        self.as_(self.cy)
        for body in ({}, {'body': 'let me in'}):
            real = self.client.post('/dm/%s/send/' % key, body, format='json')
            missing = self.client.post('/dm/c_nothing_here/send/', body, format='json')
            self.assertEqual(real.status_code, 404, real.data)
            self.assertEqual(missing.status_code, 404)
        self.assertEqual(DirectMessage.objects.count(), 1)
        self.assertEqual(self.client.get('/dm/%s/' % key).status_code, 404)

    def test_somebody_elses_identity_document_is_not_found(self):
        doc = KYCDocument.objects.create(user=self.ada, document_type='passport',
                                         document_image='kyc/ada.png')
        self.as_(self.cy)
        real = self.client.get('/auth/kyc/document/%d/' % doc.pk)
        missing = self.client.get('/auth/kyc/document/%d/' % (doc.pk + 999))
        self.assertEqual(real.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(real.data['code'], missing.data['code'])
