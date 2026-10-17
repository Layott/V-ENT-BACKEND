"""A knockout can be drawn before the stages that feed it finish (P1d).

CEO, 16 October 2026: "i aprove the design". Section 4: "Every bracket can be
seen whole from the day it is drawn, with named placeholders for places not
yet decided." A seat waits for a place ("Groups: group A, place 1") and fills
the moment the link it waits on sends its people.
"""
from django.db import transaction
from django.test import TestCase

from . import match_shape, stage_engine
from .models import MatchSlot
from .tests_stage_engine import add_stage, draw, field, play_out


def groups_into_knockout(n=8, groups=2, advances=2, target='single_elimination', placement='cross'):
    t, creator, regs = field(n, 'round_robin')
    first = add_stage(t, 0, 'round_robin', advances=advances, groups=groups, label='Groups')
    ko = add_stage(t, 1, target, placement=placement, label='Playoff')
    draw(first, creator)
    return t, creator, first, ko


def draw_early(stage, user):
    with transaction.atomic():
        return stage_engine.draw(stage, user, early=True)


def seats(match):
    return [(s.kind, s.source_group, s.source_place) for s in MatchSlot.objects.filter(match=match)]


class PlaceSeatTests(TestCase):
    def test_playoff_drawn_before_the_groups_finish(self):
        t, creator, first, ko = groups_into_knockout()
        draw_early(ko, creator)
        ko.refresh_from_db()
        self.assertIsNotNone(ko.drawn_at)
        self.assertEqual(ko.matches.count(), 3)
        one, two = ko.matches.filter(round_number=1).order_by('match_number')
        self.assertEqual(seats(one), [('place', 1, 1), ('place', 2, 2)])
        self.assertEqual(seats(two), [('place', 2, 1), ('place', 1, 2)])
        self.assertFalse(ko.matches.filter(participant_1__isnull=False).exists())

    def test_early_needs_cross(self):
        _t, creator, _first, ko = groups_into_knockout(placement='by_record')
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            draw_early(ko, creator)
        self.assertEqual(caught.exception.code, 'STAGE_EARLY_NEEDS_CROSS')

    def test_early_not_for_round_robin(self):
        _t, creator, _first, ko = groups_into_knockout(target='round_robin')
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            draw_early(ko, creator)
        self.assertEqual(caught.exception.code, 'STAGE_EARLY_NOT_FOR_FORMAT')

    def test_payload_names_the_place(self):
        _t, creator, first, ko = groups_into_knockout()
        draw_early(ko, creator)
        m = ko.matches.filter(round_number=1).order_by('match_number').first()
        slot = match_shape.match_row(m)['slots'][0]
        self.assertEqual(slot['kind'], 'place')
        self.assertEqual(slot['from_place'], {'stage': 'Groups', 'group': 1, 'place': 1, 'tournament': None})


def advance(stage, user, **kw):
    with transaction.atomic():
        return stage_engine.advance(stage, user, **kw)


class FillTests(TestCase):
    def test_seats_fill_when_the_groups_close(self):
        t, creator, first, ko = groups_into_knockout()
        draw_early(ko, creator)
        play_out(first)
        advance(first, creator)
        rows = {(r['group'], r['rank']): r['registration_id'] for r in stage_engine.standings(first)}
        one, two = ko.matches.filter(round_number=1).order_by('match_number')
        self.assertEqual((one.participant_1_id, one.participant_2_id), (rows[(1, 1)], rows[(2, 2)]))
        self.assertEqual((two.participant_1_id, two.participant_2_id), (rows[(2, 1)], rows[(1, 2)]))
        ko.refresh_from_db()
        self.assertEqual(ko.status, 'running')

    def test_merge_fills_pool_by_pool(self):
        t, creator, regs = field(8, 'round_robin')
        ids = [r.id for r in regs]
        a = add_stage(t, 0, 'round_robin', direct=ids[:4], label='Pool A')
        b = add_stage(t, 1, 'round_robin', direct=ids[4:], label='Pool B')
        ko = add_stage(t, 2, 'single_elimination', label='Playoff')
        from .models import Progression
        Progression.objects.filter(source__tournament=t).delete()
        Progression.objects.create(source=a, target=ko, place_to=2, order=0)
        Progression.objects.create(source=b, target=ko, place_to=2, order=1)
        draw(a, creator)
        draw(b, creator)
        draw_early(ko, creator)
        play_out(a)
        advance(a, creator)
        seated = {p for m in ko.matches.all() for p in (m.participant_1_id, m.participant_2_id) if p}
        self.assertEqual(seated, set(ids[:4]) & seated)
        self.assertEqual(len(seated), 2)
        play_out(b)
        advance(b, creator)
        seated = {p for m in ko.matches.filter(round_number=1) for p in (m.participant_1_id, m.participant_2_id) if p}
        self.assertEqual(len(seated), 4)

    def test_short_line_leaves_a_walkover(self):
        # 7 entrants in 2 groups: group B has three, so "place 4" of B never comes.
        t, creator, first, ko = groups_into_knockout(n=7, advances=3)
        from .models import Progression
        line = Progression.objects.get(source=first)
        line.place_to = 3
        line.save(update_fields=['place_to'])
        first.advances = 3
        first.save(update_fields=['advances'])
        draw_early(ko, creator)
        play_out(first)
        advance(first, creator)
        self.assertFalse(ko.matches.filter(round_number=1, status='scheduled',
                                           participant_1__isnull=True).exists())
        play_out(ko)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')

    def test_unadvance_refused_while_the_early_draw_stands(self):
        from . import bracket_edit
        t, creator, first, ko = groups_into_knockout()
        draw_early(ko, creator)
        play_out(first)
        advance(first, creator)
        first.refresh_from_db()
        with self.assertRaises(bracket_edit.EditError) as caught:
            with transaction.atomic():
                bracket_edit.unadvance(first, creator)
        self.assertEqual(caught.exception.code, 'NEXT_STAGE_DRAWN')
        ko.refresh_from_db()
        with transaction.atomic():
            bracket_edit.undraw(ko, creator)
        with transaction.atomic():
            bracket_edit.unadvance(first, creator)

    def test_lower_side_tokens(self):
        t, creator, regs = field(8, 'round_robin')
        first = add_stage(t, 0, 'round_robin', advances=3, groups=2, label='Groups')
        de = add_stage(t, 1, 'double_elimination', label='Playoff')
        from .models import Progression
        Progression.objects.filter(source__tournament=t).delete()
        Progression.objects.create(source=first, target=de, place_to=2, side='winners', order=0)
        Progression.objects.create(source=first, target=de, place_from=3, place_to=3,
                                   side='losers', order=1)
        draw(first, creator)
        draw_early(de, creator)
        lower = MatchSlot.objects.filter(match__stage=de, kind='place',
                                         match__bracket_side='losers')
        self.assertEqual(lower.count(), 2)
        play_out(first)
        advance(first, creator)
        entry = de.matches.filter(bracket_side='losers', round_number=1)
        self.assertEqual(sum(1 for m in entry if m.participant_2_id), 2)
        play_out(de)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')


class EndpointTests(TestCase):
    def test_draw_early_through_the_endpoint(self):
        from .tests import client_for
        t, creator, first, ko = groups_into_knockout()
        res = client_for(creator).post('/tournament/%s/stages/%s/draw/' % (t.slug, ko.id),
                                       {'early': True}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        ko.refresh_from_db()
        self.assertIsNotNone(ko.drawn_at)
        self.assertTrue(MatchSlot.objects.filter(match__stage=ko, kind='place').exists())


class ManualLineTests(TestCase):
    """A line that waits for the organiser's press (walk, 17 October 2026):
    closing its stage sends nobody, so a knockout drawn early stays waiting,
    and pressing send fills its seats and starts it."""

    def test_closing_with_a_manual_line_leaves_the_early_draw_waiting(self):
        from .models import Progression
        t, creator, first, ko = groups_into_knockout()
        Progression.objects.filter(source=first).update(fire='manual')
        draw_early(ko, creator)
        play_out(first)
        advance(first, creator)
        first.refresh_from_db()
        ko.refresh_from_db()
        self.assertEqual(first.status, 'complete')
        self.assertEqual(ko.status, 'pending')
        self.assertFalse(ko.matches.filter(participant_1__isnull=False).exists())

    def test_pressing_send_fills_the_seats_and_starts_it(self):
        from . import progressions
        from .models import Progression
        t, creator, first, ko = groups_into_knockout()
        Progression.objects.filter(source=first).update(fire='manual')
        draw_early(ko, creator)
        play_out(first)
        advance(first, creator)
        line = Progression.objects.get(source=first)
        with transaction.atomic():
            progressions.pull(line, creator)
        ko.refresh_from_db()
        self.assertEqual(ko.status, 'running')
        self.assertEqual(ko.matches.filter(round_number=1, participant_1__isnull=False,
                                           participant_2__isnull=False).count(), 2)
