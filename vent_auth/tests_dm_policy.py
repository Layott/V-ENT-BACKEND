"""A profile says what that person accepts, without saying who is blocked.

T11: "A profile offers a DM, and shows what that person allows publicly."

The offering half was already built - `may_message`, the `can_message` flag,
and a Message button gated on it. What was missing is the SHOWING: a viewer
with no button and no explanation assumes the page is broken.

The one decision worth pinning is what this must NOT say. `may_message` also
returns False for a block, and if the policy reported that, the absence of a
button would tell somebody they had been blocked. So this reports the owner's
own stated setting and nothing derived from the viewer: blocked and
simply-not-allowed look identical from outside.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import UserProfile, Users
from .views_usersearch import may_message, message_policy


def a_user(name):
    user = Users.objects.create(username=name, email='%s@vent.test' % name,
                                is_active=True,
                                login_session_token=('dm-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    UserProfile.objects.get_or_create(user=user)
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def set_policy(user, value):
    from .models import UserSetting
    row, _ = UserSetting.objects.get_or_create(user=user)
    data = dict(row.data or {})
    privacy = dict(data.get('privacy') or {})
    privacy['allow_direct_messages'] = value
    data['privacy'] = privacy
    row.data = data
    row.save()


class PolicyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner, self.owner_auth = a_user('dm_owner')
        self.viewer, self.viewer_auth = a_user('dm_viewer')

    def test_the_default_is_anyone(self):
        self.assertEqual(message_policy(self.owner), 'anyone')

    def test_nobody_is_reported_as_nobody(self):
        set_policy(self.owner, 'nobody')
        self.assertEqual(message_policy(self.owner), 'nobody')
        self.assertFalse(may_message(self.viewer, self.owner))

    def test_followers_is_reported_as_followers(self):
        set_policy(self.owner, 'followers')
        self.assertEqual(message_policy(self.owner), 'followers')

    def test_the_profile_carries_it(self):
        set_policy(self.owner, 'nobody')
        res = self.client.get('/user/%s/profile/' % self.owner.username,
                              **self.viewer_auth)
        self.assertEqual(res.status_code, 200, res.content[:200])
        data = res.json()['data']
        self.assertFalse(data['can_message'])
        self.assertEqual(data['message_policy'], 'nobody')

    def test_a_signed_out_visitor_sees_the_policy_too(self):
        """It is what the person allows PUBLICLY. A stranger deciding whether
        to make an account is exactly who benefits from knowing."""
        set_policy(self.owner, 'followers')
        res = self.client.get('/user/%s/profile/' % self.owner.username)
        self.assertEqual(res.json()['data']['message_policy'], 'followers')


class ABlockIsNeverVisibleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner, self.owner_auth = a_user('dm_owner2')
        self.blocked, self.blocked_auth = a_user('dm_blocked')

    def test_being_blocked_looks_exactly_like_being_allowed(self):
        """`may_message` is False for a block too. If the policy reported that,
        the absence of a button would tell somebody they had been blocked."""
        from .models import UserBlock
        UserBlock.objects.create(blocker=self.owner, blocked=self.blocked)

        # The owner accepts anybody, and this person still may not write.
        self.assertEqual(message_policy(self.owner), 'anyone')
        self.assertFalse(may_message(self.blocked, self.owner))

        res = self.client.get('/user/%s/profile/' % self.owner.username,
                              **self.blocked_auth)
        data = res.json()['data']
        # The policy says "anyone" - the honest public fact - and can_message
        # says no. Nothing here reveals the block.
        self.assertEqual(data['message_policy'], 'anyone')
        self.assertFalse(data['can_message'])
