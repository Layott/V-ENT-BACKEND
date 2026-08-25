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
