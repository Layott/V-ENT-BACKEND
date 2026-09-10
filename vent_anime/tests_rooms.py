"""Reading rooms: who gets in, who turns the page, and what the feed carries.

The two that matter most are the ones a room gets wrong in the wild: somebody
removed by the host walking back in through the address, and a page turn by
somebody who is not driving.
"""
import uuid

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import UserWallet, Users

from . import rooms
from .models import (Chapter, ReadingRoom, RoomAnnotation, RoomMember,
                     RoomMessage, RoomSignal, Series)


def make_user(i):
    user = Users.objects.create(
        username='ar%s' % i, email='ar%s@test.co' % i,
        login_session_token='art%s' % str(i).zfill(11),
        login_session_created_at=timezone.now(), is_active=True)
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user,
                              wallet_balance=0)
    return user


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return c


@override_settings(ANIME_ENABLED=True)
class RoomBase(TestCase):

    def setUp(self):
        self.host = make_user(1)
        self.friend = make_user(2)
        self.stranger = make_user(3)
        self.series = Series.objects.create(
            author=self.host, title='Read together', visibility='public')
        self.chapter = Chapter.objects.create(series=self.series, number=1)

    def make_room(self, **kwargs):
        res = client_for(self.host).post('/anime/rooms/', {
            'series': self.series.slug, 'chapter': self.chapter.slug,
            'name': 'Friday night', **kwargs}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return ReadingRoom.objects.get(token=res.data['data']['token'])


class MakingAndJoiningTests(RoomBase):

    def test_a_room_with_no_chapter_named_opens_on_the_first(self):
        """Found on the walk: a room with nothing open has nothing to read."""
        res = client_for(self.host).post('/anime/rooms/', {
            'series': self.series.slug, 'name': 'No chapter named'},
            format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['data']['chapter'], self.chapter.slug)

    def test_a_room_is_addressed_by_a_token_not_a_number(self):
        room = self.make_room()
        self.assertTrue(room.token.startswith('rr_'))
        self.assertNotIn(str(room.room_id), room.token)

    def test_a_public_room_lets_anybody_in(self):
        room = self.make_room(privacy='public')
        res = client_for(self.stranger).post(
            '/anime/rooms/%s/join/' % room.token, {}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_a_private_room_needs_an_invitation(self):
        room = self.make_room(privacy='private')
        res = client_for(self.stranger).post(
            '/anime/rooms/%s/join/' % room.token, {}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'INVITE_ONLY')

    def test_an_invitation_opens_a_private_room(self):
        room = self.make_room(privacy='private')
        client_for(self.host).post('/anime/rooms/%s/invite/' % room.token,
                                   {'username': self.friend.username},
                                   format='json')
        res = client_for(self.friend).post(
            '/anime/rooms/%s/join/' % room.token, {}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_a_password_room_checks_the_password(self):
        room = self.make_room(privacy='password', password='letmein')
        bad = client_for(self.stranger).post(
            '/anime/rooms/%s/join/' % room.token, {'password': 'nope'},
            format='json')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data['code'], 'WRONG_PASSWORD')
        good = client_for(self.stranger).post(
            '/anime/rooms/%s/join/' % room.token, {'password': 'letmein'},
            format='json')
        self.assertEqual(good.status_code, 200)

    def test_the_password_is_never_stored_in_the_clear(self):
        room = self.make_room(privacy='password', password='letmein')
        self.assertNotIn('letmein', room.password_hash)
        self.assertTrue(room.password_hash)

    def test_a_password_room_with_no_password_is_refused_at_creation(self):
        res = client_for(self.host).post('/anime/rooms/', {
            'series': self.series.slug, 'privacy': 'password'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'PASSWORD_REQUIRED')

    def test_private_rooms_are_not_listed(self):
        self.make_room(privacy='private')
        res = client_for(self.stranger).get('/anime/rooms/')
        self.assertEqual(res.data['data']['rooms'], [])

    def test_somebody_removed_cannot_walk_back_in(self):
        """Otherwise the removal is decorative."""
        room = self.make_room(privacy='public')
        client_for(self.friend).post('/anime/rooms/%s/join/' % room.token, {},
                                     format='json')
        client_for(self.host).post('/anime/rooms/%s/remove/' % room.token,
                                   {'username': self.friend.username},
                                   format='json')
        res = client_for(self.friend).post(
            '/anime/rooms/%s/join/' % room.token, {}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'REMOVED_FROM_ROOM')

    def test_only_the_host_removes(self):
        room = self.make_room(privacy='public')
        client_for(self.friend).post('/anime/rooms/%s/join/' % room.token, {},
                                     format='json')
        res = client_for(self.friend).post(
            '/anime/rooms/%s/remove/' % room.token,
            {'username': self.host.username}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_a_closed_room_takes_nobody(self):
        room = self.make_room(privacy='public')
        client_for(self.host).post('/anime/rooms/%s/close/' % room.token, {},
                                   format='json')
        res = client_for(self.stranger).post(
            '/anime/rooms/%s/join/' % room.token, {}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'ROOM_CLOSED')


class TurningThePageTests(RoomBase):

    def setUp(self):
        super().setUp()
        self.room = self.make_room(privacy='public')
        client_for(self.friend).post('/anime/rooms/%s/join/' % self.room.token,
                                     {}, format='json')

    def test_the_host_turns_the_page_and_everybody_follows(self):
        res = client_for(self.host).post(
            '/anime/rooms/%s/page/' % self.room.token, {'page': 4},
            format='json')
        self.assertEqual(res.status_code, 200)
        feed = client_for(self.friend).get(
            '/anime/rooms/%s/feed/' % self.room.token)
        self.assertEqual(feed.data['data']['page_number'], 4)

    def test_somebody_who_is_not_driving_cannot(self):
        res = client_for(self.friend).post(
            '/anime/rooms/%s/page/' % self.room.token, {'page': 9},
            format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'NOT_THE_DRIVER')
        self.room.refresh_from_db()
        self.assertEqual(self.room.page_number, 1)

    def test_control_can_be_handed_over(self):
        client_for(self.host).post('/anime/rooms/%s/control/' % self.room.token,
                                   {'username': self.friend.username},
                                   format='json')
        res = client_for(self.friend).post(
            '/anime/rooms/%s/page/' % self.room.token, {'page': 9},
            format='json')
        self.assertEqual(res.status_code, 200)

    def test_anyone_control_lets_a_member_turn_it(self):
        self.room.control = 'anyone'
        self.room.save(update_fields=['control'])
        res = client_for(self.friend).post(
            '/anime/rooms/%s/page/' % self.room.token, {'page': 3},
            format='json')
        self.assertEqual(res.status_code, 200)

    def test_the_version_goes_up_on_a_change_and_not_on_a_poll(self):
        """Otherwise the feed never settles and the backoff never engages."""
        before = ReadingRoom.objects.get(pk=self.room.pk).version
        client_for(self.friend).get('/anime/rooms/%s/feed/' % self.room.token)
        self.assertEqual(ReadingRoom.objects.get(pk=self.room.pk).version,
                         before)
        client_for(self.host).post('/anime/rooms/%s/page/' % self.room.token,
                                   {'page': 2}, format='json')
        self.assertGreater(ReadingRoom.objects.get(pk=self.room.pk).version,
                           before)


class TalkingAndDrawingTests(RoomBase):

    def setUp(self):
        super().setUp()
        self.room = self.make_room(privacy='public')
        client_for(self.friend).post('/anime/rooms/%s/join/' % self.room.token,
                                     {}, format='json')

    def test_chat_reaches_the_feed(self):
        client_for(self.friend).post('/anime/rooms/%s/chat/' % self.room.token,
                                     {'body': 'that panel'}, format='json')
        feed = client_for(self.host).get(
            '/anime/rooms/%s/feed/' % self.room.token)
        bodies = [m['body'] for m in feed.data['data']['messages']]
        self.assertIn('that panel', bodies)

    def test_a_reaction_carries_the_page_it_was_aimed_at(self):
        client_for(self.host).post('/anime/rooms/%s/page/' % self.room.token,
                                   {'page': 5}, format='json')
        client_for(self.friend).post('/anime/rooms/%s/chat/' % self.room.token,
                                     {'emoji': 'fire'}, format='json')
        row = RoomMessage.objects.get(kind='reaction')
        self.assertEqual(row.page_number, 5)

    def test_an_empty_message_is_refused(self):
        res = client_for(self.friend).post(
            '/anime/rooms/%s/chat/' % self.room.token, {}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_somebody_outside_the_room_cannot_talk_in_it(self):
        res = client_for(self.stranger).post(
            '/anime/rooms/%s/chat/' % self.room.token, {'body': 'hello'},
            format='json')
        self.assertEqual(res.status_code, 403)

    def test_an_annotation_is_seen_by_everybody(self):
        client_for(self.friend).post(
            '/anime/rooms/%s/annotations/' % self.room.token,
            {'kind': 'note', 'x': 0.2, 'y': 0.3, 'text': 'look here'},
            format='json')
        feed = client_for(self.host).get(
            '/anime/rooms/%s/feed/' % self.room.token)
        texts = [a['text'] for a in feed.data['data']['annotations']]
        self.assertIn('look here', texts)

    def test_an_annotation_is_positioned_in_fractions_not_pixels(self):
        client_for(self.friend).post(
            '/anime/rooms/%s/annotations/' % self.room.token,
            {'kind': 'highlight', 'x': 0.5, 'y': 0.5, 'w': 0.25, 'h': 0.1},
            format='json')
        row = RoomAnnotation.objects.get()
        self.assertEqual((row.x, row.w), (0.5, 0.25))

    def test_a_drawing_keeps_its_path(self):
        client_for(self.friend).post(
            '/anime/rooms/%s/annotations/' % self.room.token,
            {'kind': 'drawing', 'path': [[0.1, 0.1], [0.2, 0.2]]},
            format='json')
        self.assertEqual(RoomAnnotation.objects.get().path,
                         [[0.1, 0.1], [0.2, 0.2]])

    def test_the_author_can_remove_their_own_annotation(self):
        client_for(self.friend).post(
            '/anime/rooms/%s/annotations/' % self.room.token,
            {'kind': 'note', 'text': 'oops'}, format='json')
        row = RoomAnnotation.objects.get()
        res = client_for(self.friend).delete(
            '/anime/rooms/%s/annotations/' % self.room.token,
            {'id': row.annotation_id}, format='json')
        self.assertEqual(res.status_code, 200)
        row.refresh_from_db()
        self.assertTrue(row.is_removed)

    def test_somebody_else_cannot_remove_it(self):
        client_for(self.friend).post(
            '/anime/rooms/%s/annotations/' % self.room.token,
            {'kind': 'note', 'text': 'mine'}, format='json')
        row = RoomAnnotation.objects.get()
        other = make_user(9)
        client_for(other).post('/anime/rooms/%s/join/' % self.room.token, {},
                               format='json')
        res = client_for(other).delete(
            '/anime/rooms/%s/annotations/' % self.room.token,
            {'id': row.annotation_id}, format='json')
        self.assertEqual(res.status_code, 403)


class VoiceSignallingTests(RoomBase):

    def setUp(self):
        super().setUp()
        self.room = self.make_room(privacy='public')
        client_for(self.friend).post('/anime/rooms/%s/join/' % self.room.token,
                                     {}, format='json')

    def test_a_signal_reaches_the_person_it_is_addressed_to(self):
        client_for(self.host).post('/anime/rooms/%s/signal/' % self.room.token,
                                   {'to': self.friend.username, 'kind': 'offer',
                                    'payload': {'sdp': 'x'}}, format='json')
        feed = client_for(self.friend).get(
            '/anime/rooms/%s/feed/' % self.room.token)
        self.assertEqual(len(feed.data['data']['signals']), 1)
        self.assertEqual(feed.data['data']['signals'][0]['kind'], 'offer')

    def test_it_does_not_reach_anybody_else(self):
        third = make_user(10)
        client_for(third).post('/anime/rooms/%s/join/' % self.room.token, {},
                               format='json')
        client_for(self.host).post('/anime/rooms/%s/signal/' % self.room.token,
                                   {'to': self.friend.username, 'kind': 'offer'},
                                   format='json')
        feed = client_for(third).get(
            '/anime/rooms/%s/feed/' % self.room.token)
        self.assertEqual(feed.data['data']['signals'], [])

    def test_a_signal_is_consumed_once(self):
        """The table is a letterbox, not a log."""
        client_for(self.host).post('/anime/rooms/%s/signal/' % self.room.token,
                                   {'to': self.friend.username, 'kind': 'ice'},
                                   format='json')
        client_for(self.friend).get('/anime/rooms/%s/feed/' % self.room.token)
        again = client_for(self.friend).get(
            '/anime/rooms/%s/feed/' % self.room.token)
        self.assertEqual(again.data['data']['signals'], [])
        self.assertEqual(RoomSignal.objects.count(), 0)

    def test_a_made_up_signal_kind_is_refused(self):
        res = client_for(self.host).post(
            '/anime/rooms/%s/signal/' % self.room.token,
            {'to': self.friend.username, 'kind': 'nonsense'}, format='json')
        self.assertEqual(res.status_code, 400)


class AnalyticsTests(RoomBase):

    def test_the_session_reports_what_it_did(self):
        room = self.make_room(privacy='public')
        client_for(self.friend).post('/anime/rooms/%s/join/' % room.token, {},
                                     format='json')
        client_for(self.host).post('/anime/rooms/%s/page/' % room.token,
                                   {'page': 3}, format='json')
        client_for(self.friend).post('/anime/rooms/%s/chat/' % room.token,
                                     {'body': 'ha'}, format='json')
        client_for(self.friend).post('/anime/rooms/%s/chat/' % room.token,
                                     {'emoji': 'fire', 'page': 3},
                                     format='json')

        res = client_for(self.host).get(
            '/anime/rooms/%s/analytics/' % room.token)
        data = res.data['data']
        self.assertEqual(data['people'], 2)
        self.assertEqual(data['messages'], 1)
        self.assertEqual(data['reactions'], 1)
        self.assertEqual(data['most_reacted_page']['page'], 3)

    def test_only_the_host_sees_it(self):
        room = self.make_room(privacy='public')
        client_for(self.friend).post('/anime/rooms/%s/join/' % room.token, {},
                                     format='json')
        res = client_for(self.friend).get(
            '/anime/rooms/%s/analytics/' % room.token)
        self.assertEqual(res.status_code, 403)

    def test_closing_the_room_answers_with_the_analytics(self):
        room = self.make_room(privacy='public')
        res = client_for(self.host).post('/anime/rooms/%s/close/' % room.token,
                                         {}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIn('minutes', res.data['data'])
