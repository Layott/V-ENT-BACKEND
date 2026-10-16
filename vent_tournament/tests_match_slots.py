"""Every match has two slots saying where each side comes from (P1a).

CEO, 16 October 2026: "i aprove the design". Section 8 of the bracket
structure design: every slot knows where it came from, so a whole bracket can
be drawn and shown with "Winner of Round 1, match 2" before anyone has played.
One writer (`match_slots.py`) puts the pointer and the slot down together, so
the two can never disagree.
"""
from django.db import transaction
from django.test import TestCase

from . import bracket_edit, match_slots, stage_engine
from .models import BracketMatch, MatchSlot
from .services import bracket as bracket_service
from .tests_golden_draws import STAGE_ONLY, STAGED, VARIANTS, drawn, drawn_stage, drawn_staged
from .tests_stage_engine import add_stage, field, play


def slots_of(m):
    return {s.slot: s for s in MatchSlot.objects.filter(match=m)}


def ordered(t, side, rnd):
    return list(t.bracket_matches.filter(bracket_side=side, round_number=rnd).order_by('match_number'))


class EveryDrawTests(TestCase):
    def test_every_variant_has_two_slots_in_step(self):
        for name, bracket_type, options, sizes in VARIANTS:
            for n in sizes:
                with self.subTest(variant=name, size=n):
                    t = drawn(name, bracket_type, options, n)
                    matches = t.bracket_matches.all()
                    self.assertEqual(match_slots.disagreements(matches), [])
                    self.assertEqual(MatchSlot.objects.filter(match__tournament=t).count(),
                                     2 * matches.count())
        for n, g in STAGED:
            t = drawn_staged(n, g)
            self.assertEqual(match_slots.disagreements(t.bracket_matches.all()), [])
        for name, fmt, settings, sizes in STAGE_ONLY:
            for n in sizes:
                with self.subTest(variant=name, size=n):
                    t = drawn_stage(fmt, settings, n)
                    self.assertEqual(match_slots.disagreements(t.bracket_matches.all()), [])


class KnockoutSlotTests(TestCase):
    def test_round_one_seats_are_seeds_and_byes(self):
        t = drawn('se', 'single_elimination', {}, 5)
        first = ordered(t, 'winners', 1)
        kinds = [s.kind for m in first for s in slots_of(m).values()]
        self.assertEqual(sorted(kinds), ['bye'] * 3 + ['entrant'] * 5)
        seeds = sorted(s.seed for m in first for s in slots_of(m).values() if s.kind == 'entrant')
        self.assertEqual(seeds, [1, 2, 3, 4, 5])
        self.assertFalse(MatchSlot.objects.filter(match__tournament=t, kind='open').exists())

    def test_round_two_reads_winner_of(self):
        t = drawn('se', 'single_elimination', {}, 5)
        first = {m.id for m in ordered(t, 'winners', 1)}
        for m in ordered(t, 'winners', 2):
            for s in slots_of(m).values():
                self.assertEqual(s.kind, 'winner')
                self.assertIn(s.source_match_id, first)

    def test_double_elimination_sources(self):
        t = drawn('de', 'double_elimination', {}, 8)
        upper_one = ordered(t, 'winners', 1)
        lower_one = ordered(t, 'losers', 1)
        for m, source in enumerate(bracket_service.reorder('reverse', upper_one)):
            slot = slots_of(lower_one[m // 2])[1 if m % 2 == 0 else 2]
            self.assertEqual((slot.kind, slot.source_match_id), ('loser', source.id))
        final = t.bracket_matches.get(bracket_side='grand_final')
        upper_final = ordered(t, 'winners', 3)[0]
        lower_final = t.bracket_matches.filter(bracket_side='losers').order_by('-round_number').first()
        got = slots_of(final)
        self.assertEqual((got[1].kind, got[1].source_match_id), ('winner', upper_final.id))
        self.assertEqual((got[2].kind, got[2].source_match_id), ('winner', lower_final.id))

    def test_reset_slots(self):
        t = drawn_stage('double_elimination', {'grand_final': 'reset'}, 4)
        first, second = t.bracket_matches.filter(bracket_side='grand_final').order_by('round_number')
        got = slots_of(second)
        self.assertEqual([(got[s].kind, got[s].source_match_id) for s in (1, 2)],
                         [('reset', first.id), ('reset', first.id)])
        self.assertEqual(match_slots.disagreements(t.bracket_matches.all()), [])


class LaterMatchTests(TestCase):
    def test_matches_made_later_get_slots(self):
        t, creator, _regs = field(8, 'swiss')
        stage = add_stage(t, 0, 'swiss')
        with transaction.atomic():
            stage_engine.draw(stage, creator)
        for _ in range(10):
            ready = list(stage.matches.filter(status='scheduled', participant_1__isnull=False,
                                              participant_2__isnull=False))
            if not ready:
                break
            for m in ready:
                play(m, 2, 0)
        self.assertGreater(stage.matches.values('round_number').distinct().count(), 1)
        self.assertEqual(match_slots.disagreements(stage.matches.all()), [])

        t2 = drawn('wso', 'winner_stays_on', {}, 5)
        for _ in range(10):
            ready = list(t2.bracket_matches.filter(status='scheduled'))
            if not ready:
                break
            play(ready[0], 2, 0)
        self.assertGreater(t2.bracket_matches.count(), 1)
        self.assertEqual(match_slots.disagreements(t2.bracket_matches.all()), [])

    def test_undraw_and_redraw_leave_no_orphans(self):
        t, creator, _regs = field(8, 'single_elimination')
        stage = add_stage(t, 0, 'single_elimination')
        with transaction.atomic():
            stage_engine.draw(stage, creator)
        with transaction.atomic():
            bracket_edit.undraw(stage, creator)
        stage.refresh_from_db()
        self.assertFalse(MatchSlot.objects.filter(match__tournament=t).exists())
        with transaction.atomic():
            stage_engine.draw(stage, creator)
        self.assertEqual(MatchSlot.objects.filter(match__stage=stage).count(),
                         2 * stage.matches.count())
        m = stage.matches.filter(round_number=1).first()
        play(m, 2, 0)
        with transaction.atomic():
            bracket_edit.reopen(BracketMatch.objects.get(pk=m.pk), creator)
        self.assertEqual(len(slots_of(m)), 2)
        self.assertEqual(match_slots.disagreements(stage.matches.all()), [])


class PayloadTests(TestCase):
    def test_bracket_endpoint_sends_slots(self):
        t = drawn('se', 'single_elimination', {}, 8)
        res = self.client.get('/tournament/get-tournament-brackets/%s/' % (t.slug or t.tournament_id))
        self.assertEqual(res.status_code, 200)
        rounds = res.json()['data']['rounds']
        for rnd in rounds:
            for m in rnd['matches']:
                self.assertEqual([s['slot'] for s in m['slots']], [1, 2])
        second = next(r for r in rounds if r['round'] == 2)
        slot = second['matches'][0]['slots'][0]
        self.assertEqual((slot['kind'], slot['from']['round'], slot['from']['side']),
                         ('winner', 1, 'winners'))
        first = next(r for r in rounds if r['round'] == 1)
        self.assertEqual(first['matches'][0]['slots'][0]['kind'], 'entrant')
        self.assertIsNone(first['matches'][0]['slots'][0]['from'])

    def test_slots_query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from . import match_shape
        counts = {}
        for n in (8, 32):
            t = drawn('de', 'double_elimination', {}, n)
            matches = list(t.bracket_matches.all())
            with CaptureQueriesContext(connection) as ctx:
                match_shape.attach_slots(matches)
                for m in matches:
                    match_shape.slot_rows(m)
            counts[n] = len(ctx)
        self.assertEqual(counts[8], counts[32])

    def test_unconverted_match_sends_empty_slots(self):
        from . import match_shape
        t = drawn('se', 'single_elimination', {}, 4)
        m = t.bracket_matches.first()
        MatchSlot.objects.filter(match=m).delete()
        self.assertEqual(match_shape.match_row(BracketMatch.objects.get(pk=m.pk))['slots'], [])
