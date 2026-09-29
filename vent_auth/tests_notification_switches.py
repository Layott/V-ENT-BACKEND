"""Every notification switch works as each person set it.

CEO, 30 September 2026: "All the switches must work as listed or shown in
their profiles. If they put on or off something for discord or push or email
or in app, it must work as each user set it."
"""
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Notification, PushSubscription, UserSetting
from .tests_dm_policy import a_user
from .views_notifications import create_notification

CHANNELS = 'vent_auth.views_notifications.'


def set_switches(user, **switches):
    row, _ = UserSetting.objects.get_or_create(user=user)
    data = dict(row.data or {})
    data['notifications'] = {**(data.get('notifications') or {}), **switches}
    row.data = data
    row.save()


class Deliveries:
    """Patch the three outward channels and record what each was asked to send."""

    def __enter__(self):
        self.patches = [mock.patch(CHANNELS + name) for name in
                        ('_deliver_by_email', '_deliver_by_push', '_deliver_to_discord')]
        self.email, self.push, self.discord = [p.start() for p in self.patches]
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


class SwitchesDecideDeliveryTests(TestCase):

    def setUp(self):
        self.user, self.auth = a_user('switcher')

    def test_defaults_inbox_email_push_but_not_discord(self):
        with Deliveries() as d:
            row = create_notification(self.user, 'tournament', 'Round 2 is up')
        self.assertTrue(row.in_inbox)
        self.assertEqual((d.email.call_count, d.push.call_count, d.discord.call_count), (1, 1, 0))

    def test_each_channel_obeys_its_own_switch(self):
        set_switches(self.user, tournaments__email=False, tournaments__push=False,
                     tournaments__discord=True, tournaments__in_app=False)
        with Deliveries() as d:
            row = create_notification(self.user, 'match', 'Your match starts soon')
        self.assertIsNotNone(row, 'a caller must still be able to tell it was handled')
        self.assertFalse(row.in_inbox)
        self.assertEqual((d.email.call_count, d.push.call_count, d.discord.call_count), (0, 0, 1))

    def test_a_row_switch_touches_only_its_own_row(self):
        set_switches(self.user, tournaments__email=False)
        with Deliveries() as d:
            create_notification(self.user, 'event', 'Doors open at 6')
        self.assertEqual(d.email.call_count, 1)

    def test_switched_off_inbox_keeps_it_out_of_the_list_and_the_bell(self):
        set_switches(self.user, followers__in_app=False)
        with Deliveries():
            create_notification(self.user, 'follower', '@a followed you')
            create_notification(self.user, 'tournament', 'Round 2 is up')
        res = self.client.get('/auth/notifications/', **self.auth)
        titles = [n['title'] for n in res.json()['data']['notifications']]
        self.assertEqual(titles, ['Round 2 is up'])
        count = self.client.get('/auth/notifications/unread-count/', **self.auth).json()['data']
        self.assertEqual(count['unread_count'], 1)

    def test_account_notices_always_reach_inbox_and_email(self):
        set_switches(self.user, account__in_app=False, account__email=False)
        with Deliveries() as d:
            row = create_notification(self.user, 'payout', 'Your payout was refused')
        self.assertTrue(row.in_inbox)
        self.assertEqual(d.email.call_count, 1)

    def test_email_false_means_the_caller_sent_its_own(self):
        with Deliveries() as d:
            create_notification(self.user, 'event', '2 tickets', email=False)
        self.assertEqual(d.email.call_count, 0)

    def test_a_choice_on_the_old_grid_still_counts(self):
        set_switches(self.user, tournament_result__email=False, tournament_invite__email=False)
        with Deliveries() as d:
            create_notification(self.user, 'tournament', 'x')
        self.assertEqual(d.email.call_count, 0)

    @override_settings(MARKETPLACE_ENABLED=False)
    def test_a_closed_module_sends_nothing_and_is_not_offered(self):
        with Deliveries() as d:
            create_notification(self.user, 'marketplace', 'Order shipped')
        self.assertEqual(d.email.call_count + d.push.call_count, 0)
        grid = self.client.get('/setting/notifications/grid/', **self.auth).json()['data']
        self.assertNotIn('marketplace', [r['id'] for r in grid['rows']])
        self.assertEqual(grid['coming_soon'], ['sms'])


class SavingSwitchesTests(TestCase):

    def setUp(self):
        self.user, self.auth = a_user('saver')

    def post(self, body):
        return self.client.post('/setting/notifications/update/', data=body,
                                content_type='application/json', **self.auth)

    def test_only_real_switches_are_stored(self):
        res = self.post({'events__email': False, 'account__email': False, 'events__sms': True,
                         'nonsense__push': True, 'events__push': 'yes', 'is_admin': True})
        self.assertEqual(res.status_code, 200)
        stored = UserSetting.objects.get(user=self.user).data['notifications']
        self.assertEqual(stored, {'events__email': False})
        self.assertEqual(len(res.json()['data']['ignored']), 5)

    def test_the_grid_reads_back_what_was_saved(self):
        self.post({'wallet__push': False})
        grid = self.client.get('/setting/notifications/grid/', **self.auth).json()['data']
        wallet = next(r for r in grid['rows'] if r['id'] == 'wallet')
        self.assertFalse(wallet['channels']['push'])
        account = next(r for r in grid['rows'] if r['id'] == 'account')
        self.assertEqual(sorted(account['locked']), ['email', 'in_app'])

    def test_signed_out_is_refused(self):
        self.assertIn(self.client.get('/setting/notifications/grid/').status_code, (401, 403))


class FollowersAndMessagesTests(TestCase):

    def setUp(self):
        self.me, self.my_auth = a_user('followed')
        self.them, self.their_auth = a_user('follower1')

    def test_a_new_follower_is_told_once(self):
        with Deliveries():
            for _ in range(2):
                self.client.post('/auth/follow/user/%s/' % self.me.username, **self.their_auth)
                self.client.delete('/auth/follow/user/%s/' % self.me.username, **self.their_auth)
            self.client.post('/auth/follow/user/%s/' % self.me.username, **self.their_auth)
        rows = Notification.objects.filter(user=self.me, category='follower')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows[0].link, '/u/follower1')

    def test_messages_are_their_own_row_and_a_burst_is_one_ping(self):
        with Deliveries() as d:
            for body in ('hi', 'are you there', 'hello?'):
                self.client.post('/dm/new/send/', data={'username': self.me.username, 'body': body},
                                 content_type='application/json', **self.their_auth)
        rows = Notification.objects.filter(user=self.me, category='dm')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(d.push.call_count, 1)


@override_settings()
class PushTests(TestCase):

    def setUp(self):
        self.user, self.auth = a_user('pusher')
        self.other, self.other_auth = a_user('pusher2')
        self.env = mock.patch.dict('os.environ', {'VAPID_PUBLIC_KEY': 'pub', 'VAPID_PRIVATE_KEY': 'priv'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def subscribe(self, auth, endpoint='https://push.example/abc'):
        return self.client.post('/auth/push/subscribe/', data={
            'endpoint': endpoint, 'keys': {'p256dh': 'k' * 20, 'auth': 'a' * 10}},
            content_type='application/json', **auth)

    def test_subscribe_and_only_unsubscribe_your_own(self):
        self.assertEqual(self.subscribe(self.auth).status_code, 200)
        self.client.post('/auth/push/unsubscribe/', data={'endpoint': 'https://push.example/abc'},
                         content_type='application/json', **self.other_auth)
        self.assertEqual(PushSubscription.objects.filter(user=self.user).count(), 1)
        self.client.post('/auth/push/unsubscribe/', data={'endpoint': 'https://push.example/abc'},
                         content_type='application/json', **self.auth)
        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_a_bad_subscription_is_refused(self):
        res = self.client.post('/auth/push/subscribe/', data={'endpoint': 'http://x', 'keys': {}},
                               content_type='application/json', **self.auth)
        self.assertEqual(res.json()['code'], 'PUSH_BAD_SUBSCRIPTION')

    def test_not_set_up_says_so(self):
        with mock.patch.dict('os.environ', {'VAPID_PUBLIC_KEY': '', 'VAPID_PRIVATE_KEY': ''}):
            res = self.subscribe(self.auth)
        self.assertEqual((res.status_code, res.json()['code']), (503, 'PUSH_UNAVAILABLE'))

    def test_a_gone_browser_is_forgotten(self):
        from pywebpush import WebPushException
        from . import push
        self.subscribe(self.auth)
        gone = WebPushException('gone', response=mock.Mock(status_code=410))
        with mock.patch('pywebpush.webpush', side_effect=gone):
            self.assertEqual(push.send_to_user(self.user.user_id, '{}'), 0)
        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_test_button_reports_no_device(self):
        res = self.client.post('/auth/push/test/', **self.auth)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'PUSH_NO_DEVICE'))

    def test_test_button_sends(self):
        self.subscribe(self.auth)
        with mock.patch('pywebpush.webpush') as send:
            res = self.client.post('/auth/push/test/', **self.auth)
        self.assertEqual(res.json()['data']['sent'], 1)
        self.assertEqual(send.call_count, 1)
