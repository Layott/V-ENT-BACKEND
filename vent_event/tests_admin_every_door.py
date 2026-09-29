"""An admin who may manage events gets one answer at every door of an event.

The seven-role walk on 28 September opened an event's console as a super admin:
Money and Tiers answered, Numbers, Earnings and Attendees refused with 403.
Seven views asked the admin override beside may_run_event and twenty-five did
not. The override is asked inside may_run_event now, so these doors cannot
disagree again; the edit view still tells an admin's edit from the owner's,
through runs_event_itself, so it is still audited and the organiser still told.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games

from .models import Event
from .permissions import may_run_event, may_work_the_door, runs_event_itself
from .tests_edit_event import a_user, console_auth

DOORS = ('money', 'tiers', 'metrics', 'earnings', 'attendees', 'door-summary')


class AdminEveryDoorTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        self.admin, _ = a_user('boss', is_staff=True, admin_role='super_admin')
        self.finance, _ = a_user('fin', is_staff=True, admin_role='finance_admin')
        self.stranger, self.stranger_auth = a_user('nosy')
        game, _ = Games.objects.get_or_create(game_title='EA FC 25')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Lagos Anime Con', game=game, creator=self.owner, event_type='physical',
            category='anime', desc='A day of panels.', location='Lagos', capacity=400,
            entry_fee=0, start_date=now + timedelta(days=20), end_date=now + timedelta(days=21),
        )

    def get(self, door, headers):
        return self.client.get('/event/%s/%s/' % (self.event.event_id, door), **headers)

    def test_an_admin_is_answered_at_every_door(self):
        headers = console_auth(self.admin, 'boss-every-door')
        for door in DOORS:
            with self.subTest(door=door):
                self.assertEqual(self.get(door, headers).status_code, 200)

    def test_the_owner_is_answered_at_every_door(self):
        for door in DOORS:
            with self.subTest(door=door):
                self.assertEqual(self.get(door, self.owner_auth).status_code, 200)

    def test_a_stranger_is_refused_at_every_door(self):
        for door in DOORS:
            with self.subTest(door=door):
                self.assertEqual(self.get(door, self.stranger_auth).status_code, 403)

    def test_an_admin_role_without_manage_events_is_refused(self):
        headers = console_auth(self.finance, 'fin-every-door')
        for door in DOORS:
            with self.subTest(door=door):
                self.assertEqual(self.get(door, headers).status_code, 403)

    def test_an_admin_session_without_the_second_factor_is_refused(self):
        self.admin.login_session_2fa_at = None
        self.admin.save(update_fields=['login_session_2fa_at'])
        self.assertFalse(may_run_event(self.admin, self.event))

    def test_the_rule_and_its_narrower_question_disagree_only_for_the_admin(self):
        console_auth(self.admin, 'boss-rule')
        self.admin.refresh_from_db()
        self.assertTrue(may_run_event(self.admin, self.event))
        self.assertTrue(may_work_the_door(self.admin, self.event))
        self.assertFalse(runs_event_itself(self.admin, self.event))
        self.assertTrue(runs_event_itself(self.owner, self.event))
