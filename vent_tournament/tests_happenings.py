"""One list of what happened in a tournament, and the bracket on stream (P1e).

CEO, 16 October 2026: "i aprove the design". Section 14b: "One event list
feeds overlays, the run of show and studios: match called, result confirmed,
stage closed, qualifiers pulled, standings changed." and "Brackets on stream
draw from slots, placeholders included, so the overlay can show 'Lagos
Qualifier, 1st' or 'Winner of Upper semi 1' before anybody has played."
"""
import json

from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient

from . import bracket_edit, happenings, results, stage_engine
from .models import BracketMatch, Happening
from .tests import gen_bracket
from .tests_draw_early import advance, draw_early, groups_into_knockout
from .tests_stage_engine import add_stage, draw, field, play, play_out


def play_all(t):
    """Play every open match of a one format tournament, the first side winning."""
    for _ in range(50):
        open_ = list(t.bracket_matches.filter(status='scheduled', participant_1__isnull=False,
                                               participant_2__isnull=False))
        if not open_:
            return
        for m in open_:
            play(m, 2, 0)
    raise AssertionError('never finished')


def first_match(t):
    return t.bracket_matches.filter(round_number=1, participant_1__isnull=False,
                                    participant_2__isnull=False).order_by('match_number').first()


def results_of(t):
    return [r for r in happenings.rows(t) if r['kind'] == 'result_confirmed']


class ResultTests(TestCase):
    def test_names_are_the_ones_every_screen_shows(self):
        t, creator, regs = field(4)
        for i, r in enumerate(regs):
            r.user.full_name = 'Player Number %d' % i
            r.user.save(update_fields=['full_name'])
        gen_bracket(t, creator, 'registration')
        m = first_match(t)
        play(m, 2, 0)
        h = Happening.objects.get(tournament=t, kind='result_confirmed')
        self.assertEqual(h.data['home'], m.participant_1.user.full_name)
        self.assertEqual(h.data['winner'], m.participant_1.user.full_name)

    def test_result_recorded_once(self):
        t, creator, _regs = field(4)
        gen_bracket(t, creator, 'registration')
        m = first_match(t)
        play(m, 2, 1)
        rows = Happening.objects.filter(tournament=t, kind='result_confirmed')
        self.assertEqual(rows.count(), 1)
        h = rows.get()
        self.assertEqual(h.match_id, m.id)
        self.assertEqual(h.data['home'], m.participant_1.entrant_name)
        self.assertEqual(h.data['away'], m.participant_2.entrant_name)
        self.assertEqual(h.data['score'], '2-1')
        self.assertEqual(h.data['winner'], m.participant_1.entrant_name)
        self.assertEqual([r['standing'] for r in results_of(t)], [True])

    def test_bye_is_not_a_result(self):
        t, creator, _regs = field(3)
        gen_bracket(t, creator, 'registration')
        self.assertTrue(t.bracket_matches.filter(status='bye').exists())
        self.assertEqual(results_of(t), [])

    def test_reopened_result_no_longer_stands(self):
        t, creator, _regs = field(4)
        gen_bracket(t, creator, 'registration')
        m = first_match(t)
        play(m, 2, 1)
        with transaction.atomic():
            bracket_edit.reopen(BracketMatch.objects.get(pk=m.pk), creator)
        self.assertEqual([r['standing'] for r in results_of(t)], [False])
        play(m, 0, 2)
        self.assertEqual([(r['data']['score'], r['standing']) for r in results_of(t)],
                         [('0-2', True), ('2-1', False)])

    def test_undrawn_stage_results_stop_standing(self):
        t, creator, _regs = field(4)
        stage = add_stage(t, 0, 'single_elimination', label='Main')
        draw(stage, creator)
        play(stage.matches.filter(round_number=1).first(), 2, 0)
        with transaction.atomic():
            bracket_edit.undraw(stage, creator, discard_results=True)
        self.assertEqual([r['standing'] for r in results_of(t)], [False])

    def test_rolled_back_result_leaves_nothing(self):
        t, creator, _regs = field(4)
        gen_bracket(t, creator, 'registration')
        m = first_match(t)
        try:
            with transaction.atomic():
                locked = BracketMatch.objects.select_for_update().get(pk=m.pk)
                results.apply(locked, results.decide(locked, 2, 0))
                raise RuntimeError('refused after the write')
        except RuntimeError:
            pass
        self.assertFalse(Happening.objects.filter(tournament=t).exists())


class StageTests(TestCase):
    def test_stage_started_and_closed(self):
        t, creator, first, ko = groups_into_knockout()
        play_out(first)
        advance(first, creator)
        got = [(h.kind, h.stage_id) for h in Happening.objects.filter(tournament=t)
               .exclude(kind='result_confirmed').order_by('id')]
        self.assertEqual(got, [('stage_started', first.id), ('line_sent', first.id),
                               ('stage_closed', first.id), ('stage_started', ko.id)])

    def test_the_deciding_result_comes_before_the_close(self):
        # The last result of a stage closes it inside the match save; the
        # result must still be listed before "finished" and "started".
        t, creator, first, _ko = groups_into_knockout()
        play_out(first)
        advance(first, creator)
        kinds = list(Happening.objects.filter(tournament=t).order_by('id').values_list('kind', flat=True))
        last_result = max(i for i, k in enumerate(kinds) if k == 'result_confirmed')
        self.assertLess(last_result, kinds.index('stage_closed'))

    def test_an_on_close_line_lists_the_deciding_result_first(self):
        from .models import Progression
        t, creator, first, _ko = groups_into_knockout()
        Progression.objects.filter(source=first).update(fire='on_close')
        play_out(first)
        kinds = list(Happening.objects.filter(tournament=t).order_by('id').values_list('kind', flat=True))
        self.assertIn('stage_closed', kinds)
        last_result = max(i for i, k in enumerate(kinds) if k == 'result_confirmed')
        self.assertLess(last_result, kinds.index('stage_closed'))

    def test_line_sent_names_target_and_count(self):
        t, creator, first, _ko = groups_into_knockout()
        play_out(first)
        advance(first, creator)
        h = Happening.objects.get(tournament=t, kind='line_sent')
        self.assertEqual(h.stage_id, first.id)
        self.assertEqual((h.data['target'], h.data['count']), ('Playoff', 4))

    def won(self, t):
        rows = Happening.objects.filter(tournament=t, kind='tournament_won')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().data['champion'],
                         t.registrations.get(final_position=1).entrant_name)

    def test_won_one_format(self):
        t, creator, _regs = field(4)
        gen_bracket(t, creator, 'registration')
        play_all(t)
        self.won(t)

    def test_won_staged_chain(self):
        t, creator, first, ko = groups_into_knockout()
        play_out(first)
        advance(first, creator)
        play_out(ko)
        self.won(t)

    def test_won_league_closing_by_itself(self):
        t, creator, _regs = field(4, 'round_robin')
        stage = add_stage(t, 0, 'round_robin', label='League')
        draw(stage, creator)
        play_out(stage)
        self.won(t)
        closed = Happening.objects.filter(tournament=t, kind='stage_closed', stage=stage)
        self.assertEqual(closed.count(), 1)

    def test_won_closed_by_hand(self):
        # A battle royale has no final match, so the organiser closes it.
        from . import br_engine, stage_settings
        from .tests_battle_royale import enter
        t, creator, regs = field(3, 'battle_royale')
        stage = br_engine.ensure_stage(t)
        stage.settings = stage_settings.clean('battle_royale', {'maps': 1})
        stage.save(update_fields=['settings'])
        draw(stage, creator)
        a, b, c = regs
        enter(stage.br_lobbies.get().maps.get(), {c: (1, 0), a: (2, 0), b: (3, 0)}, creator)
        with transaction.atomic():
            stage_engine.finish_last_stage(stage, creator)
        self.won(t)
        closed = Happening.objects.filter(tournament=t, kind='stage_closed', stage=stage)
        self.assertEqual(closed.count(), 1)


class FeedTests(TestCase):
    def feed(self, t):
        res = APIClient().get('/tournament/%s/overlay-feed/' % t.slug)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['data']

    def test_feed_bracket_names_waiting_seats(self):
        t, creator, _first, ko = groups_into_knockout()
        draw_early(ko, creator)
        playoff = next(s for s in self.feed(t)['bracket'] if s['label'] == 'Playoff')
        self.assertEqual(playoff['format'], 'single_elimination')
        first_round = playoff['rounds'][0]['matches']
        self.assertEqual(len(first_round), 2)
        for m in first_round:
            self.assertIsNone(m['participant_1'])
            self.assertIsNone(m['participant_2'])
            self.assertEqual([s['kind'] for s in m['slots']], ['place', 'place'])
        self.assertEqual(first_round[0]['slots'][0]['from_place'],
                         {'stage': 'Groups', 'group': 1, 'place': 1, 'tournament': None})

    def test_live_rows_name_waiting_seats(self):
        t, creator, _regs = field(4)
        gen_bracket(t, creator, 'registration')
        final = next(r for r in self.feed(t)['live'] if r['round'] == 2)
        self.assertEqual((final['home'], final['away']), ('', ''))
        self.assertEqual((final['home_slot']['kind'], final['away_slot']['kind']), ('winner', 'winner'))
        self.assertEqual(final['home_slot']['from']['round'], 1)

    def test_feed_carries_happenings_newest_first(self):
        t, creator, _regs = field(4)
        gen_bracket(t, creator, 'registration')
        a, b = t.bracket_matches.filter(round_number=1).order_by('match_number')
        play(a, 2, 0)
        play(b, 1, 2)
        rows = [r for r in self.feed(t)['happenings'] if r['kind'] == 'result_confirmed']
        self.assertEqual([r['data']['score'] for r in rows], ['1-2', '2-0'])

    def test_version_moves_when_a_bracket_is_drawn(self):
        t, creator, _first, ko = groups_into_knockout()
        before = self.feed(t)['version']
        draw_early(ko, creator)
        self.assertNotEqual(self.feed(t)['version'], before)

    def test_feed_hides_who_recorded(self):
        t, creator, _regs = field(4)
        gen_bracket(t, creator, 'registration')
        m = first_match(t)
        with transaction.atomic():
            locked = BracketMatch.objects.select_for_update().get(pk=m.pk)
            results.apply(locked, results.decide(locked, 2, 0), recorded_by=creator)
        rows = self.feed(t)['happenings']
        self.assertTrue(rows)
        self.assertNotIn(creator.username, json.dumps(rows))


class StudioFeedTests(TestCase):
    """The studio's own feed (/studio/<token>/feed/) is what every studio
    element reads. It forwarded a hand-picked list of the overlay feed's keys,
    so `bracket` and `happenings` reached the overlay feed and never the
    studio (walk, 17 October 2026)."""

    def test_studio_feed_carries_everything_the_overlay_feed_does(self):
        from .models import BroadcastSession
        t, creator, _first, ko = groups_into_knockout()
        draw_early(ko, creator)
        session = BroadcastSession.objects.create(tournament=t, name='walk')
        overlay = APIClient().get('/tournament/%s/overlay-feed/' % t.slug).json()['data']
        studio = APIClient().get('/studio/%s/feed/' % session.token).json()['data']
        missing = sorted(set(overlay) - set(studio))
        self.assertEqual(missing, [])
        self.assertEqual(studio['bracket'], overlay['bracket'])
        self.assertEqual(studio['happenings'], overlay['happenings'])
