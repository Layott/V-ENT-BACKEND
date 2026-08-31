"""A club as a group chat somebody runs.

CEO, 31 August 2026: "Clubs are meant to be like group chats, that people can
join and stay an read and send messages around particular set topics, then you
have people who manage the group chat and manage it, they also can add also
admins too with varying levels of control to their clubs."

What is worth testing hardest is not that a message saves. It is the ladder:
that a moderator cannot demote an admin, that an admin cannot appoint an admin,
that a muted member still reads, and that a message taken down leaves its place
in the thread. Those are the rules an organiser will lean on, and every one of
them is a check the API has to make, because a client that hides the button is
a courtesy rather than a permission.
"""
import json
import uuid

from django.test import TestCase
from django.utils import timezone

from .models import Club, ClubMember, ClubMessage, ClubTopic, Games, Users


def a_user(name):
    tag = uuid.uuid4().hex[:5]
    user = Users.objects.create(
        username='%s_%s' % (name, tag),
        email='%s_%s@vent.test' % (name, tag),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
    )
    user.login_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ClubChatTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        self.admin, self.admin_auth = a_user('admin')
        self.mod, self.mod_auth = a_user('mod')
        self.member, self.member_auth = a_user('member')
        self.stranger, self.stranger_auth = a_user('stranger')
        self.game, _ = Games.objects.get_or_create(game_title='Free Fire')

        res = self.client.post('/club/create/', data=json.dumps({
            'name': 'Lagos Free Fire %s' % uuid.uuid4().hex[:4],
            'description': 'Weeknight squads.',
            'game': 'Free Fire',
        }), content_type='application/json', **self.owner_auth)
        self.assertEqual(res.status_code, 201, res.content)
        self.club = Club.objects.get(id=res.json()['data']['club']['id'])
        self.slug = self.club.slug

        for person in (self.admin, self.mod, self.member):
            ClubMember.objects.create(club=self.club, user=person)
        ClubMember.objects.filter(club=self.club, user=self.admin).update(role='admin')
        ClubMember.objects.filter(club=self.club, user=self.mod).update(role='moderator')

        self.topic = ClubTopic.objects.get(club=self.club, name='General')

    # -- helpers ----------------------------------------------------------

    def _post(self, url, auth=None, **body):
        return self.client.post(url, data=json.dumps(body),
                                content_type='application/json', **(auth or {}))

    def _say(self, auth, words, topic=None):
        return self._post('/club/%s/topic/%d/post/' % (self.slug, (topic or self.topic).id),
                          auth, body=words)

    # -- creation ---------------------------------------------------------

    def test_creator_owns_the_club_and_it_has_somewhere_to_talk(self):
        """A club with no topic has nowhere to say anything, and a club whose
        creator is an ordinary member has nobody who can appoint anybody."""
        membership = ClubMember.objects.get(club=self.club, user=self.owner)
        self.assertEqual(membership.role, ClubMember.ROLE_OWNER)
        self.assertTrue(ClubTopic.objects.filter(club=self.club).exists())

    # -- reading ----------------------------------------------------------

    def test_a_public_club_reads_without_an_account(self):
        res = self.client.get('/club/%s/overview/' % self.slug)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(data['club']['slug'], self.slug)
        self.assertTrue(data['topics'])
        self.assertFalse(data['me']['is_member'])
        self.assertFalse(data['me']['can_post'])

    def test_a_private_club_does_not(self):
        self.club.is_private = True
        self.club.save(update_fields=['is_private'])
        self.assertEqual(self.client.get('/club/%s/overview/' % self.slug).status_code, 403)
        self.assertEqual(
            self.client.get('/club/%s/overview/' % self.slug, **self.member_auth).status_code,
            200)

    def test_a_renamed_club_still_answers_on_its_old_address(self):
        old = self.slug
        self.club.name = 'Lagos Free Fire Nights'
        self.club.save()
        res = self.client.get('/club/%s/overview/' % old)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'moved')
        self.assertEqual(res.json()['data']['slug'], self.club.slug)

    # -- posting ----------------------------------------------------------

    def test_joining_is_what_earns_the_right_to_post(self):
        res = self._say(self.stranger_auth, 'hello')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'JOIN_REQUIRED')

        self._post('/club/%s/join/' % self.slug, self.stranger_auth)
        res = self._say(self.stranger_auth, 'hello')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['data']['message']['body'], 'hello')

    def test_an_empty_or_enormous_message_is_refused(self):
        self.assertEqual(self._say(self.member_auth, '   ').status_code, 400)
        self.assertEqual(self._say(self.member_auth, 'x' * 4001).status_code, 400)

    def test_messages_come_back_oldest_last_and_after_returns_only_the_new(self):
        first = self._say(self.member_auth, 'one').json()['data']['message']['id']
        second = self._say(self.member_auth, 'two').json()['data']['message']['id']

        res = self.client.get('/club/%s/topic/%d/' % (self.slug, self.topic.id))
        bodies = [m['body'] for m in res.json()['data']['messages']]
        self.assertEqual(bodies, ['one', 'two'])

        res = self.client.get('/club/%s/topic/%d/?after=%d' % (self.slug, self.topic.id, first))
        ids = [m['id'] for m in res.json()['data']['messages']]
        self.assertEqual(ids, [second])

    def test_a_locked_topic_still_reads_and_only_moderators_may_write(self):
        self.topic.is_locked = True
        self.topic.save(update_fields=['is_locked'])

        self.assertEqual(self.client.get(
            '/club/%s/topic/%d/' % (self.slug, self.topic.id)).status_code, 200)
        res = self._say(self.member_auth, 'anyone there')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'TOPIC_LOCKED')
        self.assertEqual(self._say(self.mod_auth, 'closed, sorry').status_code, 201)

    # -- moderation -------------------------------------------------------

    def test_a_mute_stops_writing_and_not_reading_and_expires_by_itself(self):
        res = self._post('/club/%s/mute/' % self.slug, self.mod_auth,
                         username=self.member.username, minutes=30)
        self.assertEqual(res.status_code, 200, res.content)

        res = self._say(self.member_auth, 'still here')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'MUTED')
        overview = self.client.get('/club/%s/overview/' % self.slug, **self.member_auth).json()
        self.assertTrue(overview['data']['me']['is_muted'])
        self.assertFalse(overview['data']['me']['can_post'])
        self.assertTrue(overview['data']['topics'])

        # Lifted by hand, and lifted by the clock: both leave the same state.
        self._post('/club/%s/mute/' % self.slug, self.mod_auth,
                   username=self.member.username, minutes=0)
        self.assertEqual(self._say(self.member_auth, 'back').status_code, 201)

    def test_a_removed_message_keeps_its_place_and_loses_its_words(self):
        mid = self._say(self.member_auth, 'said something').json()['data']['message']['id']
        res = self._post('/club/%s/message/%d/delete/' % (self.slug, mid), self.mod_auth)
        self.assertEqual(res.status_code, 200, res.content)

        rows = self.client.get(
            '/club/%s/topic/%d/' % (self.slug, self.topic.id)).json()['data']['messages']
        self.assertEqual([m['id'] for m in rows], [mid])
        self.assertTrue(rows[0]['deleted'])
        self.assertEqual(rows[0]['body'], '')
        self.assertTrue(ClubMessage.objects.filter(id=mid).exists())

    def test_an_author_may_remove_their_own_and_a_member_may_not_remove_anothers(self):
        mine = self._say(self.member_auth, 'mine').json()['data']['message']['id']
        theirs = self._say(self.admin_auth, 'theirs').json()['data']['message']['id']
        self.assertEqual(
            self._post('/club/%s/message/%d/delete/' % (self.slug, mine),
                       self.member_auth).status_code, 200)
        self.assertEqual(
            self._post('/club/%s/message/%d/delete/' % (self.slug, theirs),
                       self.member_auth).status_code, 403)

    def test_a_moderator_cannot_remove_an_admins_message(self):
        theirs = self._say(self.admin_auth, 'from the admin').json()['data']['message']['id']
        self.assertEqual(
            self._post('/club/%s/message/%d/delete/' % (self.slug, theirs),
                       self.mod_auth).status_code, 403)

    # -- the ladder -------------------------------------------------------

    def test_only_the_owner_can_make_somebody_an_admin(self):
        res = self._post('/club/%s/role/' % self.slug, self.admin_auth,
                         username=self.member.username, role='admin')
        self.assertEqual(res.status_code, 403, res.content)

        res = self._post('/club/%s/role/' % self.slug, self.owner_auth,
                         username=self.member.username, role='admin')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(
            ClubMember.objects.get(club=self.club, user=self.member).role, 'admin')

    def test_an_admin_may_appoint_a_moderator(self):
        res = self._post('/club/%s/role/' % self.slug, self.admin_auth,
                         username=self.member.username, role='moderator')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(
            ClubMember.objects.get(club=self.club, user=self.member).role, 'moderator')

    def test_equal_rank_is_not_enough(self):
        """Two admins that can demote each other leaves a club with no
        management, decided by whoever pressed first."""
        second, second_auth = a_user('admin2')
        ClubMember.objects.create(club=self.club, user=second, role='admin')
        res = self._post('/club/%s/role/' % self.slug, self.admin_auth,
                         username=second.username, role='member')
        self.assertEqual(res.status_code, 403)

    def test_nobody_may_touch_the_owner(self):
        for auth in (self.admin_auth, self.mod_auth):
            self.assertEqual(
                self._post('/club/%s/role/' % self.slug, auth,
                           username=self.owner.username, role='member').status_code, 403)
            self.assertEqual(
                self._post('/club/%s/remove-member/' % self.slug, auth,
                           username=self.owner.username).status_code, 403)

    def test_a_moderator_cannot_change_roles_at_all(self):
        res = self._post('/club/%s/role/' % self.slug, self.mod_auth,
                         username=self.member.username, role='moderator')
        self.assertEqual(res.status_code, 403)

    def test_ownership_is_handed_over_not_assigned(self):
        res = self._post('/club/%s/role/' % self.slug, self.owner_auth,
                         username=self.member.username, role='owner')
        self.assertEqual(res.status_code, 400)

    def test_a_moderator_may_remove_a_member(self):
        res = self._post('/club/%s/remove-member/' % self.slug, self.mod_auth,
                         username=self.member.username)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(ClubMember.objects.filter(club=self.club, user=self.member).exists())

    def test_the_owner_cannot_leave_and_a_member_can(self):
        res = self._post('/club/%s/leave/' % self.slug, self.owner_auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'OWNER_CANNOT_LEAVE')

        res = self._post('/club/%s/leave/' % self.slug, self.member_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(ClubMember.objects.filter(club=self.club, user=self.member).exists())

    # -- topics -----------------------------------------------------------

    def test_admins_manage_topics_and_members_do_not(self):
        res = self._post('/club/%s/topic/create/' % self.slug, self.member_auth,
                         name='Scrims')
        self.assertEqual(res.status_code, 403)

        res = self._post('/club/%s/topic/create/' % self.slug, self.admin_auth,
                         name='Scrims', description='Find a squad')
        self.assertEqual(res.status_code, 201, res.content)
        topic_id = res.json()['data']['topic']['id']

        res = self._post('/club/%s/topic/%d/update/' % (self.slug, topic_id),
                         self.admin_auth, name='Scrims and squads', is_locked=True)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['topic']['name'], 'Scrims and squads')
        self.assertTrue(res.json()['data']['topic']['is_locked'])

        res = self._post('/club/%s/topic/%d/delete/' % (self.slug, topic_id), self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(ClubTopic.objects.filter(id=topic_id).exists())

    def test_two_topics_cannot_share_a_name_and_the_last_one_cannot_go(self):
        self.assertEqual(
            self._post('/club/%s/topic/create/' % self.slug, self.owner_auth,
                       name='general').status_code, 400)
        res = self._post('/club/%s/topic/%d/delete/' % (self.slug, self.topic.id),
                         self.owner_auth)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'LAST_TOPIC')

    def test_the_topic_list_counts_messages_and_ignores_removed_ones(self):
        mid = self._say(self.member_auth, 'one').json()['data']['message']['id']
        self._say(self.member_auth, 'two')
        self._post('/club/%s/message/%d/delete/' % (self.slug, mid), self.mod_auth)

        topics = self.client.get('/club/%s/overview/' % self.slug).json()['data']['topics']
        general = [t for t in topics if t['id'] == self.topic.id][0]
        self.assertEqual(general['message_count'], 1)

    # -- members list -----------------------------------------------------

    def test_the_member_list_reports_roles(self):
        res = self.client.get('/club/%s/members/' % self.slug)
        self.assertEqual(res.status_code, 200, res.content)
        by_name = {m['user']['username']: m['role'] for m in res.json()['data']['members']}
        self.assertEqual(by_name[self.owner.username], 'owner')
        self.assertEqual(by_name[self.admin.username], 'admin')
        self.assertEqual(by_name[self.mod.username], 'moderator')
        self.assertEqual(by_name[self.member.username], 'member')
