"""Results stay editable after a tournament has ended (inbox 521).

CEO, 17 October 2026, with a screenshot of the match room refusing "This
tournament is finished, so its bracket cannot change.": "even after a
tournament has ended, organizers, should still be able to edit or change
results".

What stays refused, and why:
- prizes already paid: money has moved on the old places (PRIZES_PAID);
- a stage that has sent its qualifiers into another tournament (STAGE_CLOSED);
- a cancelled tournament (TOURNAMENT_FINISHED).
"""
from django.db import transaction
from django.test import TestCase

from . import bracket_edit, results
from .models import PrizePayout
from .tests import gen_bracket
from .tests_p3_schema import preset_stage
from .tests_stage_engine import field, play, play_out


def final_of(tournament):
    return tournament.bracket_matches.filter(is_final=True).order_by('-round_number').first()


def take_off(match, user):
    with transaction.atomic():
        return bracket_edit.reopen(match, user)


class StagedTests(TestCase):
    def setUp(self):
        self.stage, self.regs = preset_stage(4, 'shaughnessy')
        self.t = self.stage.tournament
        play_out(self.stage)
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, 'completed')

    def test_taking_the_final_off_reopens_the_tournament_and_its_stage(self):
        final = final_of(self.t)
        take_off(final, self.t.tournament_creator)
        self.t.refresh_from_db()
        self.stage.refresh_from_db()
        self.assertEqual((self.t.status, self.t.completed_at), ('live', None))
        self.assertEqual((self.stage.status, self.stage.completed_at), ('running', None))
        self.assertFalse(self.t.registrations.filter(final_position__isnull=False).exists())

    def test_the_right_result_entered_again_finishes_it_with_the_new_winner(self):
        final = final_of(self.t)
        old = final.winner_id
        take_off(final, self.t.tournament_creator)
        final.refresh_from_db()
        if final.participant_1_id == old:
            play(final, 0, 2)
        else:
            play(final, 2, 0)
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, 'completed')
        champion = self.t.registrations.get(final_position=1)
        self.assertNotEqual(champion.id, old)

    def test_paid_prizes_keep_it_closed(self):
        PrizePayout.objects.create(tournament=self.t, winner_registration=self.regs[0],
                                   position=1, amount=10)
        with self.assertRaises(bracket_edit.EditError) as caught:
            take_off(final_of(self.t), self.t.tournament_creator)
        self.assertEqual(caught.exception.code, 'PRIZES_PAID')
        final = final_of(self.t)
        loser_side = 2 if final.winner_id == final.participant_1_id else 1
        with self.assertRaises(results.ResultError) as caught:
            results.decide(final, *((0, 2) if loser_side == 2 else (2, 0)))
        self.assertEqual(caught.exception.code, 'PRIZES_PAID')

    def test_a_cancelled_tournament_stays_closed(self):
        self.t.status = 'cancelled'
        self.t.save(update_fields=['status'])
        with self.assertRaises(bracket_edit.EditError) as caught:
            take_off(final_of(self.t), self.t.tournament_creator)
        self.assertEqual(caught.exception.code, 'TOURNAMENT_FINISHED')


class OneFormatTests(TestCase):
    def setUp(self):
        self.t, self.creator, self.regs = field(4)
        gen_bracket(self.t, self.creator, strategy='registration')
        for m in self.t.bracket_matches.order_by('round_number', 'match_number'):
            m.refresh_from_db()
            if m.participant_1_id and m.participant_2_id and m.status == 'scheduled':
                play(m, 2, 0)
        for m in self.t.bracket_matches.order_by('round_number', 'match_number'):
            m.refresh_from_db()
            if m.participant_1_id and m.participant_2_id and m.status == 'scheduled':
                play(m, 2, 0)
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, 'completed')

    def test_correcting_the_final_directly_moves_the_places_with_it(self):
        final = final_of(self.t)
        old = final.winner_id
        play(final, 0, 2)
        champion = self.t.registrations.get(final_position=1)
        runner_up = self.t.registrations.get(final_position=2)
        self.assertNotEqual(champion.id, old)
        self.assertEqual(runner_up.id, old)

    def test_taking_the_final_off_reopens_a_one_format_tournament(self):
        take_off(final_of(self.t), self.creator)
        self.t.refresh_from_db()
        self.assertEqual((self.t.status, self.t.completed_at), ('live', None))


class SentOnTests(TestCase):
    def test_a_qualifier_that_sent_its_places_on_stays_closed(self):
        from .tests_feeds import qualifier_and_finals
        from .tests_stage_engine import draw
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals(fire='on_close')
        draw(q_stage, creator)
        play_out(q_stage)
        q_stage.refresh_from_db()
        line.refresh_from_db()
        self.assertIsNotNone(line.fired_at)
        last = q_stage.matches.filter(status='completed').order_by('-round_number', '-match_number').first()
        with self.assertRaises(bracket_edit.EditError) as caught:
            take_off(last, creator)
        self.assertEqual(caught.exception.code, 'STAGE_CLOSED')
