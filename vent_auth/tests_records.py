"""The admin console's records, every model on the site (inbox 420).

Two kinds of test. The registry tests need no database: they read every model
Django knows and fail when one is neither listed nor excluded with a reason, or
carries a secret-looking or money-looking column nobody has marked. Those are the
catchers: a model added next month is either handled or this file says so.

The rest walk the doors as the people who use them: a super admin, an admin
(Edit records, no money), a support admin (looks only), a finance admin (money,
no Edit records), a signed-in stranger and nobody at all.
"""
import uuid
from datetime import timedelta
from io import StringIO
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth import records
from vent_auth.models import (AdminAction, Club, ClubMember, Games, RecordBin, RecordVersion,
                              Transaction, Users, UserWallet)

BASE = '/auth/admin/records/'


class RegistryTests(SimpleTestCase):
    def test_every_model_is_listed_or_excluded_with_a_reason(self):
        listed = set(map(records.key_of, records.listed_models()))
        for model in apps.get_models():
            key = records.key_of(model)
            if model._meta.proxy:
                continue
            app = model._meta.app_label
            excluded = records.EXCLUDED_APPS.get(app) or records.EXCLUDED_MODELS.get(key)
            self.assertTrue(key in listed or excluded,
                            '%s is neither listed nor excluded with a reason' % key)
            if excluded:
                self.assertTrue(str(excluded).strip(), '%s is excluded without a reason' % key)

    def test_every_vent_model_appears_without_being_named(self):
        """Automatic: nothing lists V-ENT models one by one."""
        listed = set(map(records.key_of, records.listed_models()))
        ours = [m for m in apps.get_models() if m._meta.app_label.startswith('vent')]
        self.assertGreater(len(ours), 150)
        for model in ours:
            key = records.key_of(model)
            if key in records.EXCLUDED_MODELS:
                continue
            self.assertIn(key, listed)

    def test_every_mark_names_a_real_model_and_column(self):
        known = {records.key_of(m): m for m in apps.get_models()}
        for table in (records.MONEY, records.NOT_MONEY, records.EXCLUDED_MODELS):
            for key in table:
                self.assertIn(key, known, 'stale mark: %s' % key)
        self.assertFalse(set(records.MONEY) & set(records.NOT_MONEY))
        for key, fields in records.SENSITIVE.items():
            self.assertIn(key, known, 'stale mark: %s' % key)
            names = {f.name for f in known[key]._meta.concrete_fields}
            for name in fields:
                self.assertIn(name, names, 'stale mark: %s.%s' % (key, name))
        for path in records.NOT_SENSITIVE:
            key, name = path.rsplit('.', 1)
            self.assertIn(key, known, 'stale mark: %s' % path)
            self.assertIn(name, {f.name for f in known[key]._meta.concrete_fields}, 'stale mark: %s' % path)

    def test_no_secret_looking_column_is_unmarked(self):
        self.assertEqual(records.unmarked_secrets(), [])

    def test_no_money_looking_model_is_unmarked(self):
        self.assertEqual(records.unmarked_money(), [])

    def test_the_secret_catcher_catches_a_column_nobody_marked(self):
        trimmed = {k: v for k, v in records.SENSITIVE.items() if k != 'vent_auth.users'}
        with mock.patch.object(records, 'SENSITIVE', trimmed):
            self.assertIn('vent_auth.users.password', records.unmarked_secrets())

    def test_the_money_catcher_catches_a_model_nobody_marked(self):
        trimmed = {k: v for k, v in records.MONEY.items() if k != 'vent_auth.transaction'}
        with mock.patch.object(records, 'MONEY', trimmed):
            self.assertTrue(any(r.startswith('vent_auth.transaction') for r in records.unmarked_money()))

    def test_money_models_have_no_editable_column(self):
        for model in records.listed_models():
            if records.is_money(model):
                for field in model._meta.concrete_fields:
                    self.assertFalse(records.editable(model, field), '%s.%s' % (model, field.name))


def person(name, **extra):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:4]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:4]),
        full_name=name.title(),
        login_session_token=('rk%s' % uuid.uuid4().hex)[:16],
        **extra)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.set_password('a-long-password-%s' % uuid.uuid4().hex)
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class RecordsBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.super, self.super_auth = person('rec_super', is_staff=True, admin_role='super_admin')
        self.admin, self.admin_auth = person('rec_admin', is_staff=True, admin_role='admin')
        self.support, self.support_auth = person('rec_support', is_staff=True, admin_role='support_admin')
        self.finance, self.finance_auth = person('rec_finance', is_staff=True, admin_role='finance_admin')
        self.stranger, self.stranger_auth = person('rec_stranger')
        self.game = Games.objects.create(game_title='Records Game %s' % uuid.uuid4().hex[:4],
                                         description='before')

    def get(self, path, auth, **params):
        return self.client.get(BASE + path, params, **auth)

    def send(self, method, path, auth, body=None):
        return getattr(self.client, method)(BASE + path, body or {}, format='json', **auth)


class DoorTests(RecordsBase):
    def test_nobody_and_a_stranger_are_refused(self):
        self.assertIn(self.client.get(BASE).status_code, (401, 403))
        res = self.get('', self.stranger_auth)
        self.assertIn(res.status_code, (401, 403))

    def test_support_may_look(self):
        res = self.get('', self.support_auth)
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        self.assertFalse(data['may_edit'])
        keys = {m['key'] for g in data['groups'] for m in g['models']}
        self.assertIn('vent_auth.users', keys)
        self.assertIn('vent_event.ticket', keys)
        self.assertNotIn('vent_auth.recordbin', keys)

    def test_the_super_admin_may_do_everything(self):
        data = self.get('', self.super_auth).json()['data']
        self.assertTrue(data['may_edit'] and data['may_purge'] and data['may_correct'])


class ReadingTests(RecordsBase):
    def test_a_record_shows_every_field_its_links_and_hides_secrets(self):
        UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=self.stranger)
        res = self.get('vent_auth.users/%s/' % self.stranger.pk, self.support_auth)
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertNotIn(self.stranger.password, body)
        self.assertNotIn(self.stranger.login_session_token, body)
        data = res.json()['data']
        fields = {f['name']: f for f in data['fields']}
        self.assertEqual(fields['password']['value'], {'hidden': True})
        self.assertEqual(fields['login_session_token']['value'], {'hidden': True})
        self.assertFalse(fields['password']['editable'])
        self.assertEqual(fields['username']['value'], self.stranger.username)
        self.assertIn('vent_auth.userwallet', {l['model'] for l in data['linked']})

    def test_a_search_forgives_a_typo(self):
        Club.objects.create(name='Lagos Rangers Club', owner=self.stranger)
        res = self.get('search/', self.support_auth, q='lagos rangrs')
        self.assertEqual(res.status_code, 200)
        labels = [r['label'] for g in res.json()['data']['results'] for r in g['rows']]
        self.assertIn('Lagos Rangers Club', labels)
        res = self.get('vent_auth.club/', self.support_auth, q='lagos rangrs')
        self.assertEqual([r['label'] for r in res.json()['data']['rows']], ['Lagos Rangers Club'])

    def test_an_unknown_kind_and_record_are_404(self):
        self.assertEqual(self.get('vent_auth.nothing/', self.super_auth).status_code, 404)
        self.assertEqual(self.get('vent_auth.games/999999/', self.super_auth).status_code, 404)


class EditingTests(RecordsBase):
    path = None

    def setUp(self):
        super().setUp()
        self.path = 'vent_auth.games/%s/' % self.game.pk

    def test_support_may_not_change_a_record(self):
        res = self.send('patch', self.path, self.support_auth,
                        {'fields': {'description': 'after'}, 'reason': 'typo'})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NO_EDIT_RECORDS')
        self.game.refresh_from_db()
        self.assertEqual(self.game.description, 'before')

    def test_an_edit_keeps_the_old_value_and_can_be_undone(self):
        res = self.send('patch', self.path, self.admin_auth,
                        {'fields': {'description': 'after'}, 'reason': 'typo in the description'})
        self.assertEqual(res.status_code, 200, res.content)
        self.game.refresh_from_db()
        self.assertEqual(self.game.description, 'after')
        version = RecordVersion.objects.get(model_label='vent_auth.games', object_pk=str(self.game.pk))
        self.assertEqual(version.changes, {'description': ['before', 'after']})
        self.assertTrue(AdminAction.objects.filter(action_type='record_edit', admin=self.admin).exists())
        self.assertEqual(len(res.json()['data']['versions']), 1)

        res = self.send('post', 'versions/%s/revert/' % version.pk, self.admin_auth, {})
        self.assertEqual(res.status_code, 200, res.content)
        self.game.refresh_from_db()
        self.assertEqual(self.game.description, 'before')
        undo = RecordVersion.objects.exclude(pk=version.pk).get()
        self.assertEqual(undo.reverts_id, version.pk)
        self.assertTrue(AdminAction.objects.filter(action_type='record_revert').exists())

    def test_an_edit_needs_a_reason(self):
        res = self.send('patch', self.path, self.admin_auth, {'fields': {'description': 'after'}})
        self.assertEqual(res.json()['code'], 'REASON_REQUIRED')

    def test_a_secret_column_cannot_be_changed(self):
        res = self.send('patch', 'vent_auth.users/%s/' % self.stranger.pk, self.super_auth,
                        {'fields': {'password': 'x'}, 'reason': 'try'})
        self.assertEqual(res.json()['code'], 'NOT_EDITABLE')

    def test_a_value_the_column_cannot_hold_is_refused(self):
        res = self.send('patch', self.path, self.admin_auth,
                        {'fields': {'sort_order': 'many'}, 'reason': 'try'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'INVALID_VALUE')


class MoneyTests(RecordsBase):
    def setUp(self):
        super().setUp()
        self.wallet = UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10],
                                                user=self.stranger, wallet_balance=Decimal('10'))
        self.other_wallet = UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10],
                                                      user=self.support, wallet_balance=Decimal('50'))
        self.line = Transaction.objects.create(wallet=self.wallet, type='top_up', amount=Decimal('10'),
                                               description='Top up', status='completed')

    def test_a_money_record_cannot_be_edited_or_deleted(self):
        path = 'vent_auth.transaction/%s/' % self.line.pk
        res = self.send('patch', path, self.super_auth, {'fields': {'description': 'x'}, 'reason': 'try'})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'MONEY_IS_VIEW_ONLY')
        res = self.send('post', path + 'delete/', self.super_auth, {'reason': 'try'})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'MONEY_IS_VIEW_ONLY')
        self.assertTrue(Transaction.objects.filter(pk=self.line.pk, description='Top up').exists())

    def test_deleting_something_whose_cascade_reaches_money_is_refused(self):
        res = self.send('post', 'vent_auth.users/%s/delete/' % self.stranger.pk, self.super_auth,
                        {'reason': 'try'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'CASCADE_TOUCHES_MONEY')
        self.assertTrue(Users.objects.filter(pk=self.stranger.pk).exists())

    def test_a_correction_is_a_new_entry_with_a_reason(self):
        path = 'vent_auth.transaction/%s/correct/' % self.line.pk
        body = {'direction': 'credit', 'amount': 3, 'other_kind': 'user',
                'other': self.support.username, 'reason': 'charged twice on 9 October'}
        res = self.send('post', path, self.admin_auth, body)      # Edit records, no money role
        self.assertEqual(res.status_code, 403)
        res = self.send('post', path, self.finance_auth, body)    # money role, no Edit records
        self.assertEqual(res.status_code, 403)
        before = Transaction.objects.count()
        res = self.send('post', path, self.super_auth, body)
        self.assertEqual(res.status_code, 200, res.content)
        self.wallet.refresh_from_db()
        self.other_wallet.refresh_from_db()
        self.assertEqual(self.wallet.wallet_balance, Decimal('13'))
        self.assertEqual(self.other_wallet.wallet_balance, Decimal('47'))
        self.assertEqual(Transaction.objects.count(), before + 2)
        self.line.refresh_from_db()
        self.assertEqual(self.line.amount, Decimal('10'))           # the original says what happened
        action = AdminAction.objects.get(action_type='record_correct')
        self.assertEqual(action.reason, 'charged twice on 9 October')

    def test_a_correction_needs_a_reason(self):
        res = self.send('post', 'vent_auth.userwallet/%s/correct/' % self.wallet.pk, self.super_auth,
                        {'direction': 'debit', 'amount': 1, 'other_kind': 'user',
                         'other': self.support.username})
        self.assertEqual(res.json()['code'], 'REASON_REQUIRED')


class BinTests(RecordsBase):
    def setUp(self):
        super().setUp()
        self.club = Club.objects.create(name='Bin Club %s' % uuid.uuid4().hex[:4],
                                        owner=self.stranger, game=self.game)
        self.member = ClubMember.objects.create(club=self.club, user=self.support)

    def test_delete_moves_a_record_and_what_goes_with_it_to_the_bin(self):
        path = 'vent_auth.club/%s/delete/' % self.club.pk
        preview = self.get(path, self.support_auth).json()['data']
        self.assertTrue(preview['allowed'])
        self.assertEqual(preview['counts'].get('vent_auth.clubmember'), 1)

        res = self.send('post', path, self.support_auth, {'reason': 'spam'})
        self.assertEqual(res.status_code, 403)
        res = self.send('post', path, self.admin_auth, {'reason': 'spam club'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(Club.objects.filter(pk=self.club.pk).exists())
        self.assertFalse(ClubMember.objects.filter(pk=self.member.pk).exists())
        entry = RecordBin.objects.get()
        self.assertEqual(entry.object_pk, str(self.club.pk))      # not "None": read before the delete
        self.assertEqual(entry.label, self.club.name)
        self.assertEqual(AdminAction.objects.get(action_type='record_delete').target_id, str(self.club.pk))
        self.assertEqual(entry.purge_after.date(), (timezone.now() + timedelta(days=90)).date())
        self.assertTrue(AdminAction.objects.filter(action_type='record_delete').exists())
        listed = self.get('bin/', self.support_auth).json()['data']['rows']
        self.assertEqual([r['id'] for r in listed], [entry.pk])
        self.assertNotIn('snapshot', listed[0])

        res = self.send('post', 'bin/%s/restore/' % entry.pk, self.admin_auth, {})
        self.assertEqual(res.status_code, 200, res.content)
        club = Club.objects.get(pk=self.club.pk)
        self.assertEqual(club.name, self.club.name)
        self.assertTrue(ClubMember.objects.filter(pk=self.member.pk, club=club).exists())
        self.assertTrue(AdminAction.objects.filter(action_type='record_restore').exists())
        res = self.send('post', 'bin/%s/restore/' % entry.pk, self.admin_auth, {})
        self.assertEqual(res.json()['code'], 'ALREADY_RESTORED')

    def test_a_column_set_to_nothing_by_the_delete_comes_back(self):
        """Clubs point at a game with SET_NULL: deleting the game empties it."""
        res = self.send('post', 'vent_auth.games/%s/delete/' % self.game.pk, self.super_auth,
                        {'reason': 'duplicate game'})
        self.assertEqual(res.status_code, 200, res.content)
        self.club.refresh_from_db()
        self.assertIsNone(self.club.game_id)
        entry = RecordBin.objects.get()
        self.send('post', 'bin/%s/restore/' % entry.pk, self.super_auth, {})
        self.club.refresh_from_db()
        self.assertEqual(self.club.game_id, self.game.pk)

    def test_a_restore_that_would_overwrite_something_is_refused(self):
        self.send('post', 'vent_auth.club/%s/delete/' % self.club.pk, self.admin_auth, {'reason': 'x'})
        Club.objects.create(pk=self.club.pk, name='Somebody else', owner=self.stranger)
        entry = RecordBin.objects.get()
        res = self.send('post', 'bin/%s/restore/' % entry.pk, self.admin_auth, {})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'RESTORE_CONFLICT')
        self.assertEqual(Club.objects.get(pk=self.club.pk).name, 'Somebody else')

    def test_only_a_super_admin_purges(self):
        self.send('post', 'vent_auth.club/%s/delete/' % self.club.pk, self.admin_auth, {'reason': 'x'})
        entry = RecordBin.objects.get()
        res = self.send('delete', 'bin/%s/' % entry.pk, self.admin_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NO_PURGE_RECORDS')
        res = self.send('delete', 'bin/%s/' % entry.pk, self.super_auth)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(RecordBin.objects.exists())
        self.assertTrue(AdminAction.objects.filter(action_type='record_purge').exists())

    def test_the_nightly_purge_keeps_its_ninety_days(self):
        self.send('post', 'vent_auth.club/%s/delete/' % self.club.pk, self.admin_auth, {'reason': 'x'})
        call_command('purge_record_bin', stdout=StringIO())
        self.assertTrue(RecordBin.objects.exists())
        RecordBin.objects.update(purge_after=timezone.now() - timedelta(minutes=1))
        call_command('purge_record_bin', stdout=StringIO())
        self.assertFalse(RecordBin.objects.exists())


class SoftDeleteTests(RecordsBase):
    def setUp(self):
        super().setUp()
        from vent_event.models import Event, TicketTier
        self.event = Event.objects.create(
            name='Records Con %s' % uuid.uuid4().hex[:4], game=self.game, creator=self.stranger,
            event_type='physical', desc='probe', entry_fee=0, capacity=100,
            reg_start_date=timezone.now(), reg_end_date=timezone.now() + timedelta(days=5),
            event_date=(timezone.now() + timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00', location='Lagos')
        self.tier = TicketTier.objects.create(event=self.event, name='General', price=5000,
                                              quantity=10, sold=0)

    def test_an_event_deletes_softly_and_comes_back(self):
        from vent_event.models import Event
        res = self.send('post', 'vent_event.event/%s/delete/' % self.event.pk, self.admin_auth,
                        {'reason': 'test event'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(Event.all_objects.filter(pk=self.event.pk, deleted_reason='test event').exists())
        entry = RecordBin.objects.get()
        self.send('post', 'bin/%s/restore/' % entry.pk, self.admin_auth, {})
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_an_event_somebody_paid_for_is_refused(self):
        from vent_event.models import Ticket
        Ticket.objects.create(event=self.event, tier=self.tier, status='valid', price_vc=5,
                              price_ngn=5000, code=uuid.uuid4().hex[:12].upper(),
                              attendee_name='Ada', attendee_email='ada@example.test')
        res = self.send('post', 'vent_event.event/%s/delete/' % self.event.pk, self.admin_auth,
                        {'reason': 'x'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'PAID_ENTRANTS')
