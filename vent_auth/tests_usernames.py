"""What a username may be, in one place.

Written because four different code paths chose usernames and disagreed, and
because a styled-unicode handle renders as somebody else's name.
"""
from django.test import TestCase

from vent_auth.models import Users
from vent_auth.views_helpers import normalize_username, username_problem, username_taken


class UsernameRuleTests(TestCase):
    def test_case_does_not_make_a_new_name(self):
        Users.objects.create(username='layott', email='a@vent.test')
        self.assertTrue(username_taken('Layott'))
        self.assertTrue(username_taken('LAYOTT'))
        self.assertTrue(username_taken('  LaYoTt '))

    def test_a_free_name_is_free(self):
        self.assertFalse(username_taken('someone_else'))

    def test_the_holder_may_keep_their_own_name(self):
        user = Users.objects.create(username='winlola', email='b@vent.test')
        self.assertFalse(username_taken('Winlola', exclude_user=user))

    def test_styled_unicode_fonts_are_refused(self):
        # These render as "layott" in most clients and are not letters.
        for styled in ['𝓵𝓪𝔂𝓸𝓽𝓽', '𝗹𝗮𝘆𝗼𝘁𝘁', 'ｌａｙｏｔｔ', 'lаyott']:  # the last has a Cyrillic а
            self.assertIsNotNone(username_problem(styled), styled)

    def test_spaces_and_symbols_are_refused(self):
        for bad in ['la yott', 'la-yott', 'la.yott', 'layott!', '<script>']:
            self.assertIsNotNone(username_problem(bad), bad)

    def test_length_bounds(self):
        self.assertIsNotNone(username_problem('ab'))
        self.assertIsNotNone(username_problem('a' * 21))
        self.assertIsNone(username_problem('abc'))
        self.assertIsNone(username_problem('a' * 20))

    def test_plain_names_pass_and_normalise(self):
        self.assertIsNone(username_problem('Layott_01'))
        self.assertEqual(normalize_username('  Layott_01 '), 'layott_01')


class OnboardingDoorTests(TestCase):
    """edit-profile-info, the door onboarding renames through (inbox 461).

    It saved whatever was typed: "keiji_is_lost " was stored with its space and
    that profile never opened.
    """

    def setUp(self):
        from django.utils import timezone
        from vent_auth.models import UserProfile
        from rest_framework.test import APIClient
        self.user = Users.objects.create(
            username='newcomer_1', email='newcomer@vent.test', is_active=True,
            login_session_token='tknewcomer1', login_session_created_at=timezone.now())
        UserProfile.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer tknewcomer1')

    def rename(self, name):
        return self.client.post('/auth/edit-profile-info/', {'username': name}, format='multipart')

    def test_a_trailing_space_and_capitals_are_stored_as_compared(self):
        res = self.rename('Keiji_Is_Lost ')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'keiji_is_lost')

    def test_a_name_with_a_space_inside_is_refused_with_a_code(self):
        res = self.rename('keiji is lost')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'USERNAME_INVALID')
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'newcomer_1')

    def test_too_long_is_refused_like_signup(self):
        res = self.rename('a' * 21)
        self.assertEqual(res.json()['code'], 'USERNAME_INVALID')

    def test_resending_your_own_name_in_other_case_changes_nothing(self):
        Users.objects.filter(pk=self.user.pk).update(username='Newcomer_1')
        res = self.rename('newcomer_1')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'Newcomer_1')


class StripUsernamesMigrationTests(TestCase):
    """0093 repairs what the onboarding door already wrote."""

    def run_it(self):
        import importlib
        from django.apps import apps
        mod = importlib.import_module('vent_auth.migrations.0093_strip_usernames')
        mod.strip_usernames(apps, None)

    def test_a_trailing_space_is_removed(self):
        u = Users.objects.create(username='keiji_is_lost ', email='k@vent.test')
        self.run_it()
        u.refresh_from_db()
        self.assertEqual(u.username, 'keiji_is_lost')

    def test_a_clash_is_left_alone_not_merged(self):
        Users.objects.create(username='taken_name', email='t@vent.test')
        u = Users.objects.create(username='taken_name ', email='t2@vent.test')
        self.run_it()
        u.refresh_from_db()
        self.assertEqual(u.username, 'taken_name ')
