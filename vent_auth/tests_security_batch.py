"""The second security batch, R62 to R86 (inbox 363, 30 September 2026).

Each class proves one promise both ways: what it must refuse, and what it must
let through, because a guard that refuses everything passes half its tests.
"""
import io
import time

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from vent_auth import bot_check, inputs, uploads
from vent_auth.models import UsedChallenge


def png_bytes():
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (4, 4), (200, 30, 30)).save(out, format='PNG')
    return out.getvalue()


# ---------------------------------------------------------------------------
# R68: the proof-of-work bot check
# ---------------------------------------------------------------------------

@override_settings(BOT_CHECK_ENABLED=True)
class BotCheckTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _answer(self):
        body = self.client.get('/auth/challenge/').json()
        self.assertEqual(body['status'], 'success')
        return bot_check.solve(body['data'])

    def test_a_solved_puzzle_passes_once(self):
        answer = self._answer()
        self.assertIsNone(bot_check.problem(answer))
        self.assertEqual(bot_check.problem(answer), 'used twice')
        self.assertEqual(UsedChallenge.objects.count(), 1)

    def test_a_wrong_number_is_refused(self):
        answer = self._answer()
        answer['number'] = (answer['number'] + 1) % bot_check.MAX_NUMBER
        self.assertEqual(bot_check.problem(answer), 'wrong answer')

    def test_a_puzzle_we_did_not_sign_is_refused(self):
        answer = self._answer()
        answer['signature'] = '0' * 64
        self.assertEqual(bot_check.problem(answer), 'not ours')

    def test_an_expired_puzzle_is_refused(self):
        answer = self._answer()
        later = time.time() + bot_check.LIFETIME_SECONDS + 5
        self.assertEqual(bot_check.problem(answer, now=later), 'expired')

    def test_a_filled_honeypot_is_refused_even_with_a_good_answer(self):
        self.assertEqual(bot_check.problem(self._answer(), honeypot='http://x'), 'honeypot')

    def test_nothing_sent_is_refused(self):
        self.assertEqual(bot_check.problem(None), 'missing')
        self.assertEqual(bot_check.problem('not json'), 'missing')

    def test_signup_without_an_answer_is_refused_before_anything_is_stored(self):
        from vent_auth.models import Users
        response = self.client.post('/auth/signup/', {
            'email': 'bot@example.com', 'username': 'botbot', 'password': 'Longer-pass-99',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'BOT_CHECK_FAILED')
        self.assertFalse(Users.objects.filter(email='bot@example.com').exists())

    def test_signup_with_an_answer_gets_past_the_check(self):
        response = self.client.post('/auth/signup/', {
            'email': 'person@example.com', 'username': 'aperson',
            'password': 'Longer-pass-99', 'challenge': self._answer(),
        }, format='json')
        self.assertNotEqual(response.json().get('code'), 'BOT_CHECK_FAILED')

    def test_the_waitlist_and_feedback_doors_ask_too(self):
        for path, body in (('/auth/add-email-to-waitlist/', {'email': 'w@example.com'}),
                           ('/auth/feedback/', {'message': 'The bracket page is slow.'})):
            response = self.client.post(path, body, format='json')
            self.assertEqual(response.json()['code'], 'BOT_CHECK_FAILED', path)


# ---------------------------------------------------------------------------
# R69: typed input readers
# ---------------------------------------------------------------------------

class InputReaderTests(TestCase):
    def test_whole_numbers(self):
        self.assertEqual(inputs.read_int({'n': '12'}, 'n'), 12)
        self.assertEqual(inputs.read_int({'n': 12.0}, 'n'), 12)
        self.assertIsNone(inputs.read_int({}, 'n'))
        for bad in ('abc', '12abc', True, 1.5, [1]):
            with self.assertRaises(inputs.BadInput, msg=repr(bad)):
                inputs.read_int({'n': bad}, 'n')
        with self.assertRaises(inputs.BadInput):
            inputs.read_int({'n': '0'}, 'n', minimum=1)
        with self.assertRaises(inputs.BadInput):
            inputs.read_int({}, 'n', required=True)

    def test_text(self):
        self.assertEqual(inputs.read_text({'t': '  hi  '}, 't', max_length=5), 'hi')
        with self.assertRaises(inputs.BadInput):
            inputs.read_text({'t': 'x' * 6}, 't', max_length=5)
        with self.assertRaises(inputs.BadInput):
            inputs.read_text({'t': 'a\x00b'}, 't', max_length=5)
        self.assertEqual(inputs.read_text({'t': 'a\nb'}, 't', max_length=5), 'a\nb')

    def test_amounts_refuse_nan_and_infinity(self):
        self.assertEqual(str(inputs.read_decimal({'a': '10.5'}, 'a')), '10.50')
        for bad in ('NaN', 'Infinity', '-inf', 'ten'):
            with self.assertRaises(inputs.BadInput, msg=bad):
                inputs.read_decimal({'a': bad}, 'a')

    def test_choices_and_flags(self):
        self.assertEqual(inputs.read_choice({'c': 'a'}, 'c', ('a', 'b')), 'a')
        with self.assertRaises(inputs.BadInput):
            inputs.read_choice({'c': 'z'}, 'c', ('a', 'b'))
        self.assertTrue(inputs.read_bool({'f': 'yes'}, 'f'))
        self.assertFalse(inputs.read_bool({}, 'f'))
        with self.assertRaises(inputs.BadInput):
            inputs.read_bool({'f': 'maybe'}, 'f')

    def test_id_lists(self):
        self.assertEqual(inputs.read_ids({'i': '1,2,3'}, 'i'), [1, 2, 3])
        self.assertEqual(inputs.read_ids({'i': [4, '5']}, 'i'), [4, 5])
        with self.assertRaises(inputs.BadInput):
            inputs.read_ids({'i': ['x']}, 'i')

    def test_a_bad_field_answers_invalid_input_not_a_500(self):
        """The abandoned-checkout reminder took any id straight into the ORM."""
        response = inputs.exception_handler(inputs.BadInput('id', 'not a whole number'), {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'INVALID_INPUT')
        self.assertEqual(response.data['field'], 'id')


# ---------------------------------------------------------------------------
# R70: uploads read from their bytes, stored under a name nobody chose
# ---------------------------------------------------------------------------

class UploadGuardTests(TestCase):
    def test_a_real_picture_passes(self):
        upload = SimpleUploadedFile('me.png', png_bytes(), content_type='image/png')
        self.assertIsNone(uploads.image_refusal(upload))
        self.assertEqual(upload.tell(), 0)

    def test_a_script_called_png_is_refused(self):
        upload = SimpleUploadedFile('me.png', b'<script>alert(1)</script>', content_type='image/png')
        self.assertEqual(uploads.image_refusal(upload), 'NOT_AN_IMAGE')

    def test_a_png_header_on_something_else_is_refused(self):
        upload = SimpleUploadedFile('me.png', b'\x89PNG\r\n\x1a\n' + b'x' * 64, content_type='image/png')
        self.assertEqual(uploads.image_refusal(upload), 'NOT_AN_IMAGE')

    def test_too_large_is_refused(self):
        upload = SimpleUploadedFile('me.png', png_bytes(), content_type='image/png')
        self.assertEqual(uploads.image_refusal(upload, max_bytes=10), 'IMAGE_TOO_LARGE')

    def test_wider_kinds(self):
        pdf = SimpleUploadedFile('rules.pdf', b'%PDF-1.7\n...', content_type='application/pdf')
        self.assertIsNone(uploads.file_refusal(pdf, 1024, ('pdf', 'document')))
        exe = SimpleUploadedFile('rules.pdf', b'MZ\x90\x00' + b'\x00' * 60, content_type='application/pdf')
        self.assertEqual(uploads.file_refusal(exe, 1024, ('pdf', 'document')), 'UNSUPPORTED_FILE')
        html = SimpleUploadedFile('o.html', b'<!doctype html><html></html>', content_type='text/html')
        self.assertIsNone(uploads.file_refusal(html, 1024, ('html',)))
        csv = SimpleUploadedFile('r.csv', b'time,what\n10:00,Doors\n', content_type='text/csv')
        self.assertIsNone(uploads.file_refusal(csv, 1024, ('spreadsheet', 'text')))
        self.assertEqual(uploads.file_refusal(csv, 1024, ('html',)), 'UNSUPPORTED_FILE')

    def test_the_stored_name_is_random_and_keeps_only_a_clean_extension(self):
        name = uploads.OpaqueName('gallery/')(None, '../../my face.photo.PNG')
        self.assertTrue(name.startswith('gallery/'))
        self.assertTrue(name.endswith('.png'))
        self.assertNotIn('face', name)
        self.assertNotIn('..', name)
        self.assertNotEqual(name, uploads.OpaqueName('gallery/')(None, 'my face.png'))


# ---------------------------------------------------------------------------
# R79 and R81: coded password refusals, and a log with no secrets in it
# ---------------------------------------------------------------------------

class PasswordAndLogTests(TestCase):
    def test_a_short_password_answers_a_code_the_screen_translates(self):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        from vent_auth.errors import password_refused
        try:
            validate_password('ab1')
        except ValidationError as exc:
            response = password_refused(exc)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'PASSWORD_TOO_SHORT')
        self.assertIn('min_length', response.data['params'])

    def test_a_failed_sign_in_is_logged_without_what_was_typed(self):
        from vent_auth import security_log
        with self.assertLogs('vent.security', level='WARNING') as seen:
            security_log.refused('login_failed', user_id=7, failures=2)
        line = seen.output[0]
        self.assertIn('event=login_failed', line)
        self.assertIn('user=7', line)
        self.assertNotIn('password', line.lower())


# ---------------------------------------------------------------------------
# R77: a Content-Security-Policy on what the API answers
# ---------------------------------------------------------------------------

class ContentSecurityPolicyTests(TestCase):
    def test_json_may_load_nothing(self):
        res = APIClient().get('/auth/challenge/')
        self.assertIn("default-src 'none'", res['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", res['Content-Security-Policy'])

    def test_a_file_is_left_without_one_so_a_pdf_still_opens(self):
        from django.http import HttpResponse
        from vent_auth.middleware_security import ContentSecurityPolicyMiddleware
        pdf = ContentSecurityPolicyMiddleware(
            lambda r: HttpResponse(b'%PDF-1.4', content_type='application/pdf'))(None)
        self.assertFalse(pdf.has_header('Content-Security-Policy'))

    def test_a_response_with_its_own_policy_keeps_it(self):
        from django.http import HttpResponse
        from vent_auth.middleware_security import ContentSecurityPolicyMiddleware

        def overlay(_request):
            response = HttpResponse('<html></html>', content_type='text/html')
            response['Content-Security-Policy'] = 'frame-ancestors *'
            return response
        self.assertEqual(
            ContentSecurityPolicyMiddleware(overlay)(None)['Content-Security-Policy'],
            'frame-ancestors *')


class NotANumberTests(TestCase):
    """float() takes 'NaN' and 'inf'. NaN passes every `< 0` and `> 100` check
    and was stored as a commission; int(float('inf')) was a 500."""

    def test_finite_float_refuses_what_float_takes(self):
        for bad in ('NaN', 'nan', 'inf', '-Infinity', True, 'ten', None):
            with self.assertRaises(inputs.BadInput, msg=repr(bad)):
                inputs.finite_float(bad)
        self.assertEqual(inputs.finite_float('12.5'), 12.5)

    def test_read_float_too(self):
        with self.assertRaises(inputs.BadInput):
            inputs.read_float({'x': 'NaN'}, 'x')
        self.assertEqual(inputs.read_float({}, 'x', default=0), 0)

    def test_a_bad_input_is_still_a_value_error_for_the_older_doors(self):
        """A door that catches ValueError with its own sentence keeps it."""
        self.assertTrue(issubclass(inputs.BadInput, ValueError))
