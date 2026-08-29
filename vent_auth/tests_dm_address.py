"""A message notification has to open the message.

CEO, 29 August 2026: "when you click on a message from your notifications it
doesn't take you to the message." The notification's link was the literal string
`/community/dm`, with no conversation on the end of it, and nothing anywhere
asserted otherwise - a wrong constant in a string field is invisible to every
test that only checks a message was created.

So these tests read the link and follow it, and they check the address is the
conversation's public token rather than its primary key.
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Conversation, Notification, Users


def _user(username):
    user = Users.objects.create(
        username=username, email='%s@vent.test' % username,
        full_name=username.title(),
        login_session_token=('tk-%s' % username)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user


def _token(user):
    return user.login_session_token


class DmAddressTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sender = _user('ada')
        self.recipient = _user('bem')

    def _send(self, body='hello there'):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(self.sender)}')
        return self.client.post(
            '/dm/new/send/', {'username': 'bem', 'body': body}, format='json')

    def test_conversation_gets_an_opaque_token_not_the_primary_key(self):
        res = self._send()
        self.assertEqual(res.status_code, 201, res.data)
        convo = Conversation.objects.get()
        self.assertTrue(convo.slug, 'conversation has no public address')
        self.assertTrue(convo.slug.startswith('d_'), convo.slug)
        self.assertNotEqual(convo.slug, str(convo.id))

    def test_the_notification_links_to_the_conversation(self):
        self._send()
        note = Notification.objects.filter(user=self.recipient).first()
        self.assertIsNotNone(note, 'the recipient was told nothing')
        convo = Conversation.objects.get()
        self.assertEqual(note.link, f'/community/dm/{convo.slug}')
        # The bare address is the list, and is what the old link was.
        self.assertNotEqual(note.link, '/community/dm')

    def test_following_that_link_reaches_the_message(self):
        self._send('meet me at the venue')
        note = Notification.objects.filter(user=self.recipient).first()
        address = note.link.rsplit('/', 1)[-1]

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(self.recipient)}')
        res = self.client.get(f'/dm/{address}/')
        self.assertEqual(res.status_code, 200, res.data)
        bodies = [m['body'] for m in res.data['data']['messages']]
        self.assertIn('meet me at the venue', bodies)

    def test_the_numeric_id_still_resolves_for_links_already_shared(self):
        self._send()
        convo = Conversation.objects.get()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(self.recipient)}')
        res = self.client.get(f'/dm/{convo.id}/')
        self.assertEqual(res.status_code, 200, res.data)

    def test_somebody_else_cannot_read_it_with_the_token(self):
        self._send()
        convo = Conversation.objects.get()
        stranger = _user('cee')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(stranger)}')
        res = self.client.get(f'/dm/{convo.slug}/')
        self.assertEqual(res.status_code, 403, res.data)

    def test_an_unknown_token_is_not_found_rather_than_a_crash(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(self.recipient)}')
        res = self.client.get('/dm/d_notarealtoken/')
        self.assertEqual(res.status_code, 404, res.data)

    def test_the_list_carries_the_address_the_page_links_to(self):
        self._send()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(self.recipient)}')
        res = self.client.get('/dm/list/')
        self.assertEqual(res.status_code, 200, res.data)
        row = res.data['data']['conversations'][0]
        self.assertTrue(row.get('slug'), 'the list has no address to link to')

    def test_replying_into_the_conversation_by_token(self):
        self._send()
        convo = Conversation.objects.get()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(self.recipient)}')
        res = self.client.post(
            f'/dm/{convo.slug}/send/', {'body': 'on my way'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(convo.messages.count(), 2)

    def test_the_token_does_not_change_when_the_conversation_is_touched(self):
        self._send()
        convo = Conversation.objects.get()
        first = convo.slug
        self._send('again')
        convo.refresh_from_db()
        self.assertEqual(convo.slug, first, 'the address moved under a shared link')
