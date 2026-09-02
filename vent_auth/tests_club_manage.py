"""Creating, renaming and deleting a club.

CEO, 2 September 2026: "users should be able to create, manage, delete clubs
(they created): I cant see anywhere for them to do this and i thought it was
created."

It had not been. Creating and joining existed; there was no way to correct a
name or a description afterwards, and no way to delete a club at all.

Worth recording why these tests exist separately from `tests_clubs.py`: the
gate for "the creator can delete a club they created" was passed by running
that suite, which is green and tests deleting **topics and messages**. It never
deleted a club, because there was no endpoint to delete one. A suite that
returns OK proves only that the assertions it contains hold, never that the
thing you had in mind is covered.
"""
from django.test import TestCase
from django.utils import timezone

from .models import Club, ClubMember, ClubMessage, ClubTopic, Users


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('c-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ClubCreateTests(TestCase):
    def setUp(self):
        self.me, self.my_auth = a_user('clubMaker')

    def create(self, **body):
        payload = {'name': 'Lagos Free Fire Club', 'description': 'We play on Sundays.'}
        payload.update(body)
        return self.client.post('/club/create/', data=payload,
                                content_type='application/json', **self.my_auth)

    def test_creating_a_club_makes_the_creator_its_owner(self):
        res = self.create()
        self.assertEqual(res.status_code, 201, res.content[:300])
        club = Club.objects.get(name='Lagos Free Fire Club')
        self.assertEqual(club.owner, self.me)
        self.assertEqual(
            ClubMember.objects.get(club=club, user=self.me).role,
            ClubMember.ROLE_OWNER)

    def test_a_new_club_has_somewhere_to_talk(self):
        """A club with no topic has nowhere to say anything, so it would open
        onto an empty room with no way to make one."""
        self.create()
        club = Club.objects.get(name='Lagos Free Fire Club')
        self.assertTrue(ClubTopic.objects.filter(club=club).exists())

    def test_creating_needs_an_account(self):
        res = self.client.post('/club/create/', data={'name': 'Anon Club'},
                               content_type='application/json')
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(Club.objects.count(), 0)

    def test_a_club_needs_a_name(self):
        self.assertEqual(self.create(name='   ').status_code, 400)

    def test_two_clubs_cannot_share_a_name(self):
        self.create()
        self.assertEqual(self.create().status_code, 409)


class ClubUpdateTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('clubOwner')
        self.admin, self.admin_auth = a_user('clubAdmin')
        self.member, self.member_auth = a_user('clubMember')
        self.stranger, self.stranger_auth = a_user('clubStranger')

        self.club = Club.objects.create(name='Naija Snipers', owner=self.owner,
                                        description='Original text.')
        ClubMember.objects.create(club=self.club, user=self.owner,
                                  role=ClubMember.ROLE_OWNER)
        ClubMember.objects.create(club=self.club, user=self.admin,
                                  role=ClubMember.ROLE_ADMIN)
        ClubMember.objects.create(club=self.club, user=self.member,
                                  role=ClubMember.ROLE_MEMBER)

    def update(self, auth, **body):
        return self.client.post('/club/%s/update/' % self.club.slug, data=body,
                                content_type='application/json', **auth)

    # --------------------------------------------------------------- allowed

    def test_the_owner_can_rename_it(self):
        res = self.update(self.owner_auth, name='Naija Marksmen')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.club.refresh_from_db()
        self.assertEqual(self.club.name, 'Naija Marksmen')

    def test_an_admin_can_change_the_description(self):
        res = self.update(self.admin_auth, description='We play on Fridays.')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.club.refresh_from_db()
        self.assertEqual(self.club.description, 'We play on Fridays.')

    def test_renaming_moves_the_address_and_says_so(self):
        """The slug follows the name, and the caller is told the new address
        so it can move rather than sitting on a URL that no longer resolves."""
        before = self.club.slug
        res = self.update(self.owner_auth, name='Naija Marksmen')
        self.club.refresh_from_db()
        self.assertNotEqual(self.club.slug, before)
        self.assertEqual(res.json()['data']['url'],
                         '/community/club/%s' % self.club.slug)

    def test_the_old_address_keeps_working(self):
        """A link shared before the rename has to keep opening the club."""
        before = self.club.slug
        self.update(self.owner_auth, name='Naija Marksmen')
        res = self.client.get('/club/%s/overview/' % before)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json().get('status'), 'moved')

    def test_privacy_can_be_changed(self):
        self.update(self.owner_auth, is_private=True)
        self.club.refresh_from_db()
        self.assertTrue(self.club.is_private)

    def test_saving_the_description_alone_does_not_trip_the_name_clash(self):
        """Its own name is not a duplicate of itself. Comparing without
        excluding this row refuses every save that leaves the name alone."""
        res = self.update(self.owner_auth, name='Naija Snipers',
                          description='Same name, new words.')
        self.assertEqual(res.status_code, 200, res.content[:300])

    # -------------------------------------------------------------- refused

    def test_a_plain_member_cannot_change_it(self):
        res = self.update(self.member_auth, name='Hijacked')
        self.assertEqual(res.status_code, 403)
        self.club.refresh_from_db()
        self.assertEqual(self.club.name, 'Naija Snipers')

    def test_a_stranger_cannot_change_it(self):
        self.assertEqual(self.update(self.stranger_auth, name='Hijacked').status_code, 403)

    def test_a_signed_out_caller_cannot_change_it(self):
        res = self.client.post('/club/%s/update/' % self.club.slug,
                               data={'name': 'Hijacked'},
                               content_type='application/json')
        self.assertIn(res.status_code, (401, 403))

    def test_a_name_already_taken_is_refused(self):
        other = Club.objects.create(name='Taken Already', owner=self.owner)
        res = self.update(self.owner_auth, name=other.name)
        self.assertEqual(res.status_code, 409)

    def test_an_empty_name_is_refused(self):
        self.assertEqual(self.update(self.owner_auth, name='  ').status_code, 400)

    def test_sending_nothing_is_refused(self):
        self.assertEqual(self.update(self.owner_auth).status_code, 400)


class ClubDeleteTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('delOwner')
        self.admin, self.admin_auth = a_user('delAdmin')
        self.club = Club.objects.create(name='Temporary Club', owner=self.owner)
        ClubMember.objects.create(club=self.club, user=self.owner,
                                  role=ClubMember.ROLE_OWNER)
        ClubMember.objects.create(club=self.club, user=self.admin,
                                  role=ClubMember.ROLE_ADMIN)
        self.topic = ClubTopic.objects.create(club=self.club, name='General',
                                              position=0, created_by=self.owner)
        ClubMessage.objects.create(topic=self.topic, author=self.owner,
                                   body='Something said here.')

    def delete(self, auth, name=None):
        return self.client.post(
            '/club/%s/delete/' % self.club.slug,
            data={'confirm_name': self.club.name if name is None else name},
            content_type='application/json', **auth)

    def test_the_owner_can_delete_it(self):
        res = self.delete(self.owner_auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertFalse(Club.objects.filter(pk=self.club.pk).exists())

    def test_deleting_takes_the_topics_and_messages_with_it(self):
        self.delete(self.owner_auth)
        self.assertEqual(ClubTopic.objects.filter(club_id=self.club.pk).count(), 0)
        self.assertEqual(ClubMessage.objects.filter(topic_id=self.topic.pk).count(), 0)

    def test_an_admin_cannot_delete_it(self):
        """An admin was appointed to help run the club, not to end it."""
        res = self.delete(self.admin_auth)
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Club.objects.filter(pk=self.club.pk).exists())

    def test_a_signed_out_caller_cannot_delete_it(self):
        res = self.client.post('/club/%s/delete/' % self.club.slug,
                               data={'confirm_name': self.club.name},
                               content_type='application/json')
        self.assertIn(res.status_code, (401, 403))
        self.assertTrue(Club.objects.filter(pk=self.club.pk).exists())

    def test_the_name_has_to_be_typed_back(self):
        """A confirmation somebody can give by pressing return without reading
        is not a confirmation, and this cannot be undone."""
        res = self.delete(self.owner_auth, name='')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'CONFIRM_NAME_MISMATCH')
        self.assertTrue(Club.objects.filter(pk=self.club.pk).exists())

    def test_the_wrong_name_is_refused(self):
        res = self.delete(self.owner_auth, name='Some Other Club')
        self.assertEqual(res.status_code, 400)
        self.assertTrue(Club.objects.filter(pk=self.club.pk).exists())

    def test_casing_and_spacing_do_not_matter(self):
        """Refusing "temporary club " for a club called "Temporary Club" is
        pedantry, not safety: they have clearly read it."""
        res = self.delete(self.owner_auth, name='  temporary club  ')
        self.assertEqual(res.status_code, 200, res.content[:300])


class ClubCapabilityTests(TestCase):
    """The screen draws its controls from these, so they have to be right."""

    def setUp(self):
        self.owner, self.owner_auth = a_user('capOwner')
        self.admin, self.admin_auth = a_user('capAdmin')
        self.member, self.member_auth = a_user('capMember')
        self.club = Club.objects.create(name='Capability Club', owner=self.owner)
        for user, role in ((self.owner, ClubMember.ROLE_OWNER),
                           (self.admin, ClubMember.ROLE_ADMIN),
                           (self.member, ClubMember.ROLE_MEMBER)):
            ClubMember.objects.create(club=self.club, user=user, role=role)

    def me(self, auth=None):
        res = self.client.get('/club/%s/overview/' % self.club.slug,
                              **(auth or {}))
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']['me']

    def test_the_owner_may_edit_and_delete(self):
        me = self.me(self.owner_auth)
        self.assertTrue(me['can_edit_club'])
        self.assertTrue(me['can_delete_club'])

    def test_an_admin_may_edit_but_not_delete(self):
        me = self.me(self.admin_auth)
        self.assertTrue(me['can_edit_club'])
        self.assertFalse(me['can_delete_club'])

    def test_a_member_may_do_neither(self):
        me = self.me(self.member_auth)
        self.assertFalse(me['can_edit_club'])
        self.assertFalse(me['can_delete_club'])

    def test_a_signed_out_viewer_may_do_neither(self):
        """And gets a usable answer rather than a refusal, so the interface
        has one code path for members and strangers alike."""
        me = self.me()
        self.assertFalse(me['can_edit_club'])
        self.assertFalse(me['can_delete_club'])
        self.assertFalse(me['is_member'])
