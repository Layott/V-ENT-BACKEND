"""Running an organisation: invites with roles, scopes, and what it holds.

CEO, 31 August 2026: "logos and banners of organizations still don't load up or
show. also trying to invite members to my organization says coming soon why? I
should be able to invite people and give them different roles to manage
different things. An organization can have different teams, events,
tournaments, clubs."

The three faults behind that sentence, each with a test here:

- the create wizard sent the logo as a `blob:` URL inside a JSON body, so
  `request.FILES` was empty and the picture was dropped in silence;
- there was no endpoint that could change a logo afterwards, while the wizard
  told people they could;
- `org_events` answered an empty list with a comment saying events were not
  org-owned, when the foreign key had existed for weeks.
"""
import json
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from .models import Club, OrgInvite, OrgMember, Organization, Users


PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08'
    b'\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00'
    b'\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


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


class OrgManageTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('orgowner')
        self.admin, self.admin_auth = a_user('orgadmin')
        self.manager, self.manager_auth = a_user('orgmanager')
        self.player, self.player_auth = a_user('player')

        self.org = Organization.objects.create(
            org_name='Vermillion %s' % uuid.uuid4().hex[:5],
            org_creator=self.owner, org_owner=self.owner,
        )
        OrgMember.objects.create(org=self.org, user=self.owner, role='owner')
        OrgMember.objects.create(org=self.org, user=self.admin, role='admin')
        OrgMember.objects.create(org=self.org, user=self.manager, role='manager',
                                 scopes=['tournaments'])
        self.ref = self.org.slug

    def _post(self, url, auth=None, **body):
        return self.client.post(url, data=json.dumps(body),
                                content_type='application/json', **(auth or {}))

    # -- pictures ---------------------------------------------------------

    def test_a_logo_sent_as_a_file_is_stored_and_comes_back_as_a_url(self):
        res = self.client.post(
            '/organization/create/',
            data={'name': 'Lagos Kings %s' % uuid.uuid4().hex[:5], 'tag': 'LKS',
                  'logo': SimpleUploadedFile('logo.png', PNG, content_type='image/png')},
            **self.owner_auth)
        self.assertEqual(res.status_code, 201, res.content)
        org = Organization.objects.get(org_id=res.json()['data']['organization']['id'])
        self.assertTrue(org.logo)
        self.assertTrue(res.json()['data']['organization']['logo'])

    def test_a_logo_sent_as_a_blob_url_in_json_is_not_mistaken_for_a_picture(self):
        """The exact shape the create wizard used to send. It must not end up
        stored as a filename that resolves to nothing."""
        res = self._post('/organization/create/', self.owner_auth,
                         name='Blob Test %s' % uuid.uuid4().hex[:5], tag='BLB',
                         logo='blob:http://localhost:3001/9f2c-4b1a')
        self.assertEqual(res.status_code, 201, res.content)
        org = Organization.objects.get(org_id=res.json()['data']['organization']['id'])
        self.assertFalse(org.logo)
        self.assertIsNone(res.json()['data']['organization']['logo'])

    def test_the_logo_can_be_changed_afterwards(self):
        res = self.client.post(
            '/organization/%s/update/' % self.ref,
            data={'banner': SimpleUploadedFile('b.png', PNG, content_type='image/png')},
            **self.admin_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.org.refresh_from_db()
        self.assertTrue(self.org.banner)
        self.assertTrue(res.json()['data']['organization']['banner'])

    def test_only_an_admin_may_edit_the_profile(self):
        res = self._post('/organization/%s/update/' % self.ref, self.manager_auth, bio='hi')
        self.assertEqual(res.status_code, 403)
        res = self._post('/organization/%s/update/' % self.ref, self.player_auth, bio='hi')
        self.assertEqual(res.status_code, 403)

    def test_renaming_keeps_the_old_address_working(self):
        old = self.org.slug
        res = self._post('/organization/%s/update/' % self.ref, self.owner_auth,
                         name='Vermillion Encore Reloaded')
        self.assertEqual(res.status_code, 200, res.content)
        self.org.refresh_from_db()
        self.assertNotEqual(self.org.slug, old)
        self.assertEqual(
            self.client.get('/organization/%s/' % old).status_code, 200)

    # -- invites ----------------------------------------------------------

    def test_an_invite_names_the_role_and_accepting_is_one_press(self):
        res = self._post('/organization/%s/invite/' % self.ref, self.owner_auth,
                         username=self.player.username, role='manager',
                         scopes=['events', 'clubs'])
        self.assertEqual(res.status_code, 200, res.content)
        token = res.json()['data']['invite']['token']
        self.assertFalse(token.isdigit(), 'an invite token must not be the primary key')

        mine = self.client.get('/organization/invites/mine/', **self.player_auth).json()
        self.assertEqual(mine['data']['count'], 1)
        self.assertEqual(mine['data']['invites'][0]['role'], 'manager')

        res = self._post('/organization/invite/%s/respond/' % token, self.player_auth,
                         accept=True)
        self.assertEqual(res.status_code, 200, res.content)
        row = OrgMember.objects.get(org=self.org, user=self.player)
        self.assertEqual(row.role, 'manager')
        self.assertEqual(sorted(row.scopes), ['clubs', 'events'])

    def test_declining_leaves_nobody_in_the_organization(self):
        token = self._post('/organization/%s/invite/' % self.ref, self.owner_auth,
                           username=self.player.username).json()['data']['invite']['token']
        self._post('/organization/invite/%s/respond/' % token, self.player_auth, accept=False)
        self.assertFalse(OrgMember.objects.filter(org=self.org, user=self.player).exists())

    def test_an_invite_can_only_be_answered_once_and_only_by_its_recipient(self):
        token = self._post('/organization/%s/invite/' % self.ref, self.owner_auth,
                           username=self.player.username).json()['data']['invite']['token']
        self.assertEqual(
            self._post('/organization/invite/%s/respond/' % token, self.manager_auth,
                       accept=True).status_code, 404)
        self._post('/organization/invite/%s/respond/' % token, self.player_auth, accept=True)
        res = self._post('/organization/invite/%s/respond/' % token, self.player_auth,
                         accept=False)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'ALREADY_ANSWERED')

    def test_re_inviting_corrects_the_role_rather_than_making_a_second_invite(self):
        self._post('/organization/%s/invite/' % self.ref, self.owner_auth,
                   username=self.player.username, role='member')
        self._post('/organization/%s/invite/' % self.ref, self.owner_auth,
                   username=self.player.username, role='manager', scopes=['teams'])
        pending = OrgInvite.objects.filter(org=self.org, user=self.player, status='pending')
        self.assertEqual(pending.count(), 1)
        self.assertEqual(pending.first().role, 'manager')

    def test_only_the_owner_may_invite_an_admin(self):
        res = self._post('/organization/%s/invite/' % self.ref, self.admin_auth,
                         username=self.player.username, role='admin')
        self.assertEqual(res.status_code, 403)
        res = self._post('/organization/%s/invite/' % self.ref, self.owner_auth,
                         username=self.player.username, role='admin')
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_manager_cannot_invite_anybody(self):
        res = self._post('/organization/%s/invite/' % self.ref, self.manager_auth,
                         username=self.player.username)
        self.assertEqual(res.status_code, 403)

    def test_inviting_somebody_already_in_says_so(self):
        res = self._post('/organization/%s/invite/' % self.ref, self.owner_auth,
                         username=self.admin.username)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'ALREADY_MEMBER')

    def test_a_cancelled_invite_cannot_be_accepted(self):
        token = self._post('/organization/%s/invite/' % self.ref, self.owner_auth,
                           username=self.player.username).json()['data']['invite']['token']
        self.assertEqual(
            self._post('/organization/%s/invite/%s/cancel/' % (self.ref, token),
                       self.owner_auth).status_code, 200)
        self.assertEqual(
            self._post('/organization/invite/%s/respond/' % token, self.player_auth,
                       accept=True).status_code, 400)

    # -- roles and scopes --------------------------------------------------

    def test_a_manager_runs_only_the_areas_named_on_their_membership(self):
        row = OrgMember.objects.get(org=self.org, user=self.manager)
        self.assertEqual(row.areas, ['tournaments'])
        self.assertTrue(row.may_run('tournaments'))
        self.assertFalse(row.may_run('clubs'))

    def test_an_admin_runs_every_area_and_a_member_runs_none(self):
        self.assertEqual(
            sorted(OrgMember.objects.get(org=self.org, user=self.admin).areas),
            sorted(OrgMember.ALL_SCOPES))
        OrgMember.objects.create(org=self.org, user=self.player, role='member')
        self.assertEqual(OrgMember.objects.get(org=self.org, user=self.player).areas, [])

    def test_scopes_are_cleared_when_somebody_stops_being_a_manager(self):
        res = self._post('/organization/%s/role/' % self.ref, self.owner_auth,
                         username=self.manager.username, role='member')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(OrgMember.objects.get(org=self.org, user=self.manager).scopes, [])

    def test_only_the_owner_may_appoint_an_admin(self):
        OrgMember.objects.create(org=self.org, user=self.player, role='member')
        self.assertEqual(
            self._post('/organization/%s/role/' % self.ref, self.admin_auth,
                       username=self.player.username, role='admin').status_code, 403)
        self.assertEqual(
            self._post('/organization/%s/role/' % self.ref, self.owner_auth,
                       username=self.player.username, role='admin').status_code, 200)

    def test_equal_rank_cannot_demote_or_remove(self):
        second, second_auth = a_user('admin2')
        OrgMember.objects.create(org=self.org, user=second, role='admin')
        self.assertEqual(
            self._post('/organization/%s/role/' % self.ref, self.admin_auth,
                       username=second.username, role='member').status_code, 403)
        self.assertEqual(
            self._post('/organization/%s/kick/' % self.ref, self.admin_auth,
                       user_id=second.user_id).status_code, 403)

    def test_the_owner_cannot_be_removed_or_demoted(self):
        self.assertEqual(
            self._post('/organization/%s/kick/' % self.ref, self.admin_auth,
                       user_id=self.owner.user_id).status_code, 403)
        self.assertEqual(
            self._post('/organization/%s/role/' % self.ref, self.admin_auth,
                       username=self.owner.username, role='member').status_code, 403)

    def test_capabilities_say_what_the_caller_may_do(self):
        res = self.client.get('/organization/%s/capabilities/' % self.ref, **self.manager_auth)
        me = res.json()['data']['me']
        self.assertEqual(me['role'], 'manager')
        self.assertEqual(me['areas'], ['tournaments'])
        self.assertFalse(me['can_invite'])

        anon = self.client.get('/organization/%s/capabilities/' % self.ref).json()['data']['me']
        self.assertFalse(anon['is_member'])

    # -- what it holds -----------------------------------------------------

    def test_an_organization_lists_the_events_it_runs(self):
        from vent_event.models import Event

        Event.objects.create(
            name='Lagos Anime Con', creator=self.owner, organization=self.org,
            event_type='physical', desc='A day of it', entry_fee=0,
            reg_start_date=timezone.now(), reg_end_date=timezone.now(),
            start_date=timezone.now().date(), start_time='10:00', end_time='18:00',
        )
        res = self.client.get('/organization/%s/events/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['data']['count'], 1)
        self.assertEqual(res.json()['data']['events'][0]['name'], 'Lagos Anime Con')

        card = self.client.get('/organization/%s/' % self.ref).json()['data']['organization']
        self.assertEqual(card['events_hosted'], 1)

    def test_a_club_can_be_handed_to_an_organization_and_taken_back(self):
        club = Club.objects.create(name='Kings Lounge %s' % uuid.uuid4().hex[:4],
                                   owner=self.owner)
        res = self._post('/organization/%s/link-club/' % self.ref, self.owner_auth,
                         club=club.slug)
        self.assertEqual(res.status_code, 200, res.content)
        club.refresh_from_db()
        self.assertEqual(club.organization_id, self.org.org_id)

        listed = self.client.get('/organization/%s/clubs/' % self.ref).json()['data']
        self.assertEqual(listed['count'], 1)

        res = self._post('/organization/%s/unlink-club/' % self.ref, self.owner_auth,
                         club=club.slug)
        self.assertEqual(res.status_code, 200, res.content)
        club.refresh_from_db()
        self.assertIsNone(club.organization_id)

    def test_only_the_club_owner_may_hand_it_over(self):
        club = Club.objects.create(name='Not Yours %s' % uuid.uuid4().hex[:4],
                                   owner=self.player)
        res = self._post('/organization/%s/link-club/' % self.ref, self.admin_auth,
                         club=club.slug)
        self.assertEqual(res.status_code, 403)

    def test_a_manager_without_the_clubs_scope_cannot_link_one(self):
        club = Club.objects.create(name='Managers Club %s' % uuid.uuid4().hex[:4],
                                   owner=self.manager)
        res = self._post('/organization/%s/link-club/' % self.ref, self.manager_auth,
                         club=club.slug)
        self.assertEqual(res.status_code, 403)

        OrgMember.objects.filter(org=self.org, user=self.manager).update(
            scopes=['tournaments', 'clubs'])
        res = self._post('/organization/%s/link-club/' % self.ref, self.manager_auth,
                         club=club.slug)
        self.assertEqual(res.status_code, 200, res.content)
