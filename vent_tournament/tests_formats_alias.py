"""Every value the create wizard can save resolves to a format.

`swiss-system` did not. The wizard has saved that string since it was written,
`formats.get` resolved it to None, and a Swiss tournament therefore had no
format at all: no explanation in the wizard, no defaults, no entry in the
catalogue. Nothing raised, which is exactly why it survived.

This test is the wizard's list of values, checked against the resolver. It fails
the day somebody adds a ninth format to the screen without adding it here.
"""
from django.test import SimpleTestCase

from . import formats

# The `value` of every option in
# src/components/create-tournament-component/format-participants/tournament-format/TournamentFormat.js
WIZARD_VALUES = [
    'single-elimination',
    'double-elimination',
    'round-robin',
    'swiss-system',
    'battle-royale',
    'gsl',
    'aggregate_2v2',
    'ladder',
]


class FormatAliasTests(SimpleTestCase):
    def test_every_wizard_value_resolves(self):
        for value in WIZARD_VALUES:
            self.assertIsNotNone(formats.get(value), value)

    def test_the_wizard_covers_every_format_the_backend_runs(self):
        # The other half of the same question: a format the platform supports
        # and the wizard does not offer cannot be built by anybody.
        resolved = {formats.get(v).key for v in WIZARD_VALUES}
        self.assertEqual(resolved, set(formats.FORMATS))

    def test_swiss_system_is_swiss(self):
        self.assertEqual(formats.get('swiss-system').key, 'swiss')

    def test_the_spellings_a_row_may_already_hold_resolve(self):
        for value in ('Single Elimination', 'single_elimination', 'SINGLE-ELIMINATION'):
            self.assertEqual(formats.get(value).key, 'single_elimination')

    def test_nonsense_still_resolves_to_nothing(self):
        # Tolerant, not credulous.
        self.assertIsNone(formats.get('best of the best'))
        self.assertIsNone(formats.get(''))
        self.assertIsNone(formats.get(None))

    def test_every_format_carries_an_explanation(self):
        # The wizard shows `notes` for whatever is picked. A format without one
        # renders an empty box.
        for key, definition in formats.FORMATS.items():
            self.assertTrue(definition.notes.strip(), key)
