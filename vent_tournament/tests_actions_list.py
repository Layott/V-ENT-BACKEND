"""What the people running a tournament need to settle (P4a).

Design section 12: "an actions list: ties, disputes, stuck matches, qualifiers
who declined, pulls waiting for a preview". Each test is one rule of
`next_actions.actions_list`, and the last group is who sees which kind.
"""
import itertools
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import next_actions
from .models import BracketMatch, MatchScore, TournamentDispute, TournamentStaff
from .tests import make_tournament, make_user, register
from .tests_feeds import advance, qualifier_and_finals
from .tests_stage_engine import add_stage, draw, field, play_out

_N = itertools.count(4600000)


def person():
    return make_user(next(_N))


def kinds(items):
    return [i['kind'] for i in items]


class MatchDeskBase(TestCase):
    """A live tournament with one open match between two entrants."""

    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org, status='live')
        self.ra, self.rb = register(self.t, person()), register(self.t, person())
        self.m = BracketMatch.objects.create(tournament=self.t, round_number=1, match_number=1,
                                             participant_1=self.ra, participant_2=self.rb)

    def items(self, user=None, **kw):
        return next_actions.actions_list(self.t, user or self.org, **kw)


class DisputeTests(MatchDeskBase):
    def test_an_open_dispute_is_listed_with_who_raised_it(self):
        raiser = self.ra.user
        TournamentDispute.objects.create(tournament=self.t, match=self.m, raised_by=raiser,
                                         description='Wrong score')
        items = self.items()
        self.assertEqual(kinds(items), ['dispute'])
        self.assertEqual(items[0]['key'], 'todo.dispute')
        self.assertEqual(items[0]['permission'], 'report')
        self.assertEqual(items[0]['detail']['raised_by'], raiser.username)
        self.assertEqual(items[0]['match']['id'], self.m.id)
        self.assertEqual(len(items[0]['match']['sides']), 2)

    def test_under_review_still_counts_and_resolved_does_not(self):
        raiser = self.ra.user
        TournamentDispute.objects.create(tournament=self.t, match=self.m, raised_by=raiser,
                                         description='a', status='under_review')
        TournamentDispute.objects.create(tournament=self.t, match=self.m, raised_by=raiser,
                                         description='b', status='resolved')
        TournamentDispute.objects.create(tournament=self.t, match=self.m, raised_by=raiser,
                                         description='c', status='dismissed')
        self.assertEqual(kinds(self.items()), ['dispute'])


class StuckMatchTests(MatchDeskBase):
    def test_nobody_checked_in_after_the_deadline_is_a_no_show(self):
        self.m.check_in_deadline = timezone.now() - timedelta(minutes=5)
        self.m.save(update_fields=['check_in_deadline'])
        self.assertEqual(kinds(self.items()), ['no_show'])

    def test_one_side_checked_in_is_the_timer_s_to_settle_not_a_person_s(self):
        self.m.check_in_deadline = timezone.now() - timedelta(minutes=5)
        self.m.checked_in_p1_at = timezone.now() - timedelta(minutes=10)
        self.m.save(update_fields=['check_in_deadline', 'checked_in_p1_at'])
        self.assertEqual(self.items(), [])

    def test_a_deadline_still_ahead_is_not_a_no_show(self):
        self.m.check_in_deadline = timezone.now() + timedelta(minutes=5)
        self.m.save(update_fields=['check_in_deadline'])
        self.assertEqual(self.items(), [])

    def _submit(self, age):
        score = MatchScore.objects.create(match=self.m, submitted_by=self.ra.user,
                                          score_p1=2, score_p2=0)
        MatchScore.objects.filter(pk=score.pk).update(submitted_at=timezone.now() - age)
        self.m.status = 'pending_opponent_confirm'
        self.m.save(update_fields=['status'])
        return score

    def test_a_report_nobody_confirmed_for_an_hour_is_listed(self):
        score = self._submit(next_actions.UNCONFIRMED_AFTER + timedelta(minutes=1))
        items = self.items()
        self.assertEqual(kinds(items), ['unconfirmed'])
        score.refresh_from_db()
        self.assertEqual(items[0]['since'], score.submitted_at)

    def test_a_fresh_report_is_not_yet_a_problem(self):
        self._submit(timedelta(minutes=5))
        self.assertEqual(self.items(), [])

    def test_a_superseded_report_is_not_the_one_that_counts(self):
        old = self._submit(timedelta(hours=5))
        fresh = MatchScore.objects.create(match=self.m, submitted_by=self.rb.user,
                                          score_p1=0, score_p2=2)
        MatchScore.objects.filter(pk=old.pk).update(superseded_by=fresh)
        self.assertEqual(self.items(), [])

    def test_a_match_past_its_start_by_two_hours_is_late(self):
        self.m.scheduled_at = timezone.now() - next_actions.LATE_AFTER - timedelta(minutes=1)
        self.m.save(update_fields=['scheduled_at'])
        self.assertEqual(kinds(self.items()), ['late'])
        self.m.scheduled_at = timezone.now() - timedelta(hours=1)
        self.m.save(update_fields=['scheduled_at'])
        self.assertEqual(self.items(), [])

    def test_a_match_with_no_start_or_an_empty_seat_is_never_late(self):
        BracketMatch.objects.create(tournament=self.t, round_number=2, match_number=1,
                                    participant_1=self.ra,
                                    scheduled_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.items(), [])

    def test_a_no_show_is_not_also_listed_as_late(self):
        self.m.scheduled_at = timezone.now() - timedelta(hours=5)
        self.m.check_in_deadline = timezone.now() - timedelta(hours=4)
        self.m.save(update_fields=['scheduled_at', 'check_in_deadline'])
        self.assertEqual(kinds(self.items()), ['no_show'])


def standings_rows(layout, group=None):
    """Rows as `stage_engine.standings` makes them: (name, rank, tied)."""
    return [{'registration_id': i + 1, 'name': name, 'group': group, 'rank': rank,
             'position': i + 1, 'tied': tied}
            for i, (name, rank, tied) in enumerate(layout)]


class TieTests(TestCase):
    def setUp(self):
        self.t, self.creator, _regs = field(4, 'round_robin')
        self.stage = add_stage(self.t, 0, 'round_robin', advances=2)
        self.playoff = add_stage(self.t, 1, 'single_elimination')
        draw(self.stage, self.creator)
        play_out(self.stage)

    def items(self, rows, user=None):
        with mock.patch('vent_tournament.stage_engine.standings', return_value=rows):
            return next_actions.actions_list(self.t, user or self.creator)

    def level_second_and_third(self):
        return standings_rows([('A', 1, False), ('B', 2, True), ('C', 2, True), ('D', 4, False)])

    def test_a_tie_across_the_cut_is_listed_with_the_places_and_names(self):
        ties = [i for i in self.items(self.level_second_and_third()) if i['kind'] == 'tie']
        self.assertEqual(len(ties), 1)
        self.assertEqual(ties[0]['permission'], 'place')
        self.assertEqual(ties[0]['detail']['places'], [2, 3])
        self.assertEqual(ties[0]['detail']['names'], ['B', 'C'])
        self.assertEqual(ties[0]['stage']['id'], self.stage.id)

    def test_a_tie_inside_the_places_a_line_sends_is_not_an_action(self):
        # Places 1 to 4 go through: second and third level changes nobody's fate.
        self.stage.advances = 4
        self.stage.save(update_fields=['advances'])
        self.playoff.delete()
        self.playoff = add_stage(self.t, 1, 'single_elimination')
        from .models import Progression
        Progression.objects.all().delete()
        Progression.objects.create(source=self.stage, target=self.playoff, place_from=1, place_to=4)
        self.assertNotIn('tie', kinds(self.items(self.level_second_and_third())))

    def test_a_tie_below_the_cut_is_not_an_action(self):
        rows = standings_rows([('A', 1, False), ('B', 2, False), ('C', 3, True), ('D', 3, True)])
        self.assertNotIn('tie', kinds(self.items(rows)))

    def test_ties_are_read_group_by_group(self):
        rows = (standings_rows([('A', 1, False), ('B', 2, False)], group=1)
                + standings_rows([('C', 1, False), ('D', 2, False)], group=2))
        self.assertNotIn('tie', kinds(self.items(rows)))

    def test_a_stage_still_being_played_lists_no_tie(self):
        m = self.stage.matches.first()
        BracketMatch.objects.filter(pk=m.pk).update(status='scheduled')
        self.assertNotIn('tie', kinds(self.items(self.level_second_and_third())))

    def test_a_stage_already_advanced_lists_no_tie(self):
        self.stage.outgoing.update(fired_at=timezone.now())
        self.assertNotIn('tie', kinds(self.items(self.level_second_and_third())))


class FeedTests(TestCase):
    def test_a_declined_place_waits_for_the_organiser_of_the_tournament_it_fills(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        line.waiting = [{'place': 3, 'group': None}, {'place': 4, 'group': None}]
        line.save(update_fields=['waiting'])
        items = next_actions.actions_list(finals, creator)
        declined = [i for i in items if i['kind'] == 'declined']
        self.assertEqual(len(declined), 1)
        self.assertEqual(declined[0]['permission'], 'manage')
        self.assertEqual(declined[0]['detail']['count'], 2)
        self.assertEqual(declined[0]['stage']['id'], f_stage.id)
        self.assertEqual([i for i in next_actions.actions_list(q, creator)
                          if i['kind'] == 'declined'], [])

    def test_no_waiting_places_is_nothing(self):
        _q, _qs, finals, _fs, _line, creator = qualifier_and_finals()
        self.assertEqual(next_actions.actions_list(finals, creator), [])

    def test_a_finished_stage_with_an_unfired_preview_line_waits_to_advance(self):
        q, q_stage, _finals, _fs, line, creator = qualifier_and_finals()
        draw(q_stage, creator)
        self.assertNotIn('advance', kinds(next_actions.actions_list(q, creator)))
        play_out(q_stage)
        items = [i for i in next_actions.actions_list(q, creator) if i['kind'] == 'advance']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['permission'], 'place')
        self.assertEqual(items[0]['stage']['id'], q_stage.id)
        advance(q_stage, creator)
        self.assertNotIn('advance', kinds(next_actions.actions_list(q, creator)))

    def test_a_manual_line_is_a_pull_not_an_advance(self):
        q, q_stage, _finals, _fs, line, creator = qualifier_and_finals(fire='manual')
        draw(q_stage, creator)
        play_out(q_stage)
        got = kinds(next_actions.actions_list(q, creator))
        self.assertIn('pull', got)
        self.assertNotIn('advance', got)
        item = [i for i in next_actions.actions_list(q, creator) if i['kind'] == 'pull'][0]
        self.assertEqual(item['permission'], 'place')

    def test_a_pull_waits_for_its_source_to_finish(self):
        q, q_stage, _finals, _fs, _line, creator = qualifier_and_finals(fire='manual')
        draw(q_stage, creator)
        self.assertNotIn('pull', kinds(next_actions.actions_list(q, creator)))


class CheckInCloseTests(TestCase):
    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org)
        self.t.start_date_and_time = timezone.now() - timedelta(minutes=10)
        self.t.options = {'check_in_minutes': 30}
        self.t.save(update_fields=['start_date_and_time', 'options'])
        register(self.t, person())

    def kinds(self):
        return kinds(next_actions.actions_list(self.t, self.org))

    def test_check_in_over_by_the_clock_and_still_open_is_listed(self):
        items = next_actions.actions_list(self.t, self.org)
        self.assertEqual(kinds(items), ['close_check_in'])
        self.assertEqual(items[0]['permission'], 'manage')
        self.assertEqual(items[0]['since'], self.t.start_date_and_time)

    def test_a_window_still_running_is_not_listed(self):
        self.t.start_date_and_time = timezone.now() + timedelta(minutes=10)
        self.t.save(update_fields=['start_date_and_time'])
        self.assertEqual(self.kinds(), [])

    def test_closed_by_the_organiser_is_done(self):
        self.t.check_in_closed_at = timezone.now()
        self.t.save(update_fields=['check_in_closed_at'])
        self.assertEqual(self.kinds(), [])

    def test_a_drawn_bracket_means_nothing_left_to_close(self):
        a, b = person(), person()
        BracketMatch.objects.create(tournament=self.t, round_number=1, match_number=1,
                                    participant_1=register(self.t, a),
                                    participant_2=register(self.t, b))
        self.assertNotIn('close_check_in', self.kinds())

    def test_a_cancelled_tournament_is_not_listed(self):
        self.t.status = 'cancelled'
        self.t.save(update_fields=['status'])
        self.assertEqual(self.kinds(), [])

    def test_no_check_in_option_is_nothing(self):
        self.t.options = {'check_in_minutes': 0}
        self.t.save(update_fields=['options'])
        self.assertEqual(self.kinds(), [])


class WhoSeesWhatTests(MatchDeskBase):
    def setUp(self):
        super().setUp()
        TournamentDispute.objects.create(tournament=self.t, match=self.m,
                                         raised_by=self.ra.user, description='x')
        # No match is drawn, so check-in is still the organiser's to close.
        self.t.bracket_matches.all().delete()
        self.t.options = {'check_in_minutes': 30}
        self.t.start_date_and_time = timezone.now() - timedelta(hours=1)
        self.t.save(update_fields=['options', 'start_date_and_time'])
        self.stage = add_stage(self.t, 0, 'round_robin', advances=2)
        add_stage(self.t, 1, 'single_elimination')
        self.tied = standings_rows([('A', 1, False), ('B', 2, True), ('C', 2, True), ('D', 4, False)])

    def items(self, user):
        with mock.patch('vent_tournament.stage_engine.standings', return_value=self.tied), \
                mock.patch('vent_tournament.stage_engine.stage_finished', return_value=True):
            return next_actions.actions_list(self.t, user)

    def helper(self, permissions):
        user = person()
        TournamentStaff.objects.create(tournament=self.t, user=user, permissions=permissions)
        return user

    def test_the_organiser_sees_every_kind(self):
        got = set(kinds(self.items(self.org)))
        self.assertTrue({'dispute', 'tie', 'close_check_in'} <= got)

    def test_a_report_only_helper_sees_matches_and_never_the_draw_or_the_tournament(self):
        got = set(kinds(self.items(self.helper(['report']))))
        self.assertEqual(got, {'dispute'})

    def test_a_place_helper_sees_the_draw_and_not_disputes_or_check_in(self):
        got = set(kinds(self.items(self.helper(['place']))))
        self.assertEqual(got, {'tie', 'advance'})

    def test_a_player_in_the_tournament_sees_nothing(self):
        self.assertEqual(self.items(self.ra.user), [])

    def test_signed_out_sees_nothing(self):
        self.assertEqual(self.items(None), [])


class OrderTests(MatchDeskBase):
    def test_kinds_come_in_the_order_of_kinds_then_oldest_first(self):
        other = BracketMatch.objects.create(tournament=self.t, round_number=1, match_number=2,
                                            participant_1=register(self.t, person()),
                                            participant_2=register(self.t, person()),
                                            scheduled_at=timezone.now() - timedelta(hours=9))
        self.m.scheduled_at = timezone.now() - timedelta(hours=3)
        self.m.save(update_fields=['scheduled_at'])
        TournamentDispute.objects.create(tournament=self.t, match=self.m,
                                         raised_by=self.ra.user, description='x')
        items = self.items()
        self.assertEqual(kinds(items), ['dispute', 'late', 'late'])
        self.assertEqual([i['match']['id'] for i in items[1:]], [other.id, self.m.id])
