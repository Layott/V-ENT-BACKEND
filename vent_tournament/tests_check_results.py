"""`manage.py check_results` finds a result that cannot be true (inbox 488).

CEO, 16 October 2026: "One local test tournament has a winner who isn't one of
the two players, left over from an earlier bracket-editing walk. - fix". The
same fault reached the data through two doors: the admin score override took
any registration id as the winner (27 August, a winner from another
tournament), and a round one result changed after the semi-final was played
(28 September). Both doors are guarded now; this reads the data itself, so a
third door would be found on the next deploy rather than on a screen.
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import BracketMatch
from .tests import gen_bracket
from .tests_stage_engine import field, play


def run():
    out = StringIO()
    call_command('check_results', stdout=out)
    return out.getvalue()


class CheckResultsTests(TestCase):
    def setUp(self):
        self.t, creator, self.regs = field(4, 'single_elimination')
        gen_bracket(self.t, creator, 'registration')
        self.first = BracketMatch.objects.filter(tournament=self.t, round_number=1).first()
        play(self.first, 2, 0)
        self.first.refresh_from_db()

    def test_clean_data_reads_zero(self):
        self.assertIn('0 problems', run())

    def test_a_winner_who_did_not_play_is_found(self):
        stranger = [r for r in self.regs if r.id not in (
            self.first.participant_1_id, self.first.participant_2_id)][0]
        BracketMatch.objects.filter(pk=self.first.pk).update(winner_id=stranger.id)
        with self.assertRaises(CommandError) as caught:
            run()
        self.assertIn('match %s' % self.first.pk, str(caught.exception))
        self.assertIn('winner is not one of the two players', str(caught.exception))

    def test_a_registration_from_another_tournament_is_found(self):
        other, _creator, other_regs = field(2, 'single_elimination')
        BracketMatch.objects.filter(pk=self.first.pk).update(
            participant_1_id=other_regs[0].id, winner_id=other_regs[0].id)
        with self.assertRaises(CommandError) as caught:
            run()
        self.assertIn('another tournament', str(caught.exception))
