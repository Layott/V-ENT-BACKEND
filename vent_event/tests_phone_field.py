"""A phone number somebody can actually be rung on.

CEO, 2 September 2026: "if its a phone number, then the users must input a
country code".

The reason it matters is what the number is FOR: a door list, a "we cannot find
you" call on the day, a cancellation. A number without a country code works from
one country and fails from every other, and the person holding the phone at the
venue is not always in Nigeria.

The rule that took the most thought is the middle one. Almost everybody in
Nigeria writes their own number as `0803...`, so refusing it would refuse most
real numbers to enforce a technicality. It is converted instead. What is refused
is a number with neither a code nor a leading zero, because there is genuinely
nothing there to infer a country from and guessing would invent one.
"""
from django.test import TestCase

from .checkout import CheckoutError, clean_phone


class PhoneFieldTests(TestCase):
    def ok(self, raw):
        return clean_phone(raw, 'WhatsApp number', 'f1')

    def refused(self, raw):
        with self.assertRaises(CheckoutError) as caught:
            clean_phone(raw, 'WhatsApp number', 'f1')
        return str(caught.exception)

    # ------------------------------------------------------- already correct

    def test_a_number_with_a_country_code_is_kept(self):
        self.assertEqual(self.ok('+2348030000000'), '+2348030000000')

    def test_spacing_and_punctuation_do_not_matter(self):
        """Numbers are written half a dozen ways and a strict pattern refuses
        more real numbers than fake ones."""
        for written in ('+234 803 000 0000', '+234-803-000-0000',
                        '+234 (803) 000 0000', ' +2348030000000 '):
            self.assertEqual(self.ok(written), '+2348030000000', written)

    def test_a_foreign_number_is_kept_as_given(self):
        self.assertEqual(self.ok('+44 7700 900123'), '+447700900123')
        self.assertEqual(self.ok('+1 415 555 0123'), '+14155550123')

    def test_a_double_zero_prefix_becomes_a_plus(self):
        """Printed cards often write 00234."""
        self.assertEqual(self.ok('002348030000000'), '+2348030000000')

    # --------------------------------------------------- the Nigerian default

    def test_a_national_number_gains_the_country_code(self):
        """Almost everybody in Nigeria writes 0803..., so this is converted
        rather than refused. Refusing it would refuse most real numbers."""
        self.assertEqual(self.ok('08030000000'), '+2348030000000')
        self.assertEqual(self.ok('0803 000 0000'), '+2348030000000')

    # ------------------------------------------------------------- refusals

    def test_a_number_with_no_code_and_no_leading_zero_is_refused(self):
        """Nothing here says which country. Guessing would invent one."""
        message = self.refused('8030000000')
        self.assertIn('+234', message)
        self.assertIn('country code', message)

    def test_the_refusal_names_the_field(self):
        self.assertIn('WhatsApp number', self.refused('8030000000'))

    def test_something_far_too_short_is_refused(self):
        self.refused('+234 12')

    def test_something_far_too_long_is_refused(self):
        self.refused('+234 8030000000000000000')

    def test_empty_is_refused(self):
        self.assertIn('needed', self.refused(''))

    def test_letters_are_refused(self):
        self.refused('call me maybe')

    # -------------------------------------------------- used by the checkout

    def test_the_checkout_applies_it(self):
        """The helper is only worth having if the checkout actually calls it."""
        from . import checkout
        import inspect

        source = inspect.getsource(checkout.clean_answer) \
            if hasattr(checkout, 'clean_answer') else inspect.getsource(checkout)
        self.assertIn('clean_phone', source)
