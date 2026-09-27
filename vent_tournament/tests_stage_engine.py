"""Stages that own their matches, every bracket type drawn as itself, and one
rule for a result.

CEO, 27 September 2026: "The option for creators to link different types of
bracket systems should be available to us too", for an FC Mobile and eFootball
event. Before this, Swiss and GSL were drawn as single elimination, a stage had
nowhere to put its matches, and a football draw could not be confirmed.
"""
from datetime import timedelta

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from . import results, stage_engine, stage_settings
from .models import BracketMatch, TournamentStage
from .services import bracket as bracket_service
from .tests import make_tournament, make_user, register


_block = [0]


def field(n, bracket_type='single_elimination'):
    _block[0] += 1
    base = 50000 + 1000 * _block[0]
    creator = make_user(base)
    t = make_tournament(creator, bracket_type=bracket_type)
    regs = []
    for i in range(n):
        reg = register(t, make_user(base + 1 + i))
        reg.seed = i + 1
        reg.save(update_fields=['seed'])
        regs.append(reg)
    return t, creator, regs


def add_stage(t, order, fmt, *, advances=0, groups=0, settings=None, placement='cross',
              direct=None, label=None):
    return TournamentStage.objects.create(
        tournament=t, order=order, label=label or fmt, format=fmt,
        advances=advances, groups=groups,
        settings=stage_settings.clean(fmt, settings or {}),
        placement=placement, direct_entrants=direct or [])


def play(match, s1, s2, pens=None, winner_id=None):
    """Record a result the way every door now does: decide, then apply."""
    match.refresh_from_db()
    with transaction.atomic():
        locked = BracketMatch.objects.select_for_update().get(pk=match.pk)
        decision = results.decide(
            locked, s1, s2,
            penalties_p1=pens[0] if pens else None,
            penalties_p2=pens[1] if pens else None, winner_id=winner_id)
        results.apply(locked, decision)
    match.refresh_from_db()
    return match


def play_out(stage, prefer_lower_seed=True, draw_every=0):
    """Play every open match of a stage, the better seed winning 2-0."""
    guard = 0
    while True:
        guard += 1
        assert guard < 200, 'stage never finished'
        open_ = list(stage.matches.filter(status='scheduled',
                                          participant_1__isnull=False,
                                          participant_2__isnull=False)
                     .select_related('participant_1', 'participant_2'))
        if not open_:
            return
        for i, m in enumerate(open_):
            if draw_every and m.draw_allowed and i % draw_every == 0:
                play(m, 1, 1)
                continue
            a, b = m.participant_1.seed or 99, m.participant_2.seed or 99
            if (a < b) == prefer_lower_seed:
                play(m, 2, 0)
            else:
                play(m, 0, 2)


def draw(stage, user):
    with transaction.atomic():
        return stage_engine.draw(stage, user)


class EveryFormatIsDrawnAsItselfTests(TestCase):
    def test_swiss_is_no_longer_a_knockout(self):
        t, creator, regs = field(8, 'swiss')
        with transaction.atomic():
            summary = bracket_service.generate(t, creator, seed_strategy='registration')
        self.assertEqual(summary['matches_created'], 4)
        first = t.bracket_matches.order_by('match_number').first()
        # Top half against bottom half: 1 v 5.
        self.assertEqual((first.participant_1.seed, first.participant_2.seed), (1, 5))

    def test_battle_royale_is_refused_with_a_code_not_drawn_as_something_else(self):
        t, creator, regs = field(4, 'battle_royale')
        with self.assertRaises(bracket_service.BracketError) as caught:
            with transaction.atomic():
                bracket_service.generate(t, creator)
        self.assertEqual(caught.exception.code, 'format_has_no_bracket')

    def test_gsl_needs_groups_of_four(self):
        t, creator, regs = field(10)
        stage = add_stage(t, 0, 'gsl')
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            draw(stage, creator)
        self.assertEqual(caught.exception.code, 'GSL_NEEDS_GROUPS_OF_FOUR')


class GslTests(TestCase):
    def test_two_groups_play_out_and_two_go_through_each(self):
        t, creator, regs = field(8)
        gsl = add_stage(t, 0, 'gsl', advances=2)
        add_stage(t, 1, 'single_elimination')
        draw(gsl, creator)
        self.assertEqual(gsl.matches.count(), 10)
        play_out(gsl)
        rows = stage_engine.standings(gsl)
        self.assertEqual(sorted(r['rank'] for r in rows if r['group'] == 1), [1, 2, 3, 4])
        chosen = stage_engine.advancing(gsl)
        self.assertEqual(len(chosen), 4)
        # Group winners first, then runners-up: 1A, 1B, 2A, 2B.
        self.assertEqual([(r['group'], r['rank']) for r in chosen],
                         [(1, 1), (2, 1), (1, 2), (2, 2)])


class SwissTests(TestCase):
    def test_rounds_are_paired_on_record_without_rematches_and_it_stops(self):
        t, creator, regs = field(8)
        swiss = add_stage(t, 0, 'swiss', settings={'rounds': 3})
        draw(swiss, creator)
        play_out(swiss)
        self.assertEqual(swiss.matches.count(), 12)
        pairs = [frozenset((m.participant_1_id, m.participant_2_id))
                 for m in swiss.matches.all()]
        self.assertEqual(len(pairs), len(set(pairs)), 'a rematch was drawn')
        # Round two pairs round-one winners with round-one winners.
        won_first = set(swiss.matches.filter(round_number=1).values_list('winner_id', flat=True))
        for m in swiss.matches.filter(round_number=2):
            self.assertEqual(m.participant_1_id in won_first,
                             m.participant_2_id in won_first)
        self.assertTrue(stage_engine.swiss_finished(swiss))
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')

    def test_win_target_and_loss_limit_take_people_out_of_pairing(self):
        t, creator, regs = field(8)
        swiss = add_stage(t, 0, 'swiss', advances=4,
                          settings={'rounds': 5, 'win_target': 2, 'loss_limit': 2})
        add_stage(t, 1, 'single_elimination')
        draw(swiss, creator)
        play_out(swiss)
        rows = stage_engine.standings(swiss)
        for r in rows:
            self.assertTrue(r['wins'] <= 2 and r['losses'] <= 2)
        self.assertEqual(sum(1 for r in rows if r['status'] == 'qualified'), 4)
        self.assertEqual(len(stage_engine.advancing(swiss)), 4)

    def test_an_odd_field_gives_the_bye_to_somebody_new_each_round(self):
        t, creator, regs = field(7)
        swiss = add_stage(t, 0, 'swiss', settings={'rounds': 3})
        draw(swiss, creator)
        play_out(swiss)
        byes = [m.winner_id for m in swiss.matches.filter(status='bye')]
        self.assertEqual(len(byes), 3)
        self.assertEqual(len(set(byes)), 3)


class GroupsIntoPlayoffTests(TestCase):
    def test_group_winners_meet_runners_up_of_another_group(self):
        t, creator, regs = field(8)
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        playoff = add_stage(t, 1, 'single_elimination')
        draw(groups, creator)
        self.assertEqual(groups.matches.count(), 12)       # two groups of four, 6 each
        self.assertEqual(set(groups.matches.values_list('group_number', flat=True)), {1, 2})
        play_out(groups)
        t.refresh_from_db()
        self.assertNotEqual(t.status, 'completed', 'the group stage ended the tournament')

        with transaction.atomic():
            stage_engine.advance(groups, creator)
        playoff.refresh_from_db()
        self.assertIsNotNone(playoff.drawn_at)
        first_round = playoff.matches.filter(round_number=1)
        group_of = {r['registration_id']: r['group'] for r in groups.advanced}
        for m in first_round:
            self.assertNotEqual(group_of[m.participant_1_id], group_of[m.participant_2_id])

        play_out(playoff)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        placed = dict(t.registrations.values_list('id', 'final_position'))
        self.assertEqual(sorted(p for p in placed.values() if p), list(range(1, 9)))

    def test_advancing_before_the_stage_is_played_is_refused(self):
        t, creator, regs = field(8)
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        add_stage(t, 1, 'single_elimination')
        draw(groups, creator)
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            stage_engine.advance(groups, creator)
        self.assertEqual(caught.exception.code, 'STAGE_NOT_FINISHED')

    def test_home_and_away_doubles_the_fixtures(self):
        t, creator, regs = field(4)
        league = add_stage(t, 0, 'round_robin', settings={'legs': 2})
        draw(league, creator)
        self.assertEqual(league.matches.count(), 12)

    def test_the_organiser_may_reorder_but_only_among_this_stages_entrants(self):
        t, creator, regs = field(8)
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        add_stage(t, 1, 'single_elimination')
        draw(groups, creator)
        play_out(groups)
        outsider = register(t, make_user(99999))
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            stage_engine.advance(groups, creator, order=[outsider.id, regs[0].id])
        self.assertEqual(caught.exception.code, 'NOT_IN_THIS_STAGE')


class ThreeStageChainTests(TestCase):
    """Groups, then Swiss, then double elimination with a reset. The chain EA's
    own FC Pro Mobile circuit runs, give or take the ladder."""

    def test_the_whole_chain_plays_through(self):
        t, creator, regs = field(16)
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=4)
        swiss = add_stage(t, 1, 'swiss', advances=4, settings={'rounds': 3})
        final = add_stage(t, 2, 'double_elimination', settings={'grand_final': 'reset'})

        draw(groups, creator)
        play_out(groups, draw_every=3)
        with transaction.atomic():
            stage_engine.advance(groups, creator)
        swiss.refresh_from_db()
        self.assertEqual(len(swiss.entrants), 8)

        play_out(swiss)
        with transaction.atomic():
            stage_engine.advance(swiss, creator)
        final.refresh_from_db()
        self.assertEqual(len(final.entrants), 4)

        play_out(final)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')
        positions = [p for p in t.registrations.values_list('final_position', flat=True) if p]
        self.assertEqual(sorted(positions), list(range(1, 17)))


class GrandFinalResetTests(TestCase):
    def _to_grand_final(self, lower_seed_wins_gf1):
        t, creator, regs = field(4)
        stage = add_stage(t, 0, 'double_elimination', settings={'grand_final': 'reset'})
        draw(stage, creator)
        for _ in range(10):
            gf1 = stage.matches.filter(bracket_side='grand_final', is_final=False).first()
            gf1.refresh_from_db()
            if gf1.participant_1_id and gf1.participant_2_id:
                break
            play_out_once(stage)
        if lower_seed_wins_gf1:
            play(gf1, 0, 2)
        else:
            play(gf1, 2, 0)
        return t, stage, gf1

    def test_winners_champion_wins_and_the_reset_is_not_played(self):
        t, stage, gf1 = self._to_grand_final(False)
        gf2 = stage.matches.get(bracket_side='grand_final', is_final=True)
        self.assertEqual(gf2.status, 'bye')
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')

    def test_losers_champion_wins_and_forces_the_reset(self):
        t, stage, gf1 = self._to_grand_final(True)
        gf2 = stage.matches.get(bracket_side='grand_final', is_final=True)
        self.assertEqual(gf2.status, 'scheduled')
        self.assertEqual({gf2.participant_1_id, gf2.participant_2_id},
                         {gf1.participant_1_id, gf1.participant_2_id})
        t.refresh_from_db()
        self.assertNotEqual(t.status, 'completed')
        play(gf2, 1, 0)
        t.refresh_from_db()
        self.assertEqual(t.status, 'completed')


def play_out_once(stage):
    for m in stage.matches.filter(status='scheduled', participant_1__isnull=False,
                                  participant_2__isnull=False, bracket_side__in=('winners', 'losers')):
        a, b = m.participant_1.seed or 99, m.participant_2.seed or 99
        play(m, 2, 0) if a < b else play(m, 0, 2)


class DirectEntrantTests(TestCase):
    def test_an_invited_side_skips_the_groups_and_is_seeded_first(self):
        t, creator, regs = field(9)
        invited = regs[-1]
        groups = add_stage(t, 0, 'round_robin', advances=1, groups=4)
        playoff = add_stage(t, 1, 'single_elimination', direct=[invited.id])
        draw(groups, creator)
        self.assertNotIn(invited.id, groups.entrants)
        play_out(groups)
        with transaction.atomic():
            stage_engine.advance(groups, creator)
        playoff.refresh_from_db()
        self.assertEqual(playoff.entrants[0], invited.id)
        self.assertEqual(len(playoff.entrants), 5)


class ThirdPlaceTests(TestCase):
    def test_a_stage_third_place_match_decides_third_and_fourth(self):
        t, creator, regs = field(4)
        stage = add_stage(t, 0, 'single_elimination', settings={'third_place': True})
        draw(stage, creator)
        self.assertEqual(stage.matches.count(), 4)
        play_out(stage)
        rows = stage_engine.standings(stage)
        self.assertEqual(sorted(r['rank'] for r in rows), [1, 2, 3, 4])


class ResultRuleTests(TestCase):
    def setUp(self):
        self.t, self.creator, self.regs = field(8)

    def _knockout_match(self, **settings):
        stage = add_stage(self.t, 0, 'single_elimination', settings=settings)
        draw(stage, self.creator)
        return stage.matches.filter(round_number=1).first()

    def test_a_group_draw_is_a_result(self):
        stage = add_stage(self.t, 0, 'round_robin', groups=2)
        draw(stage, self.creator)
        m = play(stage.matches.first(), 1, 1)
        self.assertEqual(m.status, 'completed')
        self.assertIsNone(m.winner_id)

    def test_a_level_knockout_needs_penalties(self):
        m = self._knockout_match()
        with self.assertRaises(results.ResultError) as caught:
            results.decide(m, 2, 2)
        self.assertEqual(caught.exception.code, 'LEVEL_NEEDS_A_DECIDER')
        m = play(m, 2, 2, pens=(3, 4))
        self.assertEqual(m.winner_id, m.participant_2_id)
        self.assertEqual((m.score_p1, m.score_p2, m.penalties_p1, m.penalties_p2), (2, 2, 3, 4))

    def test_penalties_beside_a_winning_score_are_refused(self):
        m = self._knockout_match()
        with self.assertRaises(results.ResultError) as caught:
            results.decide(m, 2, 1, penalties_p1=4, penalties_p2=3)
        self.assertEqual(caught.exception.code, 'PENALTIES_ONLY_WHEN_LEVEL')

    def test_the_winner_must_be_in_the_match(self):
        m = self._knockout_match()
        stranger = register(self.t, make_user(77777))
        with self.assertRaises(results.ResultError) as caught:
            results.decide(m, 1, 0, winner_id=stranger.id)
        self.assertEqual(caught.exception.code, 'WINNER_NOT_IN_MATCH')

    def test_a_named_winner_that_disagrees_with_the_score_is_refused(self):
        m = self._knockout_match()
        with self.assertRaises(results.ResultError) as caught:
            results.decide(m, 1, 0, winner_id=m.participant_2_id)
        self.assertEqual(caught.exception.code, 'WINNER_DISAGREES_WITH_SCORE')

    def test_a_best_of_three_cannot_end_three_one(self):
        m = self._knockout_match(best_of=3)
        self.assertEqual(m.best_of, 3)
        with self.assertRaises(results.ResultError) as caught:
            results.decide(m, 3, 1)
        self.assertEqual(caught.exception.code, 'SERIES_OVER_ITS_LENGTH')
        with self.assertRaises(results.ResultError):
            results.decide(m, 1, 0)
        self.assertEqual(results.decide(m, 2, 1).winner.id, m.participant_1_id)

    def test_a_best_of_two_holds_two_games(self):
        stage = add_stage(self.t, 0, 'round_robin', groups=2, settings={'best_of': 2})
        draw(stage, self.creator)
        m = stage.matches.first()
        self.assertEqual(m.best_of, 2)
        with self.assertRaises(results.ResultError) as caught:
            results.decide(m, 2, 1)
        self.assertEqual(caught.exception.code, 'SERIES_OVER_ITS_LENGTH')
        self.assertIsNone(results.decide(m, 1, 1).winner)
        self.assertEqual(results.decide(m, 2, 0).winner.id, m.participant_1_id)

    def test_the_games_must_add_up_to_the_headline(self):
        m = self._knockout_match(best_of=3)
        with self.assertRaises(results.ResultError) as caught:
            results.decide(m, 2, 0, games=[{'p1': 1, 'p2': 0}, {'p1': 0, 'p2': 2}])
        self.assertEqual(caught.exception.code, 'GAMES_DO_NOT_ADD_UP')

    def test_per_round_and_final_best_of_are_fixed_on_the_match(self):
        stage = add_stage(self.t, 0, 'single_elimination',
                          settings={'best_of': 1, 'final_best_of': 5,
                                    'round_best_of': {'2': 3}})
        draw(stage, self.creator)
        self.assertEqual(stage.matches.get(round_number=1, match_number=1).best_of, 1)
        self.assertEqual(stage.matches.get(round_number=2, match_number=1).best_of, 3)
        self.assertEqual(stage.matches.get(is_final=True).best_of, 5)

    def test_a_single_format_round_robin_still_takes_a_draw(self):
        t, creator, regs = field(4, 'round_robin')
        with transaction.atomic():
            bracket_service.generate(t, creator)
        m = play(t.bracket_matches.first(), 0, 0)
        self.assertIsNone(m.winner_id)


class NoShowTests(TestCase):
    def test_the_side_that_checked_in_wins_by_forfeit_when_the_clock_runs_out(self):
        t, creator, regs = field(4)
        stage = add_stage(t, 0, 'single_elimination', settings={'check_in_minutes': 10})
        draw(stage, creator)
        m = stage.matches.filter(round_number=1).first()
        self.assertIsNotNone(m.check_in_deadline)
        stage_engine.check_in(m, m.participant_1.user)
        self.assertEqual(stage_engine.sweep_no_shows(t, now=timezone.now()), 0)
        later = m.check_in_deadline + timedelta(seconds=1)
        self.assertEqual(stage_engine.sweep_no_shows(t, now=later), 1)
        m.refresh_from_db()
        self.assertEqual(m.winner_id, m.participant_1_id)
        self.assertEqual((m.score_p1, m.score_p2, m.forfeit_reason), (3, 0, 'no_show'))
        # And the winner was sent on.
        nxt = BracketMatch.objects.get(pk=m.winner_to_match_id)
        self.assertIn(m.participant_1_id, (nxt.participant_1_id, nxt.participant_2_id))

    def test_neither_checked_in_is_left_for_the_organiser(self):
        t, creator, regs = field(4)
        stage = add_stage(t, 0, 'single_elimination', settings={'check_in_minutes': 5})
        draw(stage, creator)
        m = stage.matches.filter(round_number=1).first()
        later = m.check_in_deadline + timedelta(minutes=1)
        self.assertEqual(stage_engine.sweep_no_shows(t, now=later), 0)

    def test_only_a_side_in_the_match_can_check_in(self):
        t, creator, regs = field(4)
        stage = add_stage(t, 0, 'single_elimination', settings={'check_in_minutes': 5})
        draw(stage, creator)
        m = stage.matches.filter(round_number=1).first()
        with self.assertRaises(stage_engine.StageEngineError) as caught:
            stage_engine.check_in(m, creator)
        self.assertEqual(caught.exception.code, 'NOT_IN_THIS_MATCH')


class DisputeWindowTests(TestCase):
    """The organiser's dispute window is read, 24 hours by default."""

    def _completed_match(self, minutes_ago, window=None):
        from rest_framework.test import APIClient
        from . import options as tournament_options
        t, creator, regs = field(4)
        if window is not None:
            t.options = tournament_options.clean({'dispute_window_minutes': window})
            t.save(update_fields=['options'])
        stage = add_stage(t, 0, 'single_elimination')
        draw(stage, creator)
        m = play(stage.matches.filter(round_number=1).first(), 1, 0)
        BracketMatch.objects.filter(pk=m.pk).update(
            completed_at=timezone.now() - timedelta(minutes=minutes_ago))
        c = APIClient()
        user = m.participant_2.user
        c.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
        return c.post('/tournament/match/%s/raise-dispute/' % m.pk,
                      {'description': 'wrong score'}, format='json')

    def test_the_default_is_a_day(self):
        self.assertEqual(self._completed_match(23 * 60).status_code, 201)
        self.assertEqual(self._completed_match(25 * 60).json()['code'], 'DISPUTE_WINDOW_CLOSED')

    def test_the_organisers_choice_is_read(self):
        self.assertEqual(self._completed_match(45, window=30).json()['code'], 'DISPUTE_WINDOW_CLOSED')
        self.assertEqual(self._completed_match(45, window=120).status_code, 201)


class SettingsTests(TestCase):
    def test_a_knockout_cannot_allow_draws(self):
        with self.assertRaises(stage_settings.SettingsError) as caught:
            stage_settings.clean('single_elimination', {'draws': 'allowed'})
        self.assertEqual(caught.exception.code, 'KNOCKOUT_NEEDS_A_WINNER')

    def test_football_presets(self):
        group = stage_settings.preset_settings('EA SPORTS FC Mobile', 'round_robin')
        self.assertEqual((group['best_of'], group['draws']), (2, 'allowed'))
        ko = stage_settings.preset_settings('eFootball 2026', 'single_elimination')
        self.assertEqual((ko['draws'], ko['final_best_of']), ('penalties', 3))
        self.assertIsNone(stage_settings.preset_settings('Free Fire', 'round_robin'))
