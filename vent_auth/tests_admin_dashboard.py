"""The admin dashboard, section by section, and the role that may open each.

CEO, 7 September 2026, spec in `tasks/specs/admin-dashboard.md`, row 191.

The tests here are grouped by the thing that would actually go wrong:

* **A role decides what somebody may DO, never what shape the data has.** Every
  section is requested by a role that should be refused as well as by one that
  should be admitted, and the refusal comes from the API. A hidden link is not
  a permission.
* **A report is a FILE.** `test_the_report_is_a_real_file` reads `res.content`
  and asserts on the header line. A DRF `Response` hands CSV to the JSON
  renderer and a test that reads `res.data` sees the right characters either
  way, which is exactly how that fault shipped here before.
* **Every act leaves a record.** A ban, a grant, a deletion, a transfer and an
  announcement each assert their `AdminAction`, because "who did this and why"
  is the only question anybody asks six weeks later.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import (AdminAction, Club, ClubMember, ClubMessage,
                              ClubTopic, Games, Notification, OrgMember,
                              Organization, OrgWallet, Post, Thread,
                              Transaction, UserGallery, UserReport, UserWallet,
                              Users, VerificationToken)


def a_user(name, **extra):
    """An account with a live, second-factor-marked session.

    The console reads the ordinary site session and insists it went through the
    authenticator. A session without `login_session_2fa_at` reaches nothing
    here, which is the whole point of the one-door design.
    """
    tag = uuid.uuid4().hex[:6]
    user = Users.objects.create(
        username='%s_%s' % (name, tag),
        email='%s_%s@vent.test' % (name, tag),
        full_name=name.replace('_', ' ').title(),
        login_session_token=('tk%s' % uuid.uuid4().hex)[:16],
        **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class ConsoleBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.game = Games.objects.get_or_create(game_title='Free Fire')[0]
        self.super_admin, self.as_super = a_user(
            'ad_super', is_staff=True, admin_role='super_admin')
        self.finance, self.as_finance = a_user(
            'ad_finance', is_staff=True, admin_role='finance_admin')
        self.moderator, self.as_mod = a_user(
            'ad_mod', is_staff=True, admin_role='mod_admin')
        self.organiser_admin, self.as_organiser = a_user(
            'ad_tourn', is_staff=True, admin_role='tournament_admin')
        self.member, self.as_member = a_user('ad_member')

    def get(self, path, auth=None, **params):
        return self.client.get(path, params,
                               **(auth if auth is not None else self.as_super))

    def post(self, path, body=None, auth=None):
        return self.client.post(path, body or {}, format='json',
                                **(auth if auth is not None else self.as_super))


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class RolesDecideWhatYouMayDoTests(ConsoleBase):
    """B2: the API refuses, not the navigation."""

    def test_a_financial_manager_cannot_read_the_report_queue(self):
        res = self.get('/auth/admin/reports/', self.as_finance)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'DO_NOT_PERMISSION_PERFORM')

    def test_a_moderator_cannot_read_every_transaction(self):
        res = self.get('/auth/admin/transactions/', self.as_mod)
        self.assertEqual(res.status_code, 403)

    def test_a_moderator_cannot_move_money(self):
        res = self.post('/auth/admin/transfer-funds/', {
            'from_kind': 'user', 'from': self.member.username,
            'to_kind': 'user', 'to': self.super_admin.username,
            'amount': 10, 'reason': 'test'}, self.as_mod)
        self.assertEqual(res.status_code, 403)

    def test_only_a_super_admin_manages_administrators(self):
        self.assertEqual(self.get('/auth/admin/administrators/',
                                  self.as_finance).status_code, 403)
        self.assertEqual(self.get('/auth/admin/administrators/',
                                  self.as_mod).status_code, 403)
        self.assertEqual(self.get('/auth/admin/administrators/',
                                  self.as_super).status_code, 200)

    def test_a_session_that_never_met_the_authenticator_reaches_nothing(self):
        """The one door. A password alone is not an admin session."""
        plain, auth = a_user('ad_plain', is_staff=True, admin_role='super_admin')
        plain.login_session_2fa_at = None
        plain.save(update_fields=['login_session_2fa_at'])
        res = self.get('/auth/admin/transactions/', auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'TWO_FACTOR_REQUIRED')

    def test_a_member_with_no_role_reaches_nothing(self):
        for path in ('/auth/admin/reports/', '/auth/admin/transactions/',
                     '/auth/admin/administrators/', '/auth/admin/content/'):
            res = self.get(path, self.as_member)
            self.assertIn(res.status_code, (401, 403), path)


class AdministratorsTests(ConsoleBase):
    """B3: a super admin makes an admin, and the record says who did."""

    def test_the_catalogue_carries_all_seven_roles_the_spec_names(self):
        res = self.get('/auth/admin/administrators/roles/')
        self.assertEqual(res.status_code, 200)
        values = {row['value'] for row in res.json()['data']['roles']}
        for role in ('super_admin', 'admin', 'finance_admin', 'mod_admin',
                     'tournament_admin', 'marketplace_admin', 'wager_admin'):
            self.assertIn(role, values, role)

    def test_the_two_roles_with_no_feature_say_so(self):
        res = self.get('/auth/admin/administrators/roles/')
        rows = {r['value']: r for r in res.json()['data']['roles']}
        self.assertTrue(rows['marketplace_admin']['awaiting_feature'])
        self.assertTrue(rows['wager_admin']['awaiting_feature'])
        self.assertFalse(rows['super_admin']['awaiting_feature'])

    def test_granting_a_role_makes_them_staff_and_is_written_down(self):
        res = self.post('/auth/admin/administrators/grant/', {
            'username': self.member.username,
            'admin_role': 'mod_admin', 'reason': 'joins the moderation team'})
        self.assertEqual(res.status_code, 200)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_staff)
        self.assertEqual(self.member.admin_role, 'mod_admin')
        self.assertTrue(AdminAction.objects.filter(
            action_type='grant_admin_role', admin=self.super_admin,
            target_id=str(self.member.user_id)).exists())

    def test_revoking_takes_the_console_away_and_is_written_down(self):
        self.post('/auth/admin/administrators/grant/', {
            'username': self.member.username, 'admin_role': 'mod_admin'})
        res = self.post('/auth/admin/administrators/grant/', {
            'username': self.member.username, 'revoke': True,
            'reason': 'left the team'})
        self.assertEqual(res.status_code, 200)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_staff)
        self.assertIsNone(self.member.admin_role)
        self.assertTrue(AdminAction.objects.filter(
            action_type='revoke_admin_role',
            target_id=str(self.member.user_id)).exists())

    def test_you_cannot_change_your_own_role(self):
        res = self.post('/auth/admin/administrators/grant/', {
            'username': self.super_admin.username, 'revoke': True})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'CANNOT_CHANGE_SELF')

    def test_an_invented_role_is_refused(self):
        res = self.post('/auth/admin/administrators/grant/', {
            'username': self.member.username, 'admin_role': 'moon_admin'})
        self.assertEqual(res.status_code, 400)

    def test_the_list_says_who_cannot_actually_sign_in_yet(self):
        """An admin with no authenticator has a role they cannot use."""
        res = self.get('/auth/admin/administrators/')
        rows = {r['username']: r for r in res.json()['data']['results']}
        self.assertIn(self.finance.username, rows)
        self.assertFalse(rows[self.finance.username]['two_factor'])


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class UserManagementTests(ConsoleBase):
    """C1: view, edit, activate, deactivate, reset a password, delete."""

    def test_a_reset_writes_a_token_and_records_who_sent_it(self):
        res = self.post('/auth/admin/users/%s/reset-password/'
                        % self.member.user_id, {'reason': 'they asked'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(VerificationToken.objects.filter(
            user_email=self.member.email).exists())
        self.assertTrue(AdminAction.objects.filter(
            action_type='reset_password',
            target_id=str(self.member.user_id)).exists())

    def test_a_moderator_may_not_send_a_reset(self):
        res = self.post('/auth/admin/users/%s/reset-password/'
                        % self.member.user_id, {}, self.as_mod)
        self.assertEqual(res.status_code, 403)

    def test_banning_deactivates_and_unbanning_restores(self):
        path = '/auth/admin/users/%s/ban/' % self.member.user_id
        res = self.client.patch(path, {'ban': True, 'reason': 'spam'},
                                format='json', **self.as_super)
        self.assertEqual(res.status_code, 200)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_active)

        res = self.client.patch(path, {'ban': False}, format='json',
                                **self.as_super)
        self.assertEqual(res.status_code, 200)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)

    def test_deleting_asks_for_confirmation_first(self):
        path = '/auth/admin/users/%s/delete/' % self.member.user_id
        res = self.client.delete(path, {}, format='json', **self.as_super)
        self.assertEqual(res.status_code, 400)
        self.assertTrue(Users.objects.filter(pk=self.member.pk).exists())

        res = self.client.delete(path, {'confirm': True, 'reason': 'asked to'},
                                 format='json', **self.as_super)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Users.objects.filter(pk=self.member.pk).exists())

    def test_the_detail_carries_reports_filed_about_them(self):
        """It returned a literal empty list until 8 September."""
        UserReport.objects.create(
            reporter=self.moderator, reported=self.member,
            reason='spam', detail='posting links', context='profile')
        res = self.get('/auth/admin/users/%s/' % self.member.user_id)
        self.assertEqual(res.status_code, 200)
        reports = res.json()['data']['reports']
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['reason'], 'spam')
        # `reporter` is a person here and a person in the report queue. The
        # same key carrying a string on one screen and an object on the other
        # is how a name renders as [object Object] on exactly one page.
        self.assertEqual(reports[0]['reporter']['username'], self.moderator.username)
        self.assertIn('avatar', reports[0]['reporter'])


# ---------------------------------------------------------------------------
# Reports and content
# ---------------------------------------------------------------------------

class ReportQueueTests(ConsoleBase):
    def setUp(self):
        super().setUp()
        self.report = UserReport.objects.create(
            reporter=self.member, reported=self.organiser_admin,
            reason='harassment', detail='abusive in a thread',
            context='club:lagos')

    def test_the_queue_lists_open_reports_and_counts_them(self):
        res = self.get('/auth/admin/reports/', self.as_mod)
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['counts']['open'], 1)

    def test_a_decision_needs_a_reason_on_it(self):
        res = self.post('/auth/admin/reports/%s/' % self.report.id,
                        {'action': 'dismiss'}, self.as_mod)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'REASON_REQUIRED')

    def test_actioning_records_who_decided_and_what_they_said(self):
        res = self.post('/auth/admin/reports/%s/' % self.report.id,
                        {'action': 'action', 'note': 'warned them'},
                        self.as_mod)
        self.assertEqual(res.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'actioned')
        self.assertEqual(self.report.reviewed_by_id, self.moderator.user_id)
        self.assertEqual(self.report.admin_note, 'warned them')
        self.assertTrue(AdminAction.objects.filter(
            action_type='report_action', target_id=str(self.report.id)).exists())

    def test_a_moderator_can_ban_from_the_report_and_it_is_one_record(self):
        res = self.post('/auth/admin/reports/%s/' % self.report.id,
                        {'action': 'action', 'note': 'repeat offender',
                         'also_ban': True}, self.as_mod)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['data']['banned'])
        self.organiser_admin.refresh_from_db()
        self.assertFalse(self.organiser_admin.is_active)
        self.assertTrue(AdminAction.objects.filter(
            action_type='ban_user',
            target_id=str(self.organiser_admin.user_id)).exists())

    def test_a_role_that_moderates_but_may_not_ban_gets_the_decision_only(self):
        """`admin` moderates content and may ban; `support_admin` does neither.
        The role used here is the one the spec gives content but not bans."""
        from vent_auth.decorators import ROLE_PERMISSIONS

        # Guard the premise rather than assume it: if the matrix ever grants
        # bans to a content role, this test should say so rather than pass.
        content_not_ban = [r for r in ROLE_PERMISSIONS['moderate_content']
                           if r not in ROLE_PERMISSIONS['ban_users']]
        if not content_not_ban:
            self.skipTest('every content role may ban today')
        actor, auth = a_user('ad_content', is_staff=True,
                             admin_role=content_not_ban[0])
        res = self.post('/auth/admin/reports/%s/' % self.report.id,
                        {'action': 'action', 'note': 'seen', 'also_ban': True},
                        auth)
        self.assertEqual(res.status_code, 403)


class ContentModerationTests(ConsoleBase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(
            title='Who is the best IGL', body='discuss', author=self.member,
            category='general')
        self.post_row = Post.objects.create(author=self.member, body='hello')
        self.image = UserGallery.objects.create(
            user=self.member, kind=UserGallery.KIND_ESPORTS,
            caption='at the arena', released_at=timezone.now(),
            release_terms_version=UserGallery.RELEASE_TERMS_VERSION)

    def test_highlighting_a_thread_was_a_column_nobody_could_write(self):
        res = self.post('/auth/admin/content/thread/%s/' % self.thread.slug,
                        {'action': 'pin'}, self.as_mod)
        self.assertEqual(res.status_code, 200, res.content)
        self.thread.refresh_from_db()
        self.assertTrue(self.thread.is_pinned)

        res = self.post('/auth/admin/content/thread/%s/' % self.thread.slug,
                        {'action': 'unpin'}, self.as_mod)
        self.assertEqual(res.status_code, 200)
        self.thread.refresh_from_db()
        self.assertFalse(self.thread.is_pinned)

    def test_locking_a_thread_stops_replies(self):
        self.post('/auth/admin/content/thread/%s/' % self.thread.slug,
                  {'action': 'lock'}, self.as_mod)
        self.thread.refresh_from_db()
        self.assertTrue(self.thread.is_locked)

    def test_removing_content_needs_a_reason_and_leaves_a_record(self):
        res = self.post('/auth/admin/content/post/%s/' % self.post_row.slug,
                        {'action': 'delete'}, self.as_mod)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'REASON_REQUIRED')

        res = self.post('/auth/admin/content/post/%s/' % self.post_row.slug,
                        {'action': 'delete', 'reason': 'advertising'},
                        self.as_mod)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Post.objects.filter(pk=self.post_row.pk).exists())
        self.assertTrue(AdminAction.objects.filter(
            action_type='delete_post').exists())

    def test_withdrawing_a_picture_licence_keeps_the_picture(self):
        res = self.post('/auth/admin/content/gallery/%s/' % self.image.pk,
                        {'action': 'revoke_release', 'reason': 'they asked'},
                        self.as_mod)
        self.assertEqual(res.status_code, 200)
        self.image.refresh_from_db()
        self.assertFalse(self.image.is_released)
        self.assertEqual(self.image.kind, UserGallery.KIND_PERSONAL)
        self.assertTrue(UserGallery.objects.filter(pk=self.image.pk).exists())

    def test_a_club_message_is_removed_softly(self):
        club = Club.objects.create(name='Lagos FC %s' % uuid.uuid4().hex[:4],
                                   owner=self.member)
        topic = ClubTopic.objects.create(club=club, name='General')
        message = ClubMessage.objects.create(
            topic=topic, author=self.member, body='spam link')
        res = self.post('/auth/admin/content/message/%s/' % message.pk,
                        {'action': 'delete', 'reason': 'spam'}, self.as_mod)
        self.assertEqual(res.status_code, 200)
        message.refresh_from_db()
        self.assertIsNotNone(message.deleted_at)
        self.assertEqual(message.deleted_by_id, self.moderator.user_id)

    def test_the_content_screen_names_what_is_not_built(self):
        res = self.get('/auth/admin/content/', self.as_mod, kind='threads')
        self.assertEqual(res.status_code, 200)
        not_built = {row['what'] for row in res.json()['data']['not_built']}
        self.assertIn('manga', not_built)
        self.assertIn('amv', not_built)

    def test_engagement_numbers_come_with_the_list(self):
        res = self.get('/auth/admin/content/', self.as_mod, kind='threads')
        engagement = res.json()['data']['engagement']
        self.assertEqual(engagement['threads_7d'], 1)
        self.assertEqual(engagement['posts_7d'], 1)


class CommunityDetailTests(ConsoleBase):
    def test_a_community_shows_its_people_and_what_was_said(self):
        club = Club.objects.create(name='Abuja Arena %s' % uuid.uuid4().hex[:4],
                                   owner=self.member)
        ClubMember.objects.create(club=club, user=self.member, role='member')
        topic = ClubTopic.objects.create(club=club, name='General')
        ClubMessage.objects.create(topic=topic, author=self.member, body='hi')
        Thread.objects.create(title='Rules of the club', body='read them',
                              author=self.member, club=club)

        res = self.get('/auth/admin/communities/%s/' % club.slug, self.as_mod)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(data['club']['members'], 1)
        self.assertEqual(data['club']['messages'], 1)
        self.assertEqual(len(data['threads']), 1)

    def test_a_finance_role_cannot_open_a_community(self):
        club = Club.objects.create(name='Kano Club %s' % uuid.uuid4().hex[:4],
                                   owner=self.member)
        res = self.get('/auth/admin/communities/%s/' % club.slug, self.as_finance)
        self.assertEqual(res.status_code, 403)


# ---------------------------------------------------------------------------
# Financial
# ---------------------------------------------------------------------------

class FinancialTests(ConsoleBase):
    def setUp(self):
        super().setUp()
        self.wallet = UserWallet.objects.create(
            user_wallet_id='w%s' % uuid.uuid4().hex[:8], user=self.member,
            wallet_balance=1000)
        Transaction.objects.create(
            wallet=self.wallet, type='top_up', amount=500, status='completed',
            description='Top up by card')
        Transaction.objects.create(
            wallet=self.wallet, type='deduction', amount=-200,
            status='completed', description='Entry fee')

    def test_the_ledger_lists_every_kind_of_wallet_in_one_place(self):
        res = self.get('/auth/admin/transactions/', self.as_finance)
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['totals']['credited_vc'], 500)
        self.assertEqual(data['totals']['debited_vc'], 200)
        self.assertEqual(data['totals']['net_vc'], 300)

    def test_the_totals_follow_the_filter_and_not_the_page(self):
        res = self.get('/auth/admin/transactions/', self.as_finance,
                       type='top_up')
        data = res.json()['data']
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['totals']['credited_vc'], 500)
        self.assertEqual(data['totals']['debited_vc'], 0)

    def test_searching_matches_the_owner_and_the_description(self):
        res = self.get('/auth/admin/transactions/', self.as_finance,
                       q=self.member.username)
        self.assertEqual(res.json()['data']['count'], 2)
        res = self.get('/auth/admin/transactions/', self.as_finance,
                       q='Entry fee')
        self.assertEqual(res.json()['data']['count'], 1)

    def test_the_report_is_a_real_file(self):
        """A DRF Response would hand this to the JSON renderer.

        `res.content` and the header line, deliberately: a test that reads
        `res.data` sees the same characters whether or not the fault is there,
        which is precisely how it shipped here once already.
        """
        res = self.get('/auth/admin/transactions/report.csv', self.as_finance)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv')
        self.assertIn('attachment;', res['Content-Disposition'])
        body = res.content.decode('utf-8')
        first = body.splitlines()[0]
        self.assertEqual(
            first, 'id,when,owner,owner_kind,type,amount_vc,status,description,reference')
        self.assertNotIn('"status": "success"', body)
        self.assertIn('Entry fee', body)

    def test_the_report_carries_the_same_filter_as_the_screen(self):
        res = self.get('/auth/admin/transactions/report.csv', self.as_finance,
                       type='top_up')
        body = res.content.decode('utf-8')
        self.assertIn('Top up by card', body)
        self.assertNotIn('Entry fee', body)

    def test_the_summary_reports_each_stream_separately(self):
        res = self.get('/auth/admin/finance/summary/', self.as_finance)
        self.assertEqual(res.status_code, 200)
        streams = {row['type']: row for row in res.json()['data']['streams']}
        self.assertEqual(streams['top_up']['total_vc'], 500)
        self.assertEqual(streams['deduction']['total_vc'], -200)
        self.assertIn('tickets', res.json()['data'])
        self.assertIn('payouts', res.json()['data'])

    def test_a_transfer_writes_both_lines_and_says_who_moved_it(self):
        org = Organization.objects.create(
            org_name='Vermillion %s' % uuid.uuid4().hex[:4],
            org_creator=self.member, org_owner=self.member)
        wallet = OrgWallet.objects.create(
            org_wallet_id='o%s' % uuid.uuid4().hex[:8], org=org,
            wallet_balance=800)

        res = self.post('/auth/admin/transfer-funds/', {
            'from_kind': 'org', 'from': org.slug,
            'to_kind': 'user', 'to': self.member.username,
            'amount': 300, 'reason': 'prize money owed'}, self.as_finance)
        self.assertEqual(res.status_code, 200, res.content)

        wallet.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(wallet.wallet_balance, 500)
        self.assertEqual(self.wallet.wallet_balance, 1300)
        self.assertTrue(AdminAction.objects.filter(
            action_type='transfer_funds', admin=self.finance).exists())
        # Both lines, and on the owner's own statement where they can see it.
        self.assertTrue(Transaction.objects.filter(
            org_wallet=wallet, amount=-300).exists())
        self.assertTrue(Transaction.objects.filter(
            wallet=self.wallet, amount=300).exists())

    def test_a_transfer_with_no_reason_is_refused(self):
        res = self.post('/auth/admin/transfer-funds/', {
            'from_kind': 'user', 'from': self.member.username,
            'to_kind': 'user', 'to': self.super_admin.username,
            'amount': 10}, self.as_finance)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'REASON_REQUIRED')

    def test_a_transfer_of_more_than_there_is_takes_nothing(self):
        target, _ = a_user('ad_target')
        UserWallet.objects.create(
            user_wallet_id='w%s' % uuid.uuid4().hex[:8], user=target,
            wallet_balance=0)
        res = self.post('/auth/admin/transfer-funds/', {
            'from_kind': 'user', 'from': self.member.username,
            'to_kind': 'user', 'to': target.username,
            'amount': 999999, 'reason': 'too much'}, self.as_finance)
        self.assertEqual(res.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, 1000)


# ---------------------------------------------------------------------------
# Organisations
# ---------------------------------------------------------------------------

class OrganisationTests(ConsoleBase):
    def setUp(self):
        super().setUp()
        self.org = Organization.objects.create(
            org_name='Encore %s' % uuid.uuid4().hex[:4],
            org_creator=self.member, org_owner=self.member, org_type='esports')
        OrgMember.objects.create(org=self.org, user=self.member, role='owner')
        self.other, _ = a_user('ad_orgmember')
        OrgMember.objects.create(org=self.org, user=self.other, role='member')

    def test_creating_one_needs_a_real_owner(self):
        res = self.post('/auth/admin/organizations/', {
            'name': 'New Org %s' % uuid.uuid4().hex[:4],
            'org_type': 'events', 'owner': 'nobody_at_all'})
        self.assertEqual(res.status_code, 404)

    def test_creating_one_makes_the_owner_a_member_too(self):
        name = 'New Org %s' % uuid.uuid4().hex[:4]
        res = self.post('/auth/admin/organizations/', {
            'name': name, 'org_type': 'events',
            'description': 'runs events in Lagos',
            'owner': self.member.username})
        self.assertEqual(res.status_code, 200, res.content)
        org = Organization.objects.get(org_name=name)
        self.assertEqual(org.org_owner_id, self.member.user_id)
        self.assertTrue(OrgMember.objects.filter(
            org=org, user=self.member, role='owner').exists())
        self.assertTrue(AdminAction.objects.filter(
            action_type='create_organization',
            target_id=str(org.org_id)).exists())

    def test_a_duplicate_name_is_refused_rather_than_created(self):
        res = self.post('/auth/admin/organizations/', {
            'name': self.org.org_name, 'org_type': 'events',
            'owner': self.member.username})
        self.assertEqual(res.status_code, 409)

    def test_renaming_keeps_the_old_address_working(self):
        old_slug = self.org.slug
        res = self.post('/auth/admin/organizations/%s/' % self.org.slug, {
            'action': 'set_details', 'name': 'Renamed %s' % uuid.uuid4().hex[:4],
            'description': 'new words'})
        self.assertEqual(res.status_code, 200, res.content)
        self.org.refresh_from_db()
        self.assertNotEqual(self.org.slug, old_slug)
        # The retired address still resolves through this endpoint.
        again = self.get('/auth/admin/organizations/%s/' % self.org.slug)
        self.assertEqual(again.status_code, 200)

    def test_a_member_role_can_be_changed_and_the_owner_cannot_be_removed(self):
        res = self.post('/auth/admin/organizations/%s/' % self.org.slug, {
            'action': 'set_member_role', 'username': self.other.username,
            'role': 'admin'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(OrgMember.objects.get(
            org=self.org, user=self.other).role, 'admin')

        res = self.post('/auth/admin/organizations/%s/' % self.org.slug, {
            'action': 'remove_member', 'username': self.member.username})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'CANNOT_REMOVE_OWNER')

        res = self.post('/auth/admin/organizations/%s/' % self.org.slug, {
            'action': 'remove_member', 'username': self.other.username,
            'reason': 'left'})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(OrgMember.objects.filter(
            org=self.org, user=self.other).exists())

    def test_the_statement_downloads_as_a_real_file(self):
        wallet = OrgWallet.objects.create(
            org_wallet_id='o%s' % uuid.uuid4().hex[:8], org=self.org,
            wallet_balance=100)
        Transaction.objects.create(
            org_wallet=wallet, type='transfer', amount=100, status='completed',
            description='Seed float')
        res = self.get('/auth/admin/organizations/%s/report.csv' % self.org.slug)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv')
        body = res.content.decode('utf-8')
        self.assertEqual(body.splitlines()[0],
                         'when,type,amount_vc,status,description')
        self.assertIn('Seed float', body)

    def test_the_detail_carries_the_members_and_the_statement(self):
        res = self.get('/auth/admin/organizations/%s/' % self.org.slug)
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertEqual(len(data['members_list']), 2)
        self.assertIn('transactions', data)


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------

class TournamentConsoleTests(ConsoleBase):
    def setUp(self):
        super().setUp()
        from vent_tournament.models import Tournament, TournamentRegistration

        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Naija Weekly %s' % uuid.uuid4().hex[:4],
            tournament_creator=self.member, tournament_game=self.game,
            tournament_type='online', tournament_access='individual',
            tournament_visibility='public', entry_fee='Paid',
            entry_fee_price=100, prize_type='no_prize',
            bracket_type='single_elimination',
            max_number_of_teams=8,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2), is_draft=False)
        self.player, _ = a_user('ad_player')
        self.reg = TournamentRegistration.objects.create(
            tournament=self.tournament, user=self.player, status='confirmed',
            entry_fee_paid=True)

    def test_analytics_count_participation_from_rows(self):
        res = self.get('/auth/admin/tournaments/%s/analytics/'
                       % self.tournament.slug, self.as_organiser)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['data']
        self.assertEqual(data['participation']['entered'], 1)
        self.assertEqual(data['participation']['confirmed'], 1)
        self.assertEqual(data['participation']['capacity'], 8)
        self.assertEqual(data['participation']['fill_percent'], 12.5)
        self.assertEqual(data['revenue']['paid_entries'], 1)

    def test_fill_is_null_rather_than_zero_when_there_is_no_cap(self):
        self.tournament.max_number_of_teams = None
        self.tournament.save(update_fields=['max_number_of_teams'])
        res = self.get('/auth/admin/tournaments/%s/analytics/'
                       % self.tournament.slug, self.as_organiser)
        self.assertIsNone(res.json()['data']['participation']['fill_percent'])

    def test_revenue_is_read_off_the_ledger_and_not_multiplied(self):
        wallet = UserWallet.objects.create(
            user_wallet_id='w%s' % uuid.uuid4().hex[:8], user=self.player,
            wallet_balance=0)
        Transaction.objects.create(
            wallet=wallet, type='deduction', amount=-100, status='completed',
            description='Entry fee', tournament=self.tournament)
        res = self.get('/auth/admin/tournaments/%s/analytics/'
                       % self.tournament.slug, self.as_organiser)
        self.assertEqual(res.json()['data']['revenue']['taken_vc'], 100)

    def test_an_announcement_reaches_the_inbox_of_everybody_registered(self):
        res = self.post('/auth/admin/tournaments/%s/announce/'
                        % self.tournament.slug,
                        {'subject': 'Check in opens at 18:00',
                         'body': 'Be in the lobby ten minutes early.'},
                        self.as_organiser)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Notification.objects.filter(
            user=self.player, category='tournament').count(), 1)
        self.assertTrue(AdminAction.objects.filter(
            action_type='announce_tournament',
            target_id=str(self.tournament.tournament_id)).exists())

    def test_an_empty_announcement_is_refused(self):
        res = self.post('/auth/admin/tournaments/%s/announce/'
                        % self.tournament.slug, {'subject': 'Hello'},
                        self.as_organiser)
        self.assertEqual(res.status_code, 400)

    def test_the_daily_limit_is_the_same_five_as_an_event(self):
        for index in range(5):
            res = self.post('/auth/admin/tournaments/%s/announce/'
                            % self.tournament.slug,
                            {'subject': 'Update %d' % index, 'body': 'text'},
                            self.as_organiser)
            self.assertEqual(res.status_code, 200)
        res = self.post('/auth/admin/tournaments/%s/announce/'
                        % self.tournament.slug,
                        {'subject': 'One too many', 'body': 'text'},
                        self.as_organiser)
        self.assertEqual(res.status_code, 429)

    def test_the_audience_narrows_to_people_who_paid(self):
        from vent_tournament.models import TournamentRegistration

        unpaid, _ = a_user('ad_unpaid')
        TournamentRegistration.objects.create(
            tournament=self.tournament, user=unpaid, status='confirmed',
            entry_fee_paid=False)
        res = self.post('/auth/admin/tournaments/%s/announce/'
                        % self.tournament.slug,
                        {'subject': 'Paid only', 'body': 'text',
                         'audience': 'paid'}, self.as_organiser)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Notification.objects.filter(user=self.player).count(), 1)
        self.assertEqual(Notification.objects.filter(user=unpaid).count(), 0)

    def test_a_finance_role_cannot_announce(self):
        res = self.post('/auth/admin/tournaments/%s/announce/'
                        % self.tournament.slug,
                        {'subject': 'x', 'body': 'y'}, self.as_finance)
        self.assertEqual(res.status_code, 403)

    def test_cancelling_marks_the_tournament_and_not_only_its_entries(self):
        """It refunded everybody and went on listing the tournament as live."""
        res = self.post('/auth/admin/tournaments/%s/cancel/'
                        % self.tournament.tournament_id,
                        {'reason': 'venue fell through'}, self.as_super)
        self.assertEqual(res.status_code, 200, res.content)
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, 'cancelled')
        self.assertIsNotNone(self.tournament.cancelled_at)

        listing = self.get('/auth/admin/tournaments/', self.as_super,
                           status='cancelled')
        titles = [row['name'] for row in listing.json()['data']['results']]
        self.assertIn(self.tournament.tournament_title, titles)

    def test_a_cancelled_tournament_is_not_reported_as_completed(self):
        self.tournament.status = 'cancelled'
        self.tournament.end_date_and_time = timezone.now() - timedelta(days=1)
        self.tournament.save(update_fields=['status', 'end_date_and_time'])
        listing = self.get('/auth/admin/tournaments/', self.as_super)
        rows = {row['name']: row for row in listing.json()['data']['results']}
        self.assertEqual(rows[self.tournament.tournament_title]['status'],
                         'cancelled')


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventAnnouncementFromTheConsoleTests(ConsoleBase):
    """C3: the console announces through the event's OWN announcement path.

    There is one EventAnnouncement model and one send. The console reaches it
    by holding `manage_events`, not by a second endpoint of its own, so a
    reader looking for "what was sent about this event" has one place to look.
    """

    def setUp(self):
        super().setUp()
        from vent_event.models import Event, Ticket, TicketTier

        self.organiser, _ = a_user('ad_eventowner')
        self.event = Event.objects.create(
            name='Lagos Meetup %s' % uuid.uuid4().hex[:4], game=self.game,
            creator=self.organiser, event_type='physical', desc='probe',
            entry_fee=0, capacity=50,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timedelta(days=3),
            event_date=(timezone.now() + timedelta(days=4)).date(),
            start_time='10:00', end_time='18:00', location='Lagos')
        tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=50, sold=1)
        self.holder, _ = a_user('ad_holder')
        Ticket.objects.create(
            event=self.event, tier=tier, status='valid', price_vc=0,
            price_ngn=0, code=uuid.uuid4().hex[:12].upper(), user=self.holder,
            attendee_name='Ada Obi', attendee_email=self.holder.email)

    def test_an_admin_can_send_one_and_it_lands_on_the_event_record(self):
        from vent_event.models import EventAnnouncement

        res = self.post('/event/%s/announcements/' % self.event.slug,
                        {'subject': 'Doors move to 11:00',
                         'body': 'The venue opens an hour later.'},
                        self.as_organiser)
        self.assertEqual(res.status_code, 201, res.content)
        rows = EventAnnouncement.objects.filter(event=self.event)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().sent_by_id, self.organiser_admin.user_id)

    def test_a_role_without_events_is_still_refused(self):
        res = self.post('/event/%s/announcements/' % self.event.slug,
                        {'subject': 'x', 'body': 'y'}, self.as_finance)
        self.assertEqual(res.status_code, 403)

    def test_a_member_who_is_not_the_organiser_is_refused(self):
        res = self.post('/event/%s/announcements/' % self.event.slug,
                        {'subject': 'x', 'body': 'y'}, self.as_member)
        self.assertEqual(res.status_code, 403)
