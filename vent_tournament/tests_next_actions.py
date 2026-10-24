"""What one viewer can do next on a tournament, with its deadline (P4a).

Design section 12: "Per viewer next actions: one source for every button and
countdown ("Check in by 14:00 WAT")." After Challengermode's `allowedActions`,
a translation key and a timer (research 2.5). Each test is one rule of
`next_actions.for_viewer`.
"""
import itertools
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import TeamMembers
from . import next_actions
from .models import BracketMatch, MatchScore, TournamentRegistration
from .tests import make_tournament, make_user, register
from .tests_aggregate import make_team

_N = itertools.count(4400000)


def person():
    return make_user(next(_N))


def kinds(actions):
    return [a['action'] for a in actions]


class RegisterTests(TestCase):
    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org)

    def test_signed_out_is_offered_register_needing_an_account(self):
        acts = next_actions.for_viewer(None, self.t)
        self.assertEqual(kinds(acts), ['register'])
        self.assertTrue(acts[0]['needs_account'])
        self.assertEqual(acts[0]['key'], 'next.register')
        self.assertEqual(acts[0]['deadline'], self.t.start_date_and_time)

    def test_signed_in_stranger_needs_no_account(self):
        acts = next_actions.for_viewer(person(), self.t)
        self.assertEqual(kinds(acts), ['register'])
        self.assertFalse(acts[0]['needs_account'])

    def test_the_close_date_is_the_deadline(self):
        closes = timezone.now() + timedelta(hours=2)
        self.t.registration_closes_at = closes
        self.t.save(update_fields=['registration_closes_at'])
        self.assertEqual(next_actions.for_viewer(person(), self.t)[0]['deadline'], closes)

    def test_not_open_yet_says_when_it_opens(self):
        opens = timezone.now() + timedelta(hours=3)
        self.t.registration_opens_at = opens
        self.t.save(update_fields=['registration_opens_at'])
        acts = next_actions.for_viewer(person(), self.t)
        self.assertEqual(kinds(acts), ['register'])
        self.assertEqual(acts[0]['opens_at'], opens)

    def test_nothing_after_the_close_date(self):
        self.t.registration_closes_at = timezone.now() - timedelta(minutes=1)
        self.t.save(update_fields=['registration_closes_at'])
        self.assertEqual(next_actions.for_viewer(person(), self.t), [])

    def test_nothing_when_full(self):
        self.t.player_size = 1
        self.t.save(update_fields=['player_size'])
        register(self.t, person())
        self.assertEqual(next_actions.for_viewer(person(), self.t), [])

    def test_nothing_once_live(self):
        self.t.status = 'live'
        self.t.save(update_fields=['status'])
        self.assertEqual(next_actions.for_viewer(person(), self.t), [])

    def test_nothing_on_a_draft(self):
        self.t.is_draft = True
        self.t.save(update_fields=['is_draft'])
        self.assertEqual(next_actions.for_viewer(person(), self.t), [])

    def test_an_entrant_is_not_offered_register(self):
        player = person()
        register(self.t, player)
        self.assertNotIn('register', kinds(next_actions.for_viewer(player, self.t)))

    def test_a_club_member_is_not_offered_register_for_their_entered_club(self):
        owner, member = person(), person()
        team = make_team('Club', owner)
        TeamMembers.objects.create(team=team, user=member)
        TournamentRegistration.objects.create(tournament=self.t, team=team, status='confirmed')
        self.assertEqual(next_actions.for_viewer(member, self.t), [])

    def test_the_organiser_is_never_offered_register(self):
        self.assertEqual(next_actions.for_viewer(self.org, self.t), [])


class CheckInTests(TestCase):
    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org)
        self.t.start_date_and_time = timezone.now() + timedelta(minutes=10)
        self.t.options = {'check_in_minutes': 30}
        self.t.save(update_fields=['start_date_and_time', 'options'])
        self.player = person()
        self.reg = register(self.t, self.player)

    def test_an_open_window_asks_for_check_in_by_its_close(self):
        acts = next_actions.for_viewer(self.player, self.t)
        self.assertEqual(kinds(acts), ['check_in'])
        self.assertEqual(acts[0]['deadline'], self.t.start_date_and_time)
        self.assertEqual(acts[0]['registration_id'], self.reg.id)

    def test_checked_in_is_nothing_to_do(self):
        self.reg.checked_in_at = timezone.now()
        self.reg.save(update_fields=['checked_in_at'])
        self.assertEqual(next_actions.for_viewer(self.player, self.t), [])

    def test_a_window_not_open_yet_says_when_it_opens(self):
        self.t.start_date_and_time = timezone.now() + timedelta(hours=2)
        self.t.save(update_fields=['start_date_and_time'])
        acts = next_actions.for_viewer(self.player, self.t)
        self.assertEqual(kinds(acts), ['check_in'])
        self.assertEqual(acts[0]['opens_at'], self.t.start_date_and_time - timedelta(minutes=30))

    def test_closed_by_the_organiser_is_nothing_to_do(self):
        self.t.check_in_closed_at = timezone.now()
        self.t.save(update_fields=['check_in_closed_at'])
        self.assertEqual(next_actions.for_viewer(self.player, self.t), [])

    def test_the_club_owner_checks_in_for_the_club_and_a_member_does_not(self):
        owner, member = person(), person()
        team = make_team('Club', owner)
        TeamMembers.objects.create(team=team, user=member)
        TournamentRegistration.objects.create(tournament=self.t, team=team, status='confirmed')
        self.assertEqual(kinds(next_actions.for_viewer(owner, self.t)), ['check_in'])
        self.assertEqual(next_actions.for_viewer(member, self.t), [])


class DeclineTests(TestCase):
    def test_a_qualifier_may_decline_until_the_window_ends(self):
        from .tests_feeds import advance, qualifier_and_finals
        from .tests_stage_engine import draw, play_out
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        draw(q_stage, creator)
        play_out(q_stage)
        advance(q_stage, creator)
        qualifier = finals.registrations.filter(qualified_via=line).order_by('source_place').first()
        viewer = qualifier.acting_user
        acts = [a for a in next_actions.for_viewer(viewer, finals) if a['action'] == 'decline_place']
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]['deadline'], qualifier.decline_until)
        later = qualifier.decline_until + timedelta(minutes=1)
        self.assertNotIn('decline_place', kinds(next_actions.for_viewer(viewer, finals, now=later)))


class MatchTests(TestCase):
    def setUp(self):
        self.org = person()
        self.t = make_tournament(self.org, status='live')
        self.a, self.b = person(), person()
        self.ra, self.rb = register(self.t, self.a), register(self.t, self.b)
        self.m = BracketMatch.objects.create(tournament=self.t, round_number=1, match_number=1,
                                             participant_1=self.ra, participant_2=self.rb)

    def test_an_open_match_asks_both_sides_to_report(self):
        for viewer, opponent in ((self.a, self.b), (self.b, self.a)):
            acts = next_actions.for_viewer(viewer, self.t)
            self.assertEqual(kinds(acts), ['report'])
            self.assertEqual(acts[0]['match']['id'], self.m.id)
            self.assertEqual(acts[0]['match']['opponent'], opponent.username)

    def test_organiser_only_results_ask_nobody_to_report(self):
        self.t.score_confirmation_mode = 'organizer_only'
        self.t.save(update_fields=['score_confirmation_mode'])
        self.assertEqual(next_actions.for_viewer(self.a, self.t), [])

    def test_a_report_asks_the_other_side_to_confirm(self):
        MatchScore.objects.create(match=self.m, submitted_by=self.a, score_p1=2, score_p2=0)
        self.m.status = 'pending_opponent_confirm'
        self.m.save(update_fields=['status'])
        self.assertEqual(kinds(next_actions.for_viewer(self.b, self.t)), ['confirm_score'])
        self.assertEqual(next_actions.for_viewer(self.a, self.t), [])

    def test_match_check_in_comes_before_reporting(self):
        deadline = timezone.now() + timedelta(minutes=10)
        self.m.check_in_deadline = deadline
        self.m.save(update_fields=['check_in_deadline'])
        acts = next_actions.for_viewer(self.a, self.t)
        self.assertEqual(kinds(acts), ['check_in_match'])
        self.assertEqual(acts[0]['deadline'], deadline)
        self.m.checked_in_p1_at = timezone.now()
        self.m.save(update_fields=['checked_in_p1_at'])
        self.assertEqual(kinds(next_actions.for_viewer(self.a, self.t)), ['report'])
        self.assertEqual(kinds(next_actions.for_viewer(self.b, self.t)), ['check_in_match'])

    def test_a_match_that_starts_later_is_a_countdown_to_its_start(self):
        start = timezone.now() + timedelta(hours=3)
        self.m.scheduled_at = start
        self.m.save(update_fields=['scheduled_at'])
        acts = next_actions.for_viewer(self.a, self.t)
        self.assertEqual(kinds(acts), ['play'])
        self.assertEqual(acts[0]['deadline'], start)

    def test_a_club_member_who_does_not_act_is_asked_nothing(self):
        owner, member, rival = person(), person(), person()
        team = make_team('Club', owner)
        TeamMembers.objects.create(team=team, user=member)
        club = TournamentRegistration.objects.create(tournament=self.t, team=team, status='confirmed')
        BracketMatch.objects.create(tournament=self.t, round_number=1, match_number=2,
                                    participant_1=club, participant_2=register(self.t, rival))
        self.assertEqual(next_actions.for_viewer(member, self.t), [])
        self.assertEqual(kinds(next_actions.for_viewer(owner, self.t)), ['report'])

    def test_only_a_squads_captain_is_asked_to_report(self):
        from .models import SquadMember, TournamentSquad
        captain, member = person(), person()
        squad = TournamentSquad.objects.create(tournament=self.t, name='Nigeria', tag='NGA',
                                               created_by=self.org)
        SquadMember.objects.create(squad=squad, user=captain, is_captain=True)
        SquadMember.objects.create(squad=squad, user=member)
        side = TournamentRegistration.objects.create(tournament=self.t, squad=squad, status='confirmed')
        BracketMatch.objects.create(tournament=self.t, round_number=1, match_number=2,
                                    participant_1=side, participant_2=register(self.t, person()))
        self.assertEqual(kinds(next_actions.for_viewer(captain, self.t)), ['report'])
        self.assertEqual(next_actions.for_viewer(member, self.t), [])

    def test_an_organiser_who_plays_is_asked_to_report_and_never_to_register(self):
        t = make_tournament(self.org)
        mine, rival = register(t, self.org), register(t, person())
        BracketMatch.objects.create(tournament=t, round_number=1, match_number=1,
                                    participant_1=mine, participant_2=rival)
        self.assertEqual(kinds(next_actions.for_viewer(self.org, t)), ['report'])

    def test_a_finished_match_or_an_empty_seat_asks_nothing(self):
        self.m.status = 'completed'
        self.m.save(update_fields=['status'])
        self.assertEqual(next_actions.for_viewer(self.a, self.t), [])
        BracketMatch.objects.create(tournament=self.t, round_number=2, match_number=1,
                                    participant_1=self.ra)
        self.assertEqual(next_actions.for_viewer(self.a, self.t), [])


class OrderTests(TestCase):
    def test_the_soonest_deadline_comes_first_and_no_deadline_last(self):
        org, a, b, c = person(), person(), person(), person()
        t = make_tournament(org, status='live')
        ra = register(t, a)
        now = timezone.now()
        later = BracketMatch.objects.create(tournament=t, round_number=1, match_number=1,
                                            participant_1=ra, participant_2=register(t, b),
                                            check_in_deadline=now + timedelta(minutes=40))
        sooner = BracketMatch.objects.create(tournament=t, round_number=1, match_number=2,
                                             participant_1=ra, participant_2=register(t, c),
                                             check_in_deadline=now + timedelta(minutes=5))
        open_ = BracketMatch.objects.create(tournament=t, round_number=1, match_number=3,
                                            participant_1=ra, participant_2=register(t, person()))
        acts = next_actions.for_viewer(a, t)
        self.assertEqual([x['match']['id'] for x in acts], [sooner.id, later.id, open_.id])
