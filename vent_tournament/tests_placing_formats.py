"""Matches for every place, stepladder, page playoff and winner stays on.

CEO, 28 September 2026: "Finish up the matches for every place and build the
stepladder also, then same for page playoff and winner stays on."

Every test plays the format out through the one result rule every door uses
(`results.decide` then `results.apply`) and checks the places it ends with.
"""
from django.db import transaction
from django.test import TestCase

from . import stage_engine
from .models import BracketMatch, TournamentRegistration
from .services import bracket as bracket_service
from .tests import gen_bracket
from .tests_stage_engine import add_stage, draw, field, play


def open_matches(qs):
    return list(qs.filter(status='scheduled', participant_1__isnull=False,
                          participant_2__isnull=False)
                .select_related('participant_1', 'participant_2')
                .order_by('round_number', 'match_number'))


def play_all(qs, better_seed_wins=True):
    guard = 0
    while True:
        guard += 1
        assert guard < 300, 'never finished'
        todo = open_matches(qs)
        if not todo:
            return
        for m in todo:
            a, b = m.participant_1.seed, m.participant_2.seed
            if (a < b) == better_seed_wins:
                play(m, 2, 0)
            else:
                play(m, 0, 2)


def positions(t):
    return {r.seed: r.final_position for r in TournamentRegistration.objects.filter(tournament=t)}


class EveryPlaceTests(TestCase):
    def one_format(self, n):
        t, creator, regs = field(n)
        t.options = {'every_place': True}
        t.save(update_fields=['options'])
        gen_bracket(t, creator, strategy='registration')
        return t

    def test_eight_play_twelve_matches_and_every_place_is_settled(self):
        t = self.one_format(8)
        self.assertEqual(t.bracket_matches.count(), 12)
        # 7 on the title path (4, 2, 1); 5 below it (5 to 8 twice, then 3rd,
        # 5th and 7th).
        self.assertEqual(t.bracket_matches.filter(bracket_side='placement').count(), 5)
        play_all(t.bracket_matches)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        self.assertEqual(positions(t), {i: i for i in range(1, 9)})

    def test_upsets_still_give_every_entrant_a_distinct_place(self):
        t = self.one_format(8)
        play_all(t.bracket_matches, better_seed_wins=False)
        placed = sorted(p for p in positions(t).values())
        self.assertEqual(placed, list(range(1, 9)))
        # The worst seed wins everything when the worse seed always wins.
        self.assertEqual(positions(t)[8], 1)

    def test_an_odd_field_with_byes_places_everybody_who_played(self):
        for n in (5, 6, 7):
            t = self.one_format(n)
            play_all(t.bracket_matches)
            t.refresh_from_db()
            self.assertEqual(t.status, 'completed', n)
            self.assertEqual(sorted(positions(t).values()), list(range(1, n + 1)), n)

    def test_a_stage_with_every_place_ranks_by_the_matches(self):
        t, creator, regs = field(4)
        stage = add_stage(t, 0, 'single_elimination', settings={'every_place': True})
        draw(stage, creator)
        self.assertFalse(stage.matches.filter(winner_place=3).count() == 0)
        play_all(stage.matches)
        rows = stage_engine.standings(stage)
        self.assertEqual([r['seed'] for r in rows], [1, 2, 3, 4])

    def test_the_public_bracket_says_which_places_each_match_is_for(self):
        from rest_framework.test import APIClient
        t = self.one_format(8)
        data = APIClient().get('/tournament/get-tournament-brackets/%s/' % t.tournament_id).json()['data']
        places = {(r['bracket_side'], r['round']): sorted({tuple(m['places']) for m in r['matches']})
                  for r in data['rounds']}
        self.assertEqual(places[('winners', 1)], [(1, 8)])
        self.assertEqual(places[('placement', 2)], [(5, 8)])
        self.assertEqual(places[('placement', 3)], [(3, 4), (5, 6), (7, 8)])

    def test_every_place_replaces_the_third_place_match(self):
        from . import stage_settings
        cleaned = stage_settings.clean('single_elimination',
                                       {'every_place': True, 'third_place': True})
        self.assertFalse(cleaned['third_place'])


class StepladderTests(TestCase):
    def test_the_lowest_seeds_play_first_and_the_top_seed_waits(self):
        t, creator, regs = field(5, 'stepladder')
        gen_bracket(t, creator, strategy='registration')
        rungs = list(t.bracket_matches.order_by('round_number'))
        self.assertEqual(len(rungs), 4)
        first = rungs[0]
        self.assertEqual({first.participant_1.seed, first.participant_2.seed}, {4, 5})
        self.assertEqual(rungs[-1].participant_1.seed, 1)
        self.assertTrue(rungs[-1].is_final)
        self.assertEqual([m.loser_place for m in rungs], [5, 4, 3, 2])

    def test_the_match_room_knows_its_format(self):
        """Walk, 28 September 2026: the room called the stepladder final
        "Round 4, match 1"; it is told the format so it can say "Final"."""
        from .tests import client_for
        t, creator, regs = field(5, 'stepladder')
        gen_bracket(t, creator, strategy='registration')
        final = t.bracket_matches.order_by('-round_number').first()
        body = client_for(creator).get('/tournament/match/%s/' % final.id).json()['data']
        self.assertEqual((body['format'], body['is_final']), ('stepladder', True))

    def test_the_underdog_climbs_every_rung(self):
        t, creator, regs = field(5, 'stepladder')
        gen_bracket(t, creator, strategy='registration')
        play_all(t.bracket_matches, better_seed_wins=False)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        self.assertEqual(positions(t), {5: 1, 1: 2, 2: 3, 3: 4, 4: 5})

    def test_favourites_hold(self):
        t, creator, regs = field(4, 'stepladder')
        gen_bracket(t, creator, strategy='registration')
        play_all(t.bracket_matches)
        self.assertEqual(positions(t), {1: 1, 2: 2, 3: 3, 4: 4})

    def test_a_table_feeds_a_stepladder(self):
        t, creator, regs = field(6)
        groups = add_stage(t, 0, 'round_robin', advances=4)
        ladder = add_stage(t, 1, 'stepladder')
        draw(groups, creator)
        play_all(groups.matches)
        with transaction.atomic():
            stage_engine.advance(groups, creator)
        ladder.refresh_from_db()
        self.assertEqual(ladder.matches.count(), 3)
        play_all(ladder.matches)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        self.assertEqual(sorted(p for p in positions(t).values() if p), [1, 2, 3, 4, 5, 6])


class PagePlayoffTests(TestCase):
    def test_shape(self):
        t, creator, regs = field(4, 'page_playoff')
        gen_bracket(t, creator, strategy='registration')
        ms = {(m.round_number, m.match_number): m for m in t.bracket_matches.all()}
        self.assertEqual(len(ms), 4)
        self.assertEqual({ms[(1, 1)].participant_1.seed, ms[(1, 1)].participant_2.seed}, {1, 2})
        self.assertEqual({ms[(1, 2)].participant_1.seed, ms[(1, 2)].participant_2.seed}, {3, 4})
        self.assertEqual(ms[(1, 2)].loser_place, 4)
        self.assertEqual(ms[(2, 1)].loser_place, 3)
        self.assertTrue(ms[(3, 1)].is_final)

    def test_the_loser_of_one_v_two_gets_a_second_chance(self):
        t, creator, regs = field(4, 'page_playoff')
        gen_bracket(t, creator, strategy='registration')
        ms = {(m.round_number, m.match_number): m for m in t.bracket_matches.all()}
        play(ms[(1, 1)], 0, 2)          # 2 beats 1: 2 to the final, 1 drops
        play(ms[(1, 2)], 2, 0)          # 3 beats 4: 4 is out in fourth
        semi = BracketMatch.objects.get(pk=ms[(2, 1)].pk)
        self.assertEqual({semi.participant_1.seed, semi.participant_2.seed}, {1, 3})
        play(semi, 2, 0)                # 1 beats 3
        final = BracketMatch.objects.get(pk=ms[(3, 1)].pk)
        self.assertEqual({final.participant_1.seed, final.participant_2.seed}, {1, 2})
        # Seed 1 wins the final from whichever slot it is in.
        play(final, *((2, 0) if final.participant_1.seed == 1 else (0, 2)))
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        self.assertEqual(positions(t), {1: 1, 2: 2, 3: 3, 4: 4})

    def test_only_four(self):
        t, creator, regs = field(5, 'page_playoff')
        with self.assertRaises(bracket_service.BracketError) as caught:
            gen_bracket(t, creator)
        self.assertEqual(caught.exception.code, 'too_many_entrants')


class WinnerStaysOnTests(TestCase):
    def test_one_pass_every_challenger_gets_one_go(self):
        t, creator, regs = field(5, 'winner_stays_on')
        gen_bracket(t, creator, strategy='registration')
        self.assertEqual(t.bracket_matches.count(), 1)
        play_all(t.bracket_matches)
        t.refresh_from_db()
        self.assertEqual(t.bracket_matches.count(), 4)
        self.assertEqual(t.status, 'completed')
        # Seed 1 won all four; everybody else has none, so seed order decides.
        self.assertEqual(positions(t), {1: 1, 2: 2, 3: 3, 4: 4, 5: 5})

    def test_the_next_challenger_is_the_next_seed(self):
        t, creator, regs = field(4, 'winner_stays_on')
        gen_bracket(t, creator, strategy='registration')
        first = t.bracket_matches.get()
        play(first, 0, 2)               # 2 beats 1
        second = t.bracket_matches.get(round_number=2)
        self.assertEqual((second.participant_1.seed, second.participant_2.seed), (2, 3))

    def test_a_streak_target_ends_it_and_losers_queue_again(self):
        t, creator, regs = field(4, 'winner_stays_on')
        t.options = {'streak_target': 2}
        t.save(update_fields=['options'])
        gen_bracket(t, creator, strategy='registration')
        # Worse seed always wins: 2 beats 1, 3 beats 2, 4 beats 3, then 4 v 1
        # (1 queued again after losing) and 4 wins: two in a row, over.
        play_all(t.bracket_matches, better_seed_wins=False)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        self.assertEqual(t.bracket_matches.count(), 4)
        last = t.bracket_matches.order_by('-round_number').first()
        self.assertEqual({last.participant_1.seed, last.participant_2.seed}, {4, 1})
        self.assertEqual(positions(t)[4], 1)

    def test_the_match_limit_stops_a_run_nobody_can_finish(self):
        t, creator, regs = field(3, 'winner_stays_on')
        stage = add_stage(t, 0, 'winner_stays_on',
                          settings={'streak_target': 5, 'max_matches': 4})
        draw(stage, creator)
        play_all(stage.matches, better_seed_wins=False)
        self.assertEqual(stage.matches.count(), 4)
        self.assertTrue(stage_engine.stage_finished(stage))

    def test_the_standings_name_the_holder_and_the_queue(self):
        t, creator, regs = field(4, 'winner_stays_on')
        stage = add_stage(t, 0, 'winner_stays_on')
        draw(stage, creator)
        play(stage.matches.get(), 2, 0)
        rows = {r['seed']: r for r in stage_engine.standings(stage)}
        self.assertTrue(rows[1]['holder'])
        # Seed 3 is already in match two; seed 4 is next in the queue.
        self.assertTrue(rows[3]['challenger'])
        self.assertEqual(rows[3]['status'], 'challenger')
        self.assertEqual(rows[4]['queue_position'], 1)
        self.assertEqual(rows[2]['status'], 'out')
