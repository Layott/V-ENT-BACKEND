"""Every bracket drawn before P1a gets its slots, the same as a fresh draw would write.

The design's background conversion (section 6 and 17): existing tournaments
are converted with nothing for organisers to do, and the conversion is
compared against what the new code writes for the same bracket.
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from . import match_slots
from .models import MatchSlot
from .tests_golden_draws import drawn, drawn_stage


def written(t):
    return {(s.match_id, s.slot): (s.kind, s.source_match_id, s.seed)
            for s in MatchSlot.objects.filter(match__tournament=t)}


class BackfillTests(TestCase):
    def test_backfill_matches_a_fresh_draw(self):
        for t in (drawn('de', 'double_elimination', {}, 11),
                  drawn('se_third', 'single_elimination', {'third_place_match': True}, 8),
                  drawn('gsl', 'gsl', {}, 8),
                  drawn_stage('double_elimination', {'grand_final': 'reset'}, 4)):
            fresh = written(t)
            MatchSlot.objects.filter(match__tournament=t).delete()
            count = match_slots.backfill(t.bracket_matches.all())
            self.assertEqual(count, len(fresh))
            self.assertEqual(written(t), fresh)
            self.assertEqual(match_slots.disagreements(t.bracket_matches.all()), [])

    def test_backfill_twice_writes_nothing(self):
        t = drawn('se', 'single_elimination', {}, 6)
        MatchSlot.objects.filter(match__tournament=t).delete()
        match_slots.backfill(t.bracket_matches.all())
        self.assertEqual(match_slots.backfill(t.bracket_matches.all()), 0)


class CheckResultsReadsSlotsTests(TestCase):
    def test_check_results_names_a_match_without_slots(self):
        t = drawn('se', 'single_elimination', {}, 4)
        one = MatchSlot.objects.filter(match__tournament=t).first()
        MatchSlot.objects.filter(pk=one.pk).delete()
        with self.assertRaises(CommandError) as caught:
            call_command('check_results', stdout=StringIO())
        self.assertIn('slots: 1, expected 2', str(caught.exception))
        match_slots.backfill(t.bracket_matches.all())
        out = StringIO()
        call_command('check_results', stdout=out)
        self.assertIn('0 problems', out.getvalue())
