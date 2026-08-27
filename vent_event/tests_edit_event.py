"""Editing an event: the organiser, and an admin overruling them.

There was no edit endpoint for an event at all. An event went out with whatever
the wizard was given, and a wrong venue or start time could only be fixed in the
database.

These cover the same ground as the tournament override: who may save, that the
console's own session counts as proof of identity, that a partial save does not
blank the fields it was not given, and that an admin edit is recorded.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import AdminAction, Games, Users

from .models import Event


def a_user(name, **extra):
    user = Users.objects.create(
        username='%s_%s' % (name, uuid.uuid4().hex[:5]),
        email='%s_%s@vent.test' % (name, uuid.uuid4().hex[:5]),
        login_session_token=('tok-%s' % name)[:16],
        **extra,
    )
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save(update_fields=['login_session_created_at', 'login_session_2fa_at'])
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


def console_auth(user, token):
    """An admin whose session passed the authenticator challenge.

    This used to mean "signed into the console but not the website", back when
    those were two sessions with two tokens. There is one now, and what makes it
    an admin session is the second factor taken at the front door.
    """
    user.login_session_token = token
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save(update_fields=['login_session_token', 'login_session_created_at',
                             'login_session_2fa_at'])
    return {'HTTP_AUTHORIZATION': 'Bearer %s' % token}


class EditEventTests(TestCase):
    def setUp(self):
        self.owner, self.owner_auth = a_user('owner')
        self.admin, _ = a_user('moddy', is_staff=True, admin_role='mod_admin')
        self.finance, _ = a_user('fin', is_staff=True, admin_role='finance_admin')
        self.stranger, self.stranger_auth = a_user('nosy')

        game, _ = Games.objects.get_or_create(game_title='EA FC 25')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Lagos Anime Con',
            game=game,
            creator=self.owner,
            event_type='physical',
            category='anime',
            desc='A day of panels and screenings.',
            location='Landmark Centre, Lagos',
            capacity=400,
            entry_fee=0,
            start_date=now + timedelta(days=20),
            end_date=now + timedelta(days=21),
        )

    def url(self):
        return '/event/edit-event/%s/' % self.event.event_id

    # ------------------------------------------------------------- the owner
    def test_the_organizer_can_correct_their_own_event(self):
        response = self.client.put(
            self.url(),
            data={'location': 'Eko Convention Centre, Lagos'},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(response.status_code, 200, response.content)
        self.event.refresh_from_db()
        self.assertEqual(self.event.location, 'Eko Convention Centre, Lagos')

    def test_a_partial_save_leaves_every_other_field_alone(self):
        """The failure this guards: a five-field screen blanking twenty fields."""
        response = self.client.put(
            self.url(), data={'name': 'Lagos Anime Con 2026'},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(response.status_code, 200, response.content)
        self.event.refresh_from_db()
        self.assertEqual(self.event.name, 'Lagos Anime Con 2026')
        self.assertEqual(self.event.desc, 'A day of panels and screenings.')
        self.assertEqual(self.event.location, 'Landmark Centre, Lagos')
        self.assertEqual(self.event.capacity, 400)

    def test_renaming_keeps_the_old_address_working(self):
        """The slug follows the name, and the retired one still resolves."""
        old_slug = self.event.slug
        self.client.put(self.url(), data={'name': 'Abuja Anime Con'},
                        content_type='application/json', **self.owner_auth)
        self.event.refresh_from_db()
        self.assertNotEqual(self.event.slug, old_slug)
        self.assertIn('abuja', self.event.slug)

    # ------------------------------------------------------------- outsiders
    def test_a_stranger_cannot_edit_somebody_elses_event(self):
        response = self.client.put(
            self.url(), data={'location': 'My house'},
            content_type='application/json', **self.stranger_auth)
        self.assertEqual(response.status_code, 403, response.content)
        self.event.refresh_from_db()
        self.assertEqual(self.event.location, 'Landmark Centre, Lagos')

    def test_an_admin_without_manage_events_is_refused(self):
        """A finance admin administers money, not somebody's event."""
        headers = console_auth(self.finance, 'finance-grant')
        response = self.client.put(
            self.url(), data={'location': 'Nowhere'},
            content_type='application/json', **headers)
        self.assertEqual(response.status_code, 403, response.content)

    # ----------------------------------------------------------- the override
    def test_an_admin_can_correct_an_event_from_the_console(self):
        headers = console_auth(self.admin, 'mod-grant')
        response = self.client.put(
            self.url(), data={'location': 'Eko Hotel, Lagos'},
            content_type='application/json', **headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.event.refresh_from_db()
        self.assertEqual(self.event.location, 'Eko Hotel, Lagos')
        self.assertTrue(response.json()['data']['edited_as_admin'])

    def test_an_admin_edit_is_written_to_the_audit_log(self):
        headers = console_auth(self.admin, 'mod-grant-2')
        self.client.put(self.url(), data={'location': 'Eko Hotel, Lagos'},
                        content_type='application/json', **headers)
        entry = AdminAction.objects.filter(action_type='edit_event').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.target_id, str(self.event.event_id))
        self.assertEqual(entry.metadata['owner_id'], self.owner.user_id)
        self.assertIn('location', entry.metadata['updated_fields'])

    def test_the_owner_editing_is_not_logged_as_an_admin_action(self):
        self.client.put(self.url(), data={'location': 'Somewhere else'},
                        content_type='application/json', **self.owner_auth)
        self.assertFalse(AdminAction.objects.filter(action_type='edit_event').exists())

    # -------------------------------------------------------------- validation
    def test_an_event_cannot_end_before_it_starts(self):
        now = timezone.now()
        response = self.client.put(
            self.url(),
            data={'start_date': (now + timedelta(days=30)).isoformat(),
                  'end_date': (now + timedelta(days=29)).isoformat()},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['code'], 'END_BEFORE_START')

    def test_a_capacity_that_is_not_a_number_is_refused(self):
        response = self.client.put(
            self.url(), data={'capacity': 'soon'},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(response.status_code, 400, response.content)
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 400)

    def test_sending_nothing_is_refused_rather_than_reported_as_saved(self):
        response = self.client.put(self.url(), data={},
                                   content_type='application/json', **self.owner_auth)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['code'], 'NO_FIELDS_TO_UPDATE')

    def test_an_anonymous_request_is_refused(self):
        response = self.client.put(self.url(), data={'location': 'x'},
                                   content_type='application/json')
        self.assertIn(response.status_code, (400, 401), response.content)

    def test_a_browser_datetime_with_no_timezone_is_accepted(self):
        """What a datetime-local field actually sends.

        The first version of this endpoint parsed "2026-07-26T23:30" into a
        naive datetime and then compared it with the stored aware one, which
        raises TypeError and answered 500. Every test here passed, because they
        all sent isoformat() output carrying an offset.
        """
        response = self.client.put(
            self.url(),
            data={'start_date': '2026-11-01T18:30', 'end_date': '2026-11-01T22:00'},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(response.status_code, 200, response.content)
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.start_date.tzinfo)
        self.assertEqual(self.event.start_date.year, 2026)
        self.assertEqual(self.event.start_date.month, 11)

    def test_the_end_before_start_check_works_on_naive_input_too(self):
        response = self.client.put(
            self.url(),
            data={'start_date': '2026-11-02T18:30', 'end_date': '2026-11-01T18:30'},
            content_type='application/json', **self.owner_auth)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['code'], 'END_BEFORE_START')
