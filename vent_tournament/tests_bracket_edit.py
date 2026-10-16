"""Editing a tournament and the brackets inside it after it has started.

CEO, 12 October 2026 (inbox 450): edit "the formats/brackets, seeding and all
that event after the event has started", and "apply different formats/brackets
to different stages of an already existiing bracket". 13 October (inbox 452):
"the bracket system and format has to be really fluid fr editing everything
inside."

Pinned here, through the real doors: the plan saves after a stage is drawn
(drawn stages keep what was played); a draw is undone, refusing to throw away
results unless told to; a stage changes format in one press; a bracket drawn as
one format becomes stage 1 with every match kept, and a stage can follow it;
inside a bracket two people swap, somebody replaces an entrant, a result comes
back off a match, a match's rules change; the general edit door no longer
relabels a drawn bracket; every change is in the history.
"""
from django.test import TestCase
from django.utils import timezone

from .models import BracketEdit, BracketMatch, TournamentDispute, TournamentStage
from .tests import client_for, gen_bracket, make_user, register
from .tests_stage_engine import add_stage, draw, field, play, play_out


def url(t, tail):
    return '/tournament/%s/%s' % (t.slug or t.tournament_id, tail)


class Base(TestCase):
    def two_stage(self, n=8, groups=2):
        t, creator, regs = field(n, bracket_type='round_robin')
        groups_stage = add_stage(t, 0, 'round_robin', advances=2, groups=groups)
        playoff = add_stage(t, 1, 'single_elimination')
        return t, creator, regs, groups_stage, playoff

    def plan_of(self, t):
        res = client_for(t.tournament_creator).get(url(t, 'stages/'))
        self.assertEqual(res.status_code, 200)
        return res.json()['data']['stages']

    def save_plan(self, t, rows, user=None):
        return client_for(user or t.tournament_creator).put(
            url(t, 'stages/set/'), {'stages': rows}, format='json')


class PlanAfterTheStartTests(Base):
    def test_the_plan_saves_after_a_stage_is_drawn(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        rows = self.plan_of(t)
        rows[0]['label'] = 'Group phase'
        rows[1]['format'] = 'double_elimination'
        rows[1]['label'] = 'Playoff'
        res = self.save_plan(t, rows)
        self.assertEqual(res.status_code, 200, res.content[:400])
        groups.refresh_from_db()
        self.assertEqual((groups.label, groups.format, groups.status), ('Group phase', 'round_robin', 'running'))
        self.assertEqual(TournamentStage.objects.get(tournament=t, order=1).format, 'double_elimination')
        # Nothing played was touched.
        self.assertEqual(groups.matches.count(), BracketMatch.objects.filter(stage=groups).count())
        self.assertTrue(BracketEdit.objects.filter(tournament=t, kind='plan').exists())

    def test_a_drawn_stage_keeps_its_format_until_its_draw_is_undone(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        rows = self.plan_of(t)
        rows[0]['format'] = 'swiss'
        res = self.save_plan(t, rows)
        self.assertEqual(res.status_code, 409)
        self.assertEqual((res.json()['code'], res.json()['field']), ('STAGE_DRAWN_FIELD', 'format'))

    def test_drawn_stages_stay_first_and_cannot_be_removed(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        rows = self.plan_of(t)
        res = self.save_plan(t, [rows[1], rows[0]])
        self.assertEqual(res.json()['code'], 'STAGE_ORDER_LOCKED')
        res = self.save_plan(t, [])
        self.assertEqual(res.json()['code'], 'DRAWN_STAGE_REMOVED')

    def test_a_stage_can_follow_the_one_that_was_last(self):
        t, creator, regs = field(8, bracket_type='round_robin')
        only = add_stage(t, 0, 'round_robin')
        draw(only, creator)
        rows = self.plan_of(t)
        rows[0]['advances'] = 4
        rows.append({'format': 'single_elimination', 'label': 'Final four'})
        res = self.save_plan(t, rows)
        self.assertEqual(res.status_code, 200, res.content[:400])
        play_out(only)
        only.refresh_from_db()
        res = client_for(creator).post(url(t, 'stages/%d/advance/' % only.id), {}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        final = TournamentStage.objects.get(tournament=t, order=1)
        self.assertEqual(len(final.entrants), 4)
        t.refresh_from_db()
        self.assertIsNone(t.completed_at)

    def test_a_finished_tournament_and_a_stranger_are_refused(self):
        t, creator, regs, groups, playoff = self.two_stage()
        stranger = make_user(990001)
        res = self.save_plan(t, self.plan_of(t), user=stranger)
        self.assertEqual(res.status_code, 403)
        t.status = 'completed'
        t.completed_at = timezone.now()
        t.save(update_fields=['status', 'completed_at'])
        res = self.save_plan(t, self.plan_of(t))
        self.assertEqual(res.json()['code'], 'TOURNAMENT_FINISHED')


class UndoTheDrawTests(Base):
    def test_undo_a_draw_with_nothing_played(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        res = client_for(creator).post(url(t, 'stages/%d/undraw/' % groups.id), {}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        groups.refresh_from_db()
        self.assertEqual((groups.status, groups.drawn_at, groups.matches.count()), ('pending', None, 0))
        t.refresh_from_db()
        self.assertEqual(t.status, 'registration_closed')

    def test_played_results_are_kept_unless_the_organiser_says_otherwise(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        play(groups.matches.filter(participant_2__isnull=False).first(), 2, 1)
        res = client_for(creator).post(url(t, 'stages/%d/undraw/' % groups.id), {}, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual((res.json()['code'], res.json()['detail']['played']), ('STAGE_HAS_RESULTS', 1))
        self.assertTrue(groups.matches.exists())
        res = client_for(creator).post(url(t, 'stages/%d/undraw/' % groups.id),
                                       {'discard_results': True}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['results_discarded'], 1)
        self.assertFalse(groups.matches.exists())

    def test_not_while_the_next_stage_is_drawn_or_a_dispute_is_open(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        match = groups.matches.filter(participant_2__isnull=False).first()
        TournamentDispute.objects.create(tournament=t, match=match, raised_by=creator,
                                         description='x', status='open')
        res = client_for(creator).post(url(t, 'stages/%d/undraw/' % groups.id), {}, format='json')
        self.assertEqual(res.json()['code'], 'DISPUTES_OPEN')
        TournamentDispute.objects.update(status='resolved')
        play_out(groups)
        res = client_for(creator).post(url(t, 'stages/%d/advance/' % groups.id), {}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        res = client_for(creator).post(url(t, 'stages/%d/undraw/' % groups.id),
                                       {'discard_results': True}, format='json')
        self.assertEqual(res.json()['code'], 'NEXT_STAGE_DRAWN')

    def test_a_closed_stage_opens_again_while_the_next_is_undrawn(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        play_out(groups)
        res = client_for(creator).post(url(t, 'stages/%d/advance/' % groups.id),
                                       {'draw_next': False}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        res = client_for(creator).post(url(t, 'stages/%d/reopen/' % groups.id), {}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        groups.refresh_from_db()
        self.assertEqual((groups.status, groups.advanced), ('running', []))


class ChangeTheFormatTests(Base):
    def test_a_drawn_stage_changes_format_in_one_press(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        res = client_for(creator).post(url(t, 'stages/%d/reformat/' % groups.id),
                                       {'format': 'swiss', 'groups': 0,
                                        'settings': {'rounds': 3}}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        groups.refresh_from_db()
        self.assertEqual((groups.format, groups.groups, groups.status), ('swiss', 0, 'running'))
        self.assertEqual(groups.matches.filter(round_number=1).count(), 4)
        self.assertTrue(BracketEdit.objects.filter(kind='reformat', detail__old='round_robin').exists())

    def test_manual_seeding_on_the_new_draw(self):
        t, creator, regs, groups, playoff = self.two_stage()
        order = [r.id for r in reversed(regs)]
        res = client_for(creator).post(url(t, 'stages/%d/reformat/' % groups.id),
                                       {'format': 'round_robin', 'groups': 2,
                                        'seed_strategy': 'manual_order', 'manual_order': order},
                                       format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        groups.refresh_from_db()
        self.assertEqual(groups.entrants[0], regs[-1].id)

    def test_results_and_the_plan_still_decide(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        play(groups.matches.filter(participant_2__isnull=False).first(), 1, 0)
        res = client_for(creator).post(url(t, 'stages/%d/reformat/' % groups.id),
                                       {'format': 'swiss'}, format='json')
        self.assertEqual(res.json()['code'], 'STAGE_HAS_RESULTS')
        # A knockout feeds nothing, so it cannot come before the playoff.
        res = client_for(creator).post(url(t, 'stages/%d/reformat/' % groups.id),
                                       {'format': 'single_elimination', 'discard_results': True},
                                       format='json')
        self.assertEqual(res.status_code, 400)
        # Its own code now, with the stage it is about, so the screen can say
        # it in the reader's language (it was VALIDATION_FAILED and English).
        self.assertEqual(res.json()['code'], 'FORMAT_FEEDS_NOTHING')
        self.assertEqual(res.json()['detail']['stage'], groups.label)
        groups.refresh_from_db()
        self.assertEqual(groups.format, 'round_robin')
        self.assertTrue(groups.matches.exists(), 'a refused change must leave the draw as it was')


class OneFormatBracketTests(Base):
    def test_a_knockout_becomes_stage_one_with_every_match_kept(self):
        t, creator, regs = field(8)
        gen_bracket(t, creator)
        first = t.bracket_matches.filter(round_number=1, participant_2__isnull=False).first()
        play(first, 2, 0)
        before = set(t.bracket_matches.values_list('id', 'participant_1_id', 'participant_2_id',
                                                   'winner_id', 'score_p1'))
        res = client_for(creator).post(url(t, 'stages/adopt/'), {}, format='json')
        self.assertEqual(res.status_code, 201, res.content[:400])
        stage = TournamentStage.objects.get(tournament=t)
        self.assertEqual((stage.format, stage.status, len(stage.entrants)), ('single_elimination', 'running', 8))
        self.assertFalse(t.bracket_matches.filter(stage__isnull=True).exists())
        after = set(t.bracket_matches.values_list('id', 'participant_1_id', 'participant_2_id',
                                                  'winner_id', 'score_p1'))
        self.assertEqual(before, after)
        res = client_for(creator).post(url(t, 'stages/adopt/'), {}, format='json')
        self.assertEqual(res.json()['code'], 'ALREADY_IN_STAGES')

    def test_a_table_bracket_gains_a_playoff_after_it(self):
        t, creator, regs = field(6, bracket_type='round_robin')
        gen_bracket(t, creator)
        self.assertEqual(client_for(creator).post(url(t, 'stages/adopt/'), {}, format='json').status_code, 201)
        rows = self.plan_of(t)
        rows[0]['advances'] = 4
        rows.append({'format': 'single_elimination', 'label': 'Playoff'})
        res = self.save_plan(t, rows)
        self.assertEqual(res.status_code, 200, res.content[:400])
        stage = TournamentStage.objects.get(tournament=t, order=0)
        play_out(stage)
        t.refresh_from_db()
        self.assertIsNone(t.completed_at, 'the group phase must not end the tournament now')
        res = client_for(creator).post(url(t, 'stages/%d/advance/' % stage.id), {}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.assertEqual(TournamentStage.objects.get(tournament=t, order=1).matches.count(), 3)

    def test_nothing_to_adopt_before_the_draw(self):
        t, creator, regs = field(4)
        res = client_for(creator).post(url(t, 'stages/adopt/'), {}, format='json')
        self.assertEqual(res.json()['code'], 'NO_BRACKET_YET')

    def test_the_edit_door_no_longer_relabels_a_drawn_bracket(self):
        t, creator, regs = field(8)
        gen_bracket(t, creator)
        res = client_for(creator).put('/tournament/edit-tournament/%s/' % t.tournament_id,
                                       {'bracket_type': 'Double Elimination'}, format='json')
        self.assertEqual(res.status_code, 409, res.content[:300])
        self.assertEqual(res.json()['code'], 'BRACKET_FORMAT_LOCKED')
        t.refresh_from_db()
        self.assertEqual(t.bracket_type, 'single_elimination')


class InsideTheBracketTests(Base):
    def knockout(self, n=8):
        t, creator, regs = field(n)
        stage = add_stage(t, 0, 'single_elimination')
        draw(stage, creator)
        return t, creator, regs, stage

    def seat_of(self, stage, reg):
        m = stage.matches.filter(round_number=1).filter(participant_1=reg).first() or \
            stage.matches.filter(round_number=1).filter(participant_2=reg).first()
        return m

    def test_two_entrants_swap_seats(self):
        t, creator, regs, stage = self.knockout()
        a, b = regs[0], regs[1]
        ma, mb = self.seat_of(stage, a), self.seat_of(stage, b)
        self.assertNotEqual(ma.id, mb.id)
        seed_a, seed_b = stage.entrants.index(a.id), stage.entrants.index(b.id)
        res = client_for(creator).post(url(t, 'bracket/swap/'),
                                       {'a': a.id, 'b': b.id, 'stage_id': stage.id}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(self.seat_of(stage, a).id, mb.id)
        self.assertEqual(self.seat_of(stage, b).id, ma.id)
        stage.refresh_from_db()
        # Their seeds trade too, so the draw still explains itself.
        self.assertEqual((stage.entrants.index(b.id), stage.entrants.index(a.id)), (seed_a, seed_b))

    def test_a_bye_moves_with_the_person(self):
        t, creator, regs, stage = self.knockout(6)
        bye = stage.matches.filter(status='bye').first()
        lucky = bye.winner
        other = next(r for r in regs if r.id != lucky.id and
                     stage.matches.filter(round_number=1, status='scheduled', participant_1=r).exists())
        res = client_for(creator).post(url(t, 'bracket/swap/'),
                                       {'a': lucky.id, 'b': other.id, 'stage_id': stage.id}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        bye.refresh_from_db()
        self.assertEqual(bye.winner_id, other.id)
        nxt = BracketMatch.objects.get(pk=bye.winner_to_match_id)
        self.assertIn(other.id, (nxt.participant_1_id, nxt.participant_2_id))
        self.assertNotIn(lucky.id, (nxt.participant_1_id, nxt.participant_2_id))

    def test_people_in_groups_swap_every_fixture(self):
        t, creator, regs, groups, playoff = self.two_stage()
        draw(groups, creator)
        g1 = groups.matches.filter(group_number=1).first()
        g2 = groups.matches.filter(group_number=2).first()
        a, b = g1.participant_1_id, g2.participant_1_id
        res = client_for(creator).post(url(t, 'bracket/swap/'),
                                       {'a': a, 'b': b, 'stage_id': groups.id}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        from django.db.models import Q
        self.assertFalse(groups.matches.filter(group_number=1).filter(
            Q(participant_1_id=a) | Q(participant_2_id=a)).exists())
        self.assertFalse(groups.matches.filter(participant_1_id=a, participant_2_id=a).exists())

    def test_not_once_one_of_them_has_played(self):
        t, creator, regs, stage = self.knockout()
        a, b = regs[0], regs[1]
        play(self.seat_of(stage, a), 2, 0)
        res = client_for(creator).post(url(t, 'bracket/swap/'),
                                       {'a': a.id, 'b': b.id, 'stage_id': stage.id}, format='json')
        self.assertEqual(res.json()['code'], 'ENTRANT_HAS_PLAYED')

    def test_somebody_takes_an_entrants_place(self):
        t, creator, regs, stage = self.knockout()
        late = register(t, make_user(990101))
        out = regs[2]
        res = client_for(creator).post(url(t, 'bracket/replace/'),
                                       {'out': out.id, 'in': late.id, 'stage_id': stage.id}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertIsNotNone(self.seat_of(stage, late))
        self.assertIsNone(self.seat_of(stage, out))
        res = client_for(creator).post(url(t, 'bracket/replace/'),
                                       {'out': regs[3].id, 'in': late.id, 'stage_id': stage.id}, format='json')
        self.assertEqual(res.json()['code'], 'ALREADY_IN_THIS_BRACKET')

    def test_a_result_comes_back_off_and_the_winner_comes_back_out(self):
        t, creator, regs, stage = self.knockout()
        m = stage.matches.filter(round_number=1, participant_2__isnull=False).first()
        play(m, 2, 0)
        nxt = BracketMatch.objects.get(pk=m.winner_to_match_id)
        self.assertIn(m.winner_id, (nxt.participant_1_id, nxt.participant_2_id))
        loser = m.participant_2_id
        res = client_for(creator).post(url(t, 'matches/%d/reopen/' % m.id), {}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        m.refresh_from_db()
        nxt.refresh_from_db()
        self.assertEqual((m.status, m.winner_id), ('scheduled', None))
        self.assertNotIn(m.participant_1_id, (nxt.participant_1_id, nxt.participant_2_id))
        play(m, 0, 3)
        nxt.refresh_from_db()
        self.assertIn(loser, (nxt.participant_1_id, nxt.participant_2_id))

    def test_the_later_match_comes_back_off_first(self):
        t, creator, regs, stage = self.knockout(4)
        semis = list(stage.matches.filter(round_number=1))
        for s in semis:
            play(s, 2, 0)
        final = stage.matches.get(round_number=2)
        play(final, 1, 0)
        t.refresh_from_db()
        if t.completed_at:
            # A finished tournament is history; reopen the stage tools stop here.
            res = client_for(creator).post(url(t, 'matches/%d/reopen/' % semis[0].id), {}, format='json')
            self.assertEqual(res.json()['code'], 'TOURNAMENT_FINISHED')
            return
        res = client_for(creator).post(url(t, 'matches/%d/reopen/' % semis[0].id), {}, format='json')
        self.assertEqual(res.json()['code'], 'NEXT_MATCH_ALREADY_PLAYED')

    def test_a_match_plays_differently_after_the_draw(self):
        t, creator, regs, stage = self.knockout()
        m = stage.matches.filter(round_number=1, participant_2__isnull=False).first()
        res = client_for(creator).post(url(t, 'matches/%d/rules/' % m.id),
                                       {'best_of': 3, 'whole_round': True}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(set(stage.matches.filter(round_number=1, status='scheduled')
                             .values_list('best_of', flat=True)), {3})
        res = client_for(creator).post(url(t, 'matches/%d/rules/' % m.id),
                                       {'draw_allowed': True}, format='json')
        self.assertEqual(res.json()['code'], 'KNOCKOUT_NEEDS_A_WINNER')
        play(m, 2, 1)
        res = client_for(creator).post(url(t, 'matches/%d/rules/' % m.id), {'best_of': 5}, format='json')
        self.assertEqual(res.json()['code'], 'MATCH_ALREADY_PLAYED')

    def test_the_history_says_who_did_what(self):
        t, creator, regs, stage = self.knockout()
        client_for(creator).post(url(t, 'bracket/swap/'),
                                 {'a': regs[0].id, 'b': regs[1].id, 'stage_id': stage.id}, format='json')
        res = client_for(creator).get(url(t, 'bracket/edits/'))
        self.assertEqual(res.status_code, 200)
        row = res.json()['data']['edits'][0]
        self.assertEqual((row['kind'], row['by']), ('swap', creator.username))
        self.assertEqual(client_for(make_user(990201)).get(url(t, 'bracket/edits/')).status_code, 403)
