"""P4b, running a tournament (design section 12; CEO released it 24 October 2026, inbox 544).

Report auto-approval: a score one side reported and nobody contested is
confirmed when the organiser's window ends, through the same function the
opponent's confirmation calls. Reset preview: every door that undoes played
results can first say what it would undo, and change nothing. Bracket repairs:
reopen one finished group, delete the last Swiss round, regenerate a round
without somebody who withdrew.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import options as tournament_options
from . import results, stage_engine
from .models import BracketEdit, BracketMatch, MatchScore, TournamentDispute
from .tests import client_for
from .tests_bracket_edit import url
from .tests_stage_engine import add_stage, draw, field, play, play_out


def with_window(t, minutes):
    t.options = tournament_options.clean({'auto_confirm_minutes': minutes})
    t.save(update_fields=['options'])


class AutoConfirmOptionTests(TestCase):
    def test_off_by_default_and_clamped(self):
        clean = tournament_options.clean
        self.assertEqual(clean({})['auto_confirm_minutes'], 0)
        self.assertEqual(clean({'auto_confirm_minutes': 0})['auto_confirm_minutes'], 0)
        self.assertEqual(clean({'auto_confirm_minutes': 2})['auto_confirm_minutes'], 5)
        self.assertEqual(clean({'auto_confirm_minutes': 90})['auto_confirm_minutes'], 90)
        self.assertEqual(clean({'auto_confirm_minutes': 99999})['auto_confirm_minutes'], 1440)


class AutoConfirmTests(TestCase):
    def reported(self, window=30):
        t, creator, regs = field(4)
        with_window(t, window)
        stage = add_stage(t, 0, 'single_elimination')
        draw(stage, creator)
        m = stage.matches.filter(round_number=1, participant_2__isnull=False).first()
        res = client_for(m.participant_1.user).post(
            '/tournament/match/%s/report-score/' % m.pk,
            {'score_p1': 2, 'score_p2': 0}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        reported_at = timezone.now() - timedelta(minutes=1)
        MatchScore.objects.filter(match=m).update(submitted_at=reported_at)
        return t, stage, m, reported_at

    def test_confirmed_once_the_window_has_passed(self):
        t, stage, m, at = self.reported(30)
        self.assertEqual(stage_engine.sweep_auto_confirms(t, now=at + timedelta(minutes=31)), 1)
        m.refresh_from_db()
        self.assertEqual((m.status, m.winner_id), ('completed', m.participant_1_id))
        nxt = BracketMatch.objects.get(pk=m.winner_to_match_id)
        self.assertIn(m.winner_id, (nxt.participant_1_id, nxt.participant_2_id))
        sub = MatchScore.objects.get(match=m)
        self.assertTrue(sub.confirmed)
        self.assertIsNone(sub.confirmed_by)
        self.assertIsNotNone(sub.confirmed_at)

    def test_the_history_says_it_was_automatic(self):
        t, stage, m, at = self.reported(30)
        stage_engine.sweep_auto_confirms(t, now=at + timedelta(minutes=31))
        edit = BracketEdit.objects.get(tournament=t, kind='auto_confirm')
        self.assertIsNone(edit.user_id)
        self.assertEqual(edit.detail['match'], m.id)
        self.assertEqual(edit.detail['minutes'], 30)

    def test_not_before_the_window_ends(self):
        t, stage, m, at = self.reported(30)
        self.assertEqual(stage_engine.sweep_auto_confirms(t, now=at + timedelta(minutes=29)), 0)
        m.refresh_from_db()
        self.assertEqual(m.status, 'pending_opponent_confirm')
        self.assertFalse(BracketEdit.objects.filter(kind='auto_confirm').exists())

    def test_a_disputed_report_is_never_confirmed(self):
        t, stage, m, at = self.reported(30)
        res = client_for(m.participant_2.user).post(
            '/tournament/match/%s/confirm-score/' % m.pk,
            {'agree': False, 'dispute_description': 'that is not what happened'}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(stage_engine.sweep_auto_confirms(t, now=at + timedelta(days=2)), 0)
        m.refresh_from_db()
        self.assertEqual(m.status, 'disputed')
        self.assertTrue(TournamentDispute.objects.filter(match=m, status='open').exists())

    def test_option_off_does_nothing(self):
        t, stage, m, at = self.reported(0)
        self.assertEqual(stage_engine.sweep_auto_confirms(t, now=at + timedelta(days=30)), 0)
        m.refresh_from_db()
        self.assertEqual(m.status, 'pending_opponent_confirm')

    def test_it_goes_through_the_function_the_opponent_uses(self):
        t, stage, m, at = self.reported(30)
        with mock.patch.object(results, 'confirm_report', wraps=results.confirm_report) as spy:
            stage_engine.sweep_auto_confirms(t, now=at + timedelta(minutes=31))
        self.assertEqual(spy.call_count, 1)
        self.assertTrue(spy.call_args.kwargs['automatic'])
        t2, stage2, m2, at2 = self.reported(30)
        with mock.patch.object(results, 'confirm_report', wraps=results.confirm_report) as spy:
            res = client_for(m2.participant_2.user).post(
                '/tournament/match/%s/confirm-score/' % m2.pk, {'agree': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(spy.call_count, 1)
        self.assertFalse(spy.call_args.kwargs.get('automatic'))
        self.assertEqual(MatchScore.objects.get(match=m2).confirmed_by_id, m2.participant_2.user_id)

    def test_reading_the_bracket_sweeps(self):
        t, stage, m, at = self.reported(30)
        MatchScore.objects.filter(match=m).update(submitted_at=timezone.now() - timedelta(minutes=45))
        res = client_for(t.tournament_creator).get('/tournament/get-tournament-brackets/%s/' % (t.slug or t.tournament_id))
        self.assertEqual(res.status_code, 200, res.content[:300])
        m.refresh_from_db()
        self.assertEqual(m.status, 'completed')


def worked_groups(case, advance=True):
    """Eight entrants in two groups, played out, sent on to an undrawn playoff."""
    t, creator, regs = field(8, bracket_type='round_robin')
    groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
    playoff = add_stage(t, 1, 'single_elimination')
    draw(groups, creator)
    play_out(groups)
    if advance:
        res = client_for(creator).post(url(t, 'stages/%d/advance/' % groups.id),
                                       {'draw_next': False}, format='json')
        case.assertEqual(res.status_code, 200, res.content[:300])
    return t, creator, regs, groups, playoff


class ResetPreviewTests(TestCase):
    def snapshot(self, t):
        return (list(BracketMatch.objects.filter(tournament=t).order_by('id')
                     .values_list('id', 'status', 'winner_id', 'score_p1', 'score_p2')),
                list(t.stages.order_by('order').values_list('status', 'drawn_at', 'advanced')),
                BracketEdit.objects.filter(tournament=t).count())

    def two_stage(self):
        t, creator, regs = field(8, bracket_type='round_robin')
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        playoff = add_stage(t, 1, 'single_elimination')
        draw(groups, creator)
        return t, creator, regs, groups, playoff

    def test_undraw_preview_lists_what_would_go_and_changes_nothing(self):
        t, creator, regs, groups, playoff = self.two_stage()
        play(groups.matches.filter(participant_2__isnull=False).first(), 2, 1)
        before = self.snapshot(t)
        res = client_for(creator).post(url(t, 'stages/%d/undraw/' % groups.id),
                                       {'preview': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        data = res.json()['data']
        self.assertTrue(data['preview'])
        self.assertEqual(data['matches_removed'], groups.matches.count())
        self.assertEqual({row['id'] for row in data['matches_undone']},
                         set(groups.matches.values_list('id', flat=True)))
        self.assertEqual(data['results_cleared'], 1)
        self.assertTrue(data['needs_discard_results'])
        self.assertEqual(set(data['entrants_moved']), {r.id for r in regs})
        self.assertEqual(self.snapshot(t), before)

    def test_the_flag_is_read_from_the_query_too(self):
        t, creator, regs, groups, playoff = self.two_stage()
        before = self.snapshot(t)
        res = client_for(creator).post(url(t, 'stages/%d/undraw/?preview=1' % groups.id),
                                       {}, format='json')
        self.assertTrue(res.json()['data']['preview'])
        self.assertEqual(self.snapshot(t), before)

    def test_undraw_without_the_flag_is_unchanged(self):
        t, creator, regs, groups, playoff = self.two_stage()
        play(groups.matches.filter(participant_2__isnull=False).first(), 2, 1)
        res = client_for(creator).post(url(t, 'stages/%d/undraw/' % groups.id), {}, format='json')
        self.assertEqual(res.json()['code'], 'STAGE_HAS_RESULTS')
        res = client_for(creator).post(url(t, 'stages/%d/undraw/' % groups.id),
                                       {'discard_results': True}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('preview', res.json()['data'])
        self.assertFalse(groups.matches.exists())

    def test_a_preview_is_still_refused_where_the_change_would_be(self):
        t, creator, regs, groups, playoff = self.two_stage()
        match = groups.matches.filter(participant_2__isnull=False).first()
        TournamentDispute.objects.create(tournament=t, match=match, raised_by=creator,
                                         description='x', status='open')
        res = client_for(creator).post(url(t, 'stages/%d/undraw/' % groups.id),
                                       {'preview': True}, format='json')
        self.assertEqual(res.json()['code'], 'DISPUTES_OPEN')

    def test_reopen_stage_preview(self):
        t, creator, regs, groups, playoff = worked_groups(self)
        groups.refresh_from_db()
        before = self.snapshot(t)
        sent = {row['registration_id'] if isinstance(row, dict) else row for row in groups.advanced}
        res = client_for(creator).post(url(t, 'stages/%d/reopen/' % groups.id),
                                       {'preview': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        data = res.json()['data']
        self.assertTrue(data['preview'])
        self.assertEqual(set(data['entrants_moved']), sent)
        self.assertEqual(data['results_cleared'], 0)
        self.assertEqual(self.snapshot(t), before)
        res = client_for(creator).post(url(t, 'stages/%d/reopen/' % groups.id), {}, format='json')
        self.assertEqual(res.status_code, 200)
        groups.refresh_from_db()
        self.assertEqual(groups.status, 'running')

    def test_reformat_preview(self):
        t, creator, regs, groups, playoff = self.two_stage()
        play(groups.matches.filter(participant_2__isnull=False).first(), 2, 1)
        before = self.snapshot(t)
        res = client_for(creator).post(url(t, 'stages/%d/reformat/' % groups.id),
                                       {'format': 'swiss', 'groups': 0, 'settings': {'rounds': 3},
                                        'preview': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        data = res.json()['data']
        self.assertTrue(data['preview'])
        self.assertEqual((data['format_from'], data['format_to']), ('round_robin', 'swiss'))
        self.assertEqual(data['results_cleared'], 1)
        self.assertTrue(data['needs_discard_results'])
        self.assertEqual(self.snapshot(t), before)
        groups.refresh_from_db()
        self.assertEqual(groups.format, 'round_robin')
        res = client_for(creator).post(url(t, 'stages/%d/reformat/' % groups.id),
                                       {'format': 'swiss', 'groups': 0, 'settings': {'rounds': 3},
                                        'discard_results': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])

    def test_reformat_preview_refuses_a_format_the_plan_cannot_hold(self):
        t, creator, regs, groups, playoff = self.two_stage()
        res = client_for(creator).post(url(t, 'stages/%d/reformat/' % groups.id),
                                       {'format': 'no_such_format', 'preview': True},
                                       format='json')
        self.assertNotEqual(res.status_code, 200)
        groups.refresh_from_db()
        self.assertEqual(groups.format, 'round_robin')

    def test_reopen_match_preview(self):
        t, creator, regs = field(8)
        stage = add_stage(t, 0, 'single_elimination')
        draw(stage, creator)
        m = stage.matches.filter(round_number=1, participant_2__isnull=False).first()
        play(m, 2, 0)
        before = self.snapshot(t)
        res = client_for(creator).post(url(t, 'matches/%d/reopen/' % m.id),
                                       {'preview': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        data = res.json()['data']
        self.assertTrue(data['preview'])
        self.assertEqual(data['results_cleared'], 1)
        self.assertEqual(data['entrants_moved'], [m.winner_id])
        self.assertEqual({row['id'] for row in data['matches_undone']}, {m.id, m.winner_to_match_id})
        self.assertEqual(self.snapshot(t), before)
        res = client_for(creator).post(url(t, 'matches/%d/reopen/' % m.id), {}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIn('match', res.json()['data'])


class ReopenOneGroupTests(TestCase):
    def post(self, t, creator, stage, group, body=None):
        return client_for(creator).post(url(t, 'stages/%d/groups/%d/reopen/' % (stage.id, group)),
                                        body or {}, format='json')

    def test_a_group_that_is_not_finished_is_refused(self):
        t, creator, regs = field(8, bracket_type='round_robin')
        groups = add_stage(t, 0, 'round_robin', advances=2, groups=2)
        add_stage(t, 1, 'single_elimination')
        draw(groups, creator)
        res = self.post(t, creator, groups, 1)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'GROUP_NOT_FINISHED'))

    def test_an_unknown_group_is_refused(self):
        t, creator, regs, groups, playoff = worked_groups(self)
        res = self.post(t, creator, groups, 9)
        self.assertEqual((res.status_code, res.json()['code']), (404, 'GROUP_NOT_FOUND'))

    def test_nothing_to_reopen_before_anybody_was_sent_on(self):
        t, creator, regs, groups, playoff = worked_groups(self, advance=False)
        res = self.post(t, creator, groups, 1)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'NOTHING_TO_REOPEN'))

    def test_the_group_opens_again_and_what_it_sent_comes_back(self):
        t, creator, regs, groups, playoff = worked_groups(self)
        res = self.post(t, creator, groups, 1)
        self.assertEqual(res.status_code, 200, res.content[:400])
        groups.refresh_from_db()
        self.assertEqual((groups.status, groups.advanced), ('running', []))
        self.assertFalse(groups.outgoing.filter(fired_at__isnull=False).exists())
        self.assertTrue(BracketEdit.objects.filter(tournament=t, kind='reopen_group').exists())

    def test_preview_changes_nothing(self):
        t, creator, regs, groups, playoff = worked_groups(self)
        res = self.post(t, creator, groups, 1, {'preview': True})
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.assertTrue(res.json()['data']['preview'])
        self.assertTrue(res.json()['data']['entrants_moved'])
        groups.refresh_from_db()
        self.assertEqual(groups.status, 'complete')
        self.assertTrue(groups.outgoing.filter(fired_at__isnull=False).exists())

    def test_the_stage_it_filled_is_cleared_with_it(self):
        t, creator, regs, groups, playoff = worked_groups(self)
        playoff.refresh_from_db()
        draw(playoff, creator)
        self.assertTrue(playoff.matches.exists())
        res = self.post(t, creator, groups, 2)
        self.assertEqual(res.status_code, 200, res.content[:400])
        playoff.refresh_from_db()
        self.assertFalse(playoff.matches.exists())
        self.assertIsNone(playoff.drawn_at)

    def test_played_playoff_results_are_not_thrown_away_unasked(self):
        t, creator, regs, groups, playoff = worked_groups(self)
        draw(playoff, creator)
        play(playoff.matches.filter(participant_2__isnull=False).first(), 2, 0)
        res = self.post(t, creator, groups, 1)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'STAGE_HAS_RESULTS'))
        groups.refresh_from_db()
        self.assertEqual(groups.status, 'complete')
        res = self.post(t, creator, groups, 1, {'preview': True})
        self.assertEqual(res.json()['data']['results_cleared'], 1)
        res = self.post(t, creator, groups, 1, {'discard_results': True})
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.assertFalse(playoff.matches.exists())


class SwissRoundTests(TestCase):
    def swiss(self, rounds_played=1, n=8):
        t, creator, regs = field(n, 'swiss')
        stage = add_stage(t, 0, 'swiss', settings={'rounds': 4})
        draw(stage, creator)
        for _ in range(rounds_played):
            for m in stage.matches.filter(status='scheduled', participant_1__isnull=False,
                                          participant_2__isnull=False):
                play(m, 2, 0)
        return t, creator, regs, stage

    def post(self, t, creator, stage, rnd, verb, body=None):
        return client_for(creator).post(
            url(t, 'stages/%d/rounds/%d/%s/' % (stage.id, rnd, verb)), body or {}, format='json')

    def rounds(self, stage):
        return sorted(set(stage.matches.values_list('round_number', flat=True)))

    def test_the_last_round_goes_when_nothing_in_it_was_played(self):
        t, creator, regs, stage = self.swiss()
        self.assertEqual(self.rounds(stage), [1, 2])
        res = self.post(t, creator, stage, 2, 'delete')
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.assertEqual(self.rounds(stage), [1])
        self.assertTrue(BracketEdit.objects.filter(tournament=t, kind='drop_round').exists())

    def test_only_the_last_round(self):
        t, creator, regs, stage = self.swiss()
        res = self.post(t, creator, stage, 1, 'delete')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'NOT_THE_LAST_ROUND'))
        self.assertEqual(self.rounds(stage), [1, 2])

    def test_not_once_a_result_is_in(self):
        t, creator, regs, stage = self.swiss()
        play(stage.matches.filter(round_number=2, status='scheduled').first(), 2, 0)
        res = self.post(t, creator, stage, 2, 'delete')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'ROUND_HAS_RESULTS'))
        self.assertEqual(self.rounds(stage), [1, 2])

    def test_a_stage_that_is_not_swiss_is_refused(self):
        t, creator, regs = field(4)
        stage = add_stage(t, 0, 'single_elimination')
        draw(stage, creator)
        res = self.post(t, creator, stage, 1, 'delete')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'FORMAT_NOT_SUPPORTED'))

    def test_delete_preview_changes_nothing(self):
        t, creator, regs, stage = self.swiss()
        count = stage.matches.count()
        res = self.post(t, creator, stage, 2, 'delete', {'preview': True})
        self.assertEqual(res.status_code, 200, res.content[:400])
        data = res.json()['data']
        self.assertTrue(data['preview'])
        self.assertEqual(data['matches_removed'], stage.matches.filter(round_number=2).count())
        self.assertEqual(stage.matches.count(), count)

    def test_a_round_is_paired_again_without_somebody_who_withdrew(self):
        t, creator, regs, stage = self.swiss()
        leaver = stage.matches.filter(round_number=2, participant_2__isnull=False).first().participant_1
        leaver.status = 'withdrawn'
        leaver.save(update_fields=['status'])
        res = self.post(t, creator, stage, 2, 'regenerate')
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.assertEqual(self.rounds(stage), [1, 2])
        again = stage.matches.filter(round_number=2)
        self.assertFalse(again.filter(participant_1=leaver).exists())
        self.assertFalse(again.filter(participant_2=leaver).exists())
        self.assertFalse(again.filter(winner=leaver).exists())
        self.assertEqual(again.exclude(status='bye').count(), 3)
        self.assertTrue(BracketEdit.objects.filter(tournament=t, kind='regen_round').exists())

    def test_nothing_to_regenerate_when_nobody_left(self):
        t, creator, regs, stage = self.swiss()
        res = self.post(t, creator, stage, 2, 'regenerate')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'NOTHING_TO_REGENERATE'))

    def test_regenerate_follows_the_same_round_rules(self):
        t, creator, regs, stage = self.swiss()
        regs[0].status = 'withdrawn'
        regs[0].save(update_fields=['status'])
        res = self.post(t, creator, stage, 1, 'regenerate')
        self.assertEqual(res.json()['code'], 'NOT_THE_LAST_ROUND')
        play(stage.matches.filter(round_number=2, status='scheduled').first(), 2, 0)
        res = self.post(t, creator, stage, 2, 'regenerate')
        self.assertEqual(res.json()['code'], 'ROUND_HAS_RESULTS')

    def test_regenerate_preview_changes_nothing(self):
        t, creator, regs, stage = self.swiss()
        leaver = stage.matches.filter(round_number=2, participant_2__isnull=False).first().participant_1
        leaver.status = 'withdrawn'
        leaver.save(update_fields=['status'])
        before = list(stage.matches.filter(round_number=2).order_by('id')
                      .values_list('id', 'participant_1_id', 'participant_2_id'))
        res = self.post(t, creator, stage, 2, 'regenerate', {'preview': True})
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.assertTrue(res.json()['data']['preview'])
        self.assertEqual(list(stage.matches.filter(round_number=2).order_by('id')
                              .values_list('id', 'participant_1_id', 'participant_2_id')), before)

    def test_a_stranger_is_refused(self):
        t, creator, regs, stage = self.swiss()
        other = regs[3].user
        for verb in ('delete', 'regenerate'):
            res = client_for(other).post(
                url(t, 'stages/%d/rounds/2/%s/' % (stage.id, verb)), {}, format='json')
            self.assertIn(res.status_code, (403, 404))
        self.assertEqual(self.rounds(stage), [1, 2])
