"""Block, mute and report, and whether they bite.

CEO, 2 September 2026: "build and fix them all fully". All three were buttons
that showed a toast and made no request. Somebody who blocked a harasser was
told "Block requested" and nothing happened.

The tests that matter here are not "does the row get written". They are "does
the block STOP anything", because a recorded block that does not stop a message
is the same fake with a database row attached.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import UserBlock, UserMute, UserReport, Users


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('b-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class BlockTests(TestCase):
    def setUp(self):
        self.me, self.my_auth = a_user('blkMe')
        self.them, self.their_auth = a_user('blkThem')

    def block(self, on=True, auth=None):
        return self.client.post(
            '/user/%s/block/' % self.them.username, data={'block': on},
            content_type='application/json',
            **(auth if auth is not None else self.my_auth))

    def test_blocking_records_it(self):
        res = self.block()
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertTrue(res.json()['data']['blocked'])
        self.assertTrue(UserBlock.objects.filter(
            blocker=self.me, blocked=self.them).exists())

    def test_unblocking(self):
        self.block()
        res = self.block(on=False)
        self.assertFalse(res.json()['data']['blocked'])
        self.assertEqual(UserBlock.objects.count(), 0)

    def test_blocking_needs_an_account(self):
        res = self.client.post('/user/%s/block/' % self.them.username,
                               data={'block': True},
                               content_type='application/json')
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(UserBlock.objects.count(), 0)

    def test_you_cannot_block_yourself(self):
        res = self.client.post('/user/%s/block/' % self.me.username,
                               data={'block': True},
                               content_type='application/json', **self.my_auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'CANNOT_BLOCK_SELF')

    # ------------------------------------------------ does it actually bite

    def dm(self, sender_auth, to_username, body='hello'):
        return self.client.post(
            '/dm/new/send/', data={'username': to_username, 'body': body},
            content_type='application/json', **sender_auth)

    def test_a_blocked_person_cannot_start_a_conversation(self):
        """The whole point. A recorded block that does not stop a message is
        the same fake with a database row attached."""
        self.block()
        res = self.dm(self.their_auth, self.me.username)
        self.assertEqual(res.status_code, 403, res.content[:300])

    def test_the_blocker_also_cannot_message_them(self):
        """Both directions. Enforcing one stops the wrong half: if only they
        are stopped, I can still write to the person I said I wanted nothing
        from."""
        self.block()
        res = self.dm(self.my_auth, self.them.username)
        self.assertEqual(res.status_code, 403, res.content[:300])

    def test_an_existing_conversation_is_stopped_too(self):
        """Somebody already in touch is exactly who a block is for. Checking
        only when a conversation is opened leaves every prior thread open."""
        opened = self.dm(self.their_auth, self.me.username, 'before the block')
        self.assertIn(opened.status_code, (200, 201), opened.content[:300])
        convo = opened.json()['data']
        key = convo.get('conversation_id') or convo.get('slug') or convo.get('id')

        self.block()

        res = self.client.post(
            '/dm/%s/send/' % key, data={'body': 'after the block'},
            content_type='application/json', **self.their_auth)
        self.assertEqual(res.status_code, 403, res.content[:300])

    def test_messaging_works_again_after_unblocking(self):
        self.block()
        self.block(on=False)
        res = self.dm(self.their_auth, self.me.username)
        self.assertIn(res.status_code, (200, 201), res.content[:300])

    def test_unrelated_people_are_unaffected(self):
        other, other_auth = a_user('blkOther')
        self.block()
        res = self.dm(other_auth, self.me.username)
        self.assertIn(res.status_code, (200, 201), res.content[:300])

    def test_the_helper_answers_both_directions(self):
        from .views_safety import blocked_ids_for
        self.block()
        self.assertIn(self.them.pk, blocked_ids_for(self.me))
        # And the blocked person should not be shown the blocker either.
        self.assertIn(self.me.pk, blocked_ids_for(self.them))


class MuteTests(TestCase):
    def setUp(self):
        self.me, self.my_auth = a_user('mutMe')
        self.them, self.their_auth = a_user('mutThem')

    def test_muting_and_unmuting(self):
        url = '/user/%s/mute/' % self.them.username
        res = self.client.post(url, data={'mute': True},
                               content_type='application/json', **self.my_auth)
        self.assertTrue(res.json()['data']['muted'])

        res = self.client.post(url, data={'mute': False},
                               content_type='application/json', **self.my_auth)
        self.assertFalse(res.json()['data']['muted'])
        self.assertEqual(UserMute.objects.count(), 0)

    def test_a_mute_can_expire_by_itself(self):
        """"Mute them for a week" is the common case, and a mute nobody
        remembers setting is a mute nobody undoes."""
        res = self.client.post('/user/%s/mute/' % self.them.username,
                               data={'mute': True, 'days': 7},
                               content_type='application/json', **self.my_auth)
        self.assertIsNotNone(res.json()['data']['until'])

        row = UserMute.objects.get(muter=self.me, muted=self.them)
        self.assertTrue(row.is_active)

        row.until = timezone.now() - timedelta(minutes=1)
        row.save(update_fields=['until'])
        self.assertFalse(row.is_active)

        from .views_safety import muted_ids_for
        self.assertNotIn(self.them.pk, muted_ids_for(self.me))

    def test_a_mute_does_not_stop_them_reaching_you(self):
        """That is what Block is for. Offering only Block for somebody who is
        loud rather than dangerous makes Block carry weight it should not."""
        self.client.post('/user/%s/mute/' % self.them.username,
                         data={'mute': True}, content_type='application/json',
                         **self.my_auth)
        res = self.client.post(
            '/dm/new/send/', data={'username': self.me.username, 'body': 'hi'},
            content_type='application/json', **self.their_auth)
        self.assertIn(res.status_code, (200, 201), res.content[:300])

    def test_a_silly_duration_is_refused(self):
        res = self.client.post('/user/%s/mute/' % self.them.username,
                               data={'mute': True, 'days': 4000},
                               content_type='application/json', **self.my_auth)
        self.assertEqual(res.status_code, 400)


class ReportTests(TestCase):
    def setUp(self):
        self.me, self.my_auth = a_user('repMe')
        self.them, self.their_auth = a_user('repThem')

    def report(self, **body):
        payload = {'reason': 'harassment', 'detail': 'abusive in DMs'}
        payload.update(body)
        return self.client.post('/user/%s/report/' % self.them.username,
                                data=payload, content_type='application/json',
                                **self.my_auth)

    def test_a_report_lands_in_a_queue_a_human_can_work(self):
        """The point of a report is the queue. One that goes nowhere is the
        same fake as the toast it replaces."""
        res = self.report(context='profile')
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = UserReport.objects.get(pk=res.json()['data']['report_id'])
        self.assertEqual(row.status, 'open')
        self.assertEqual(row.reason, 'harassment')
        self.assertEqual(row.context, 'profile')
        self.assertEqual(row.reported, self.them)

    def test_reporting_twice_does_not_fill_the_queue_with_duplicates(self):
        """A second press is somebody making sure it worked, not a second
        incident, and a queue full of duplicates is one nobody can read."""
        first = self.report()
        second = self.report()
        self.assertTrue(second.json()['data']['already'])
        self.assertEqual(second.json()['data']['report_id'],
                         first.json()['data']['report_id'])
        self.assertEqual(UserReport.objects.count(), 1)

    def test_an_invented_reason_is_refused(self):
        res = self.report(reason='because')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_REASON')

    def test_reporting_needs_an_account(self):
        res = self.client.post('/user/%s/report/' % self.them.username,
                               data={'reason': 'spam'},
                               content_type='application/json')
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(UserReport.objects.count(), 0)


class SafetyStateTests(TestCase):
    def setUp(self):
        self.me, self.my_auth = a_user('sfMe')
        self.them, _ = a_user('sfThem')

    def test_one_request_tells_the_profile_what_to_draw(self):
        """So the menu is right on first paint rather than flipping Block to
        Unblock a second later."""
        self.client.post('/user/%s/block/' % self.them.username,
                         data={'block': True}, content_type='application/json',
                         **self.my_auth)
        res = self.client.get('/user/%s/safety/' % self.them.username,
                              **self.my_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        data = res.json()['data']
        self.assertTrue(data['blocked'])
        self.assertFalse(data['muted'])
        self.assertTrue(any(r['key'] == 'harassment' for r in data['reasons']))

    def test_a_signed_out_viewer_gets_a_usable_answer(self):
        res = self.client.get('/user/%s/safety/' % self.them.username)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['data']['blocked'])
