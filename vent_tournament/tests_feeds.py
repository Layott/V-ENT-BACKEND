"""Places pulled from one tournament into another (P2a, design section 7).

CEO, 16 October 2026 (inbox 470): connect brackets across tournaments and
events "so far they have access to those events". One `Progression` row, its
source stage in another tournament; firing it registers the qualifiers in the
target. Decisions accepted by silence: fee waived by default, a 48 hour decline
window, automatic pull, same game only.
"""
import itertools

from django.db import transaction
from django.test import TestCase

from . import bracket_edit, feeds, progressions, stage_engine, stage_settings
from .models import Progression, TournamentRegistration
from .tests import client_for, make_tournament, make_user, register
from .tests_stage_engine import add_stage, draw, field, play_out

# The finals' own entrants, numbered from a counter of their own. They were
# 90000 + q.pk * 10 + i, which lands on the fixed users below (92001, 92003)
# whenever the qualifier's key is 200. (The duplicate email seen on 17 October
# came from field() in tests_stage_engine; this was the second way to it.)
_OWN_ENTRANTS = itertools.count(950000)


def advance(stage, user):
    with transaction.atomic():
        return stage_engine.advance(stage, user)


def qualifier_and_finals(n=4, finals_own=2, fire='after_preview'):
    """A qualifier (one round robin of `n`) feeding its top two into a finals
    tournament whose knockout also seats `finals_own` registrations of its own.
    One organiser runs both."""
    q, creator, q_regs = field(n, 'round_robin')
    q_stage = add_stage(q, 0, 'round_robin', label='League')
    finals = make_tournament(creator, bracket_type='single_elimination')
    own = [register(finals, make_user(next(_OWN_ENTRANTS))) for _ in range(finals_own)]
    for i, r in enumerate(own, start=1):
        r.seed = i
        r.save(update_fields=['seed'])
    f_stage = add_stage(finals, 0, 'single_elimination', label='Finals', direct=[r.id for r in own])
    line = feeds.create(f_stage, q_stage, creator, place_from=1, place_to=2, fire=fire)
    return q, q_stage, finals, f_stage, line, creator


class CreateTests(TestCase):
    def test_a_manager_of_both_creates_a_feed(self):
        q, q_stage, finals, f_stage, line, _c = qualifier_and_finals()
        self.assertEqual((line.source_id, line.target_id), (q_stage.id, f_stage.id))
        self.assertEqual(line.decline_hours, 48)
        self.assertEqual(line.on_decline, 'next')

    def refused(self, code, **kw):
        with self.assertRaises(feeds.FeedError) as caught:
            feeds.create(**kw)
        self.assertEqual(caught.exception.code, code)

    def test_not_a_manager_of_the_source(self):
        q, _creator, _regs = field(4, 'round_robin')
        q_stage = add_stage(q, 0, 'round_robin', label='League')
        other = make_user(91001)
        finals = make_tournament(other)
        f_stage = add_stage(finals, 0, 'single_elimination', label='Finals')
        self.refused('FEED_NOT_MANAGER', target_stage=f_stage, source_stage=q_stage, user=other,
                     place_from=1, place_to=2)

    def test_same_game_only(self):
        from vent_auth.models import Games
        q, creator, _regs = field(4, 'round_robin')
        q.tournament_game = Games.objects.create(game_title='Game One')
        q.save(update_fields=['tournament_game'])
        q_stage = add_stage(q, 0, 'round_robin', label='League')
        finals = make_tournament(creator)
        finals.tournament_game = Games.objects.create(game_title='Game Two')
        finals.save(update_fields=['tournament_game'])
        f_stage = add_stage(finals, 0, 'single_elimination', label='Finals')
        self.refused('FEED_GAME_MISMATCH', target_stage=f_stage, source_stage=q_stage, user=creator,
                     place_from=1, place_to=2)

    def test_team_to_team_player_to_player(self):
        q, creator, _regs = field(4, 'round_robin')
        q_stage = add_stage(q, 0, 'round_robin', label='League')
        finals = make_tournament(creator)
        finals.tournament_access = 'team'
        finals.team_size = 5
        finals.save(update_fields=['tournament_access', 'team_size'])
        f_stage = add_stage(finals, 0, 'single_elimination', label='Finals')
        self.refused('FEED_ENTRY_TYPE_MISMATCH', target_stage=f_stage, source_stage=q_stage,
                     user=creator, place_from=1, place_to=2)

    def test_the_target_must_have_room(self):
        q, creator, _regs = field(4, 'round_robin')
        q_stage = add_stage(q, 0, 'round_robin', label='League')
        finals = make_tournament(creator)
        finals.max_number_of_teams = 3
        finals.save(update_fields=['max_number_of_teams'])
        for i in range(2):
            register(finals, make_user(91100 + i))
        f_stage = add_stage(finals, 0, 'single_elimination', label='Finals')
        self.refused('FEED_TARGET_FULL', target_stage=f_stage, source_stage=q_stage, user=creator,
                     place_from=1, place_to=2)

    def test_no_cycle_across_tournaments(self):
        q, q_stage, finals, f_stage, _line, creator = qualifier_and_finals()
        self.refused('FEED_CYCLE', target_stage=q_stage, source_stage=f_stage, user=creator,
                     place_from=1, place_to=1)

    def test_a_feed_is_never_inside_one_tournament(self):
        t, creator, _regs = field(4, 'round_robin')
        a = add_stage(t, 0, 'round_robin', label='A')
        b = add_stage(t, 1, 'single_elimination', label='B')
        self.refused('FEED_SAME_TOURNAMENT', target_stage=b, source_stage=a, user=creator,
                     place_from=1, place_to=2)


class QualifyTests(TestCase):
    def test_firing_registers_the_qualifiers_in_the_target(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        draw(q_stage, creator)
        play_out(q_stage)
        with transaction.atomic():
            progressions.pull(Progression.objects.get(pk=line.pk), creator)
        line.refresh_from_db()
        top = [r for r in stage_engine.standings(q_stage) if r['rank'] <= 2]
        made = list(TournamentRegistration.objects.filter(tournament=finals, qualified_via=line)
                    .order_by('source_place'))
        self.assertEqual(len(made), 2)
        self.assertEqual([m.source_place for m in made], [1, 2])
        source_users = [TournamentRegistration.objects.get(pk=r['registration_id']).user_id for r in top]
        self.assertEqual([m.user_id for m in made], source_users)
        self.assertTrue(all(m.status == 'confirmed' and m.entry_fee_paid for m in made))
        self.assertTrue(all(m.decline_until is not None for m in made))
        # The line's sent rows carry the TARGET registrations, so every reader
        # of `sent` (the engine, early seats, the Path view) works unchanged.
        self.assertEqual({r['registration_id'] for r in line.sent}, {m.id for m in made})
        self.assertEqual({r['source_registration_id'] for r in line.sent}, {r['registration_id'] for r in top})

    def test_the_target_is_drawn_from_its_own_and_the_qualifiers(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        draw(q_stage, creator)
        play_out(q_stage)
        with transaction.atomic():
            progressions.pull(Progression.objects.get(pk=line.pk), creator)
        f_stage.refresh_from_db()
        self.assertIsNotNone(f_stage.drawn_at)
        seated = {p for m in f_stage.matches.filter(round_number=1)
                  for p in (m.participant_1_id, m.participant_2_id) if p}
        self.assertEqual(len(seated), 4)
        self.assertTrue(TournamentRegistration.objects.filter(pk__in=seated, tournament=finals)
                        .count() == 4)

    def test_somebody_already_in_the_target_is_not_registered_twice(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        draw(q_stage, creator)
        play_out(q_stage)
        winner = TournamentRegistration.objects.get(
            pk=[r for r in stage_engine.standings(q_stage) if r['rank'] == 1][0]['registration_id'])
        mine = register(finals, winner.user)
        with transaction.atomic():
            progressions.pull(Progression.objects.get(pk=line.pk), creator)
        mine.refresh_from_db()
        self.assertEqual(mine.qualified_via_id, line.id)
        self.assertEqual(mine.source_place, 1)
        self.assertEqual(TournamentRegistration.objects.filter(tournament=finals, user=winner.user).count(), 1)

    def test_each_qualifier_is_told(self):
        from vent_auth.models import Notification
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        draw(q_stage, creator)
        play_out(q_stage)
        with transaction.atomic():
            progressions.pull(Progression.objects.get(pk=line.pk), creator)
        made = TournamentRegistration.objects.filter(tournament=finals, qualified_via=line)
        for reg in made:
            self.assertTrue(Notification.objects.filter(user=reg.user, link__contains=finals.slug).exists())

    def test_an_on_close_feed_sends_when_the_qualifier_finishes(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals(fire='on_close')
        draw(q_stage, creator)
        play_out(q_stage)
        q.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(q.status, 'completed')
        self.assertIsNotNone(line.fired_at)
        self.assertEqual(TournamentRegistration.objects.filter(tournament=finals, qualified_via=line).count(), 2)


class OwnPlanTests(TestCase):
    def test_saving_the_qualifier_plan_keeps_the_feed(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        with transaction.atomic():
            bracket_edit.save_plan(q, [{'id': q_stage.id, 'format': 'round_robin', 'label': 'League'}],
                                   creator)
        self.assertTrue(Progression.objects.filter(pk=line.pk).exists())

    def test_removing_a_stage_that_feeds_another_tournament_is_refused(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        with self.assertRaises(bracket_edit.EditError) as caught:
            with transaction.atomic():
                bracket_edit.save_plan(q, [{'format': 'single_elimination', 'label': 'Cup'}], creator)
        self.assertEqual(caught.exception.code, 'STAGE_HAS_FEED')
        self.assertTrue(Progression.objects.filter(pk=line.pk).exists())

    def test_a_feed_is_not_the_qualifiers_next_stage(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        self.assertIsNone(stage_engine.next_stage(q_stage))
        self.assertIsNone(stage_engine.previous_stage(f_stage))

    def test_the_public_plan_of_each_side_lists_only_its_own_lines(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        self.assertEqual(list(progressions.lines_of(q)), [])
        self.assertEqual(list(progressions.lines_of(finals)), [])


class EndpointTests(TestCase):
    def test_list_create_and_delete(self):
        q, creator, _regs = field(4, 'round_robin')
        q_stage = add_stage(q, 0, 'round_robin', label='League')
        finals = make_tournament(creator)
        f_stage = add_stage(finals, 0, 'single_elimination', label='Finals')
        api = client_for(creator)
        res = api.post('/tournament/%s/feeds/' % finals.slug, {
            'target_stage': f_stage.id, 'source_tournament': q.slug, 'source_stage': q_stage.id,
            'place_from': 1, 'place_to': 2}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        feed_id = res.json()['data']['feed']['id']
        public = client_for(make_user(91201)).get('/tournament/%s/feeds/' % finals.slug).json()['data']
        self.assertEqual([f['id'] for f in public['incoming']], [feed_id])
        self.assertEqual(public['incoming'][0]['source']['tournament']['slug'], q.slug)
        out = client_for(make_user(91202)).get('/tournament/%s/feeds/' % q.slug).json()['data']
        self.assertEqual([f['id'] for f in out['outgoing']], [feed_id])
        self.assertEqual(api.delete('/tournament/%s/feeds/%s/' % (finals.slug, feed_id)).status_code, 200)
        self.assertFalse(Progression.objects.filter(pk=feed_id).exists())

    def test_a_stranger_cannot_create_or_delete(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        stranger = client_for(make_user(91301))
        res = stranger.post('/tournament/%s/feeds/' % finals.slug, {
            'target_stage': f_stage.id, 'source_tournament': q.slug, 'source_stage': q_stage.id,
            'place_from': 3, 'place_to': 3}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['code'], 'NOT_YOURS')
        self.assertEqual(stranger.delete('/tournament/%s/feeds/%s/' % (finals.slug, line.id)).status_code, 403)

    def test_a_feed_that_sent_cannot_be_deleted(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        draw(q_stage, creator)
        play_out(q_stage)
        with transaction.atomic():
            progressions.pull(Progression.objects.get(pk=line.pk), creator)
        res = client_for(creator).delete('/tournament/%s/feeds/%s/' % (finals.slug, line.id))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'FEED_ALREADY_SENT')


class UndoTests(TestCase):
    def test_clearing_a_fed_line_withdraws_who_it_registered_and_keeps_who_registered_themselves(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        # A second feed keeps the finals from drawing when the first sends.
        q2, _c2, _r2 = field(4, 'round_robin')
        q2.tournament_creator = creator
        q2.save(update_fields=['tournament_creator'])
        q2_stage = add_stage(q2, 0, 'round_robin', label='League two')
        feeds.create(f_stage, q2_stage, creator, place_from=1, place_to=1)
        draw(q_stage, creator)
        play_out(q_stage)
        top = [r for r in stage_engine.standings(q_stage) if r['rank'] <= 2]
        second = TournamentRegistration.objects.get(pk=top[1]['registration_id'])
        theirs = register(finals, second.user)
        with transaction.atomic():
            progressions.pull(Progression.objects.get(pk=line.pk), creator)
        f_stage.refresh_from_db()
        self.assertIsNone(f_stage.drawn_at)
        made = TournamentRegistration.objects.filter(tournament=finals, qualified_via=line).exclude(pk=theirs.pk).get()
        with transaction.atomic():
            progressions.clear(q_stage)
        made.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(made.status, 'withdrawn')
        self.assertEqual(theirs.status, 'confirmed')
        self.assertIsNone(theirs.qualified_via_id)


def fired_pair(on_decline='next', decline_hours=48, block_draw=True):
    """A qualifier of 6 feeding its top two into the finals; when `block_draw`,
    a second, unfired feed keeps the finals from drawing."""
    q, q_stage, finals, f_stage, line, creator = qualifier_and_finals(n=6)
    Progression.objects.filter(pk=line.pk).update(on_decline=on_decline, decline_hours=decline_hours)
    if block_draw:
        q2, _c, _r = field(4, 'round_robin')
        q2.tournament_creator = creator
        q2.save(update_fields=['tournament_creator'])
        q2_stage = add_stage(q2, 0, 'round_robin', label='League two')
        feeds.create(f_stage, q2_stage, creator, place_from=1, place_to=1)
    draw(q_stage, creator)
    play_out(q_stage)
    with transaction.atomic():
        progressions.pull(Progression.objects.get(pk=line.pk), creator)
    line.refresh_from_db()
    return q, q_stage, finals, f_stage, line, creator


def qualifier_reg(line, place):
    return TournamentRegistration.objects.get(qualified_via=line, source_place=place,
                                              status__in=('confirmed', 'pending'))


def ranked(q_stage, place):
    return TournamentRegistration.objects.get(
        pk=[r for r in stage_engine.standings(q_stage) if r['rank'] == place][0]['registration_id'])


def match_of(stage, reg):
    return (stage.matches.filter(participant_1=reg).first()
            or stage.matches.filter(participant_2=reg).first())


def win(match, reg):
    from .tests_stage_engine import play
    if match.participant_1_id == reg.id:
        play(match, 2, 0)
    else:
        play(match, 0, 2)


class DeclineTests(TestCase):
    def test_a_qualifier_declines_and_the_next_placement_takes_the_place(self):
        q, q_stage, finals, f_stage, line, creator = fired_pair()
        second = qualifier_reg(line, 2)
        with transaction.atomic():
            feeds.decline(second, second.user)
        second.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(second.status, 'declined')
        third = qualifier_reg(line, 3)
        self.assertEqual(third.user_id, ranked(q_stage, 3).user_id)
        self.assertEqual([r['registration_id'] for r in line.sent][1], third.id)
        self.assertNotIn(second.id, [r['registration_id'] for r in line.sent])

    def test_only_the_qualifier_may_decline(self):
        q, q_stage, finals, f_stage, line, creator = fired_pair()
        second = qualifier_reg(line, 2)
        with self.assertRaises(feeds.FeedError) as caught:
            feeds.decline(second, make_user(92001))
        self.assertEqual(caught.exception.code, 'NOT_YOURS')

    def test_after_the_window_the_place_is_kept(self):
        from datetime import timedelta
        from django.utils import timezone
        q, q_stage, finals, f_stage, line, creator = fired_pair()
        second = qualifier_reg(line, 2)
        TournamentRegistration.objects.filter(pk=second.pk).update(
            decline_until=timezone.now() - timedelta(minutes=1))
        second.refresh_from_db()
        with self.assertRaises(feeds.FeedError) as caught:
            feeds.decline(second, second.user)
        self.assertEqual(caught.exception.code, 'DECLINE_CLOSED')

    def test_no_window_means_no_decline(self):
        q, q_stage, finals, f_stage, line, creator = fired_pair(decline_hours=None)
        second = qualifier_reg(line, 2)
        with self.assertRaises(feeds.FeedError) as caught:
            feeds.decline(second, second.user)
        self.assertEqual(caught.exception.code, 'DECLINE_CLOSED')

    def test_the_organiser_picks_who_takes_a_declined_place(self):
        q, q_stage, finals, f_stage, line, creator = fired_pair(on_decline='organiser')
        second = qualifier_reg(line, 2)
        with transaction.atomic():
            feeds.decline(second, second.user)
        line.refresh_from_db()
        self.assertEqual(len(line.waiting), 1)
        self.assertFalse(progressions.ready(f_stage))
        fifth = ranked(q_stage, 5)
        with self.assertRaises(feeds.FeedError) as caught:
            feeds.pick(line, fifth.id, make_user(92002))
        self.assertEqual(caught.exception.code, 'FEED_NOT_MANAGER')
        with transaction.atomic():
            feeds.pick(line, fifth.id, creator)
        line.refresh_from_db()
        self.assertEqual(line.waiting, [])
        picked = TournamentRegistration.objects.get(qualified_via=line, user=fifth.user)
        self.assertIn(picked.id, [r['registration_id'] for r in line.sent])

    def test_a_pick_must_come_from_the_source_stage(self):
        q, q_stage, finals, f_stage, line, creator = fired_pair(on_decline='organiser')
        second = qualifier_reg(line, 2)
        with transaction.atomic():
            feeds.decline(second, second.user)
        stranger_reg = register(finals, make_user(92003))
        with self.assertRaises(feeds.FeedError) as caught:
            feeds.pick(Progression.objects.get(pk=line.pk), stranger_reg.id, creator)
        self.assertEqual(caught.exception.code, 'NOT_IN_THIS_STAGE')

    def test_after_the_draw_the_next_placement_takes_the_seat(self):
        q, q_stage, finals, f_stage, line, creator = fired_pair(block_draw=False)
        f_stage.refresh_from_db()
        self.assertIsNotNone(f_stage.drawn_at)
        second = qualifier_reg(line, 2)
        with transaction.atomic():
            feeds.decline(second, second.user)
        third = qualifier_reg(line, 3)
        seated = {p for m in f_stage.matches.all() for p in (m.participant_1_id, m.participant_2_id) if p}
        self.assertIn(third.id, seated)
        self.assertNotIn(second.id, seated)


class RecallTests(TestCase):
    def drawn(self):
        q, q_stage, finals, f_stage, line, creator = fired_pair(block_draw=False)
        return q, q_stage, finals, f_stage, line, creator, qualifier_reg(line, 1)

    def test_recall_preview_names_the_matches(self):
        q, q_stage, finals, f_stage, line, creator, first = self.drawn()
        preview = feeds.recall_preview(first)
        self.assertEqual(len(preview['drawn']), 1)
        self.assertEqual(preview['played'], [])
        self.assertEqual(preview['next']['source_place'], 3)

    def test_recall_hands_the_place_on_and_keeps_results(self):
        from .models import BracketEdit
        q, q_stage, finals, f_stage, line, creator, first = self.drawn()
        m = match_of(f_stage, first)
        win(m, first)
        with transaction.atomic():
            feeds.recall(first, creator, mode='hand_on')
        first.refresh_from_db()
        m.refresh_from_db()
        self.assertEqual(first.status, 'withdrawn')
        self.assertEqual(m.status, 'completed')
        third = qualifier_reg(line, 3)
        final = f_stage.matches.get(round_number=2)
        self.assertIn(third.id, (final.participant_1_id, final.participant_2_id))
        self.assertTrue(BracketEdit.objects.filter(tournament=finals, kind='recall').exists())

    def test_recall_can_undo_the_results(self):
        q, q_stage, finals, f_stage, line, creator, first = self.drawn()
        m = match_of(f_stage, first)
        win(m, first)
        with transaction.atomic():
            feeds.recall(first, creator, mode='undo')
        m.refresh_from_db()
        third = qualifier_reg(line, 3)
        self.assertEqual(m.status, 'scheduled')
        self.assertIn(third.id, (m.participant_1_id, m.participant_2_id))

    def test_only_a_manager_recalls(self):
        q, q_stage, finals, f_stage, line, creator, first = self.drawn()
        with self.assertRaises(feeds.FeedError) as caught:
            feeds.recall(first, make_user(92004), mode='hand_on')
        self.assertEqual(caught.exception.code, 'NOT_YOURS')


class QualifierEndpointTests(TestCase):
    def test_decline_pick_and_recall_doors(self):
        q, q_stage, finals, f_stage, line, creator = fired_pair(on_decline='organiser')
        second = qualifier_reg(line, 2)
        base = '/tournament/%s/' % finals.slug
        stranger = client_for(make_user(92101))
        self.assertEqual(stranger.post(base + 'qualifiers/%s/decline/' % second.id).status_code, 403)
        res = client_for(second.user).post(base + 'qualifiers/%s/decline/' % second.id)
        self.assertEqual(res.status_code, 200, res.content)
        fifth = ranked(q_stage, 5)
        res = client_for(creator).post(base + 'feeds/%s/pick/' % line.id,
                                       {'source_registration': fifth.id}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        first = qualifier_reg(line, 1)
        res = client_for(creator).get(base + 'qualifiers/%s/recall/' % first.id)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn('drawn', res.json()['data'])
        self.assertEqual(stranger.get(base + 'qualifiers/%s/recall/' % first.id).status_code, 403)


class PlaceholderTests(TestCase):
    def test_a_seat_waiting_on_a_feed_names_the_other_tournament(self):
        from . import match_shape
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals(finals_own=0, n=4)
        Progression.objects.filter(pk=line.pk).update(place_to=4)
        with transaction.atomic():
            stage_engine.draw(f_stage, creator, early=True)
        m = f_stage.matches.filter(round_number=1).order_by('match_number').first()
        place = [s for s in match_shape.match_row(m)['slots'] if s['kind'] == 'place'][0]['from_place']
        self.assertEqual(place['tournament'], q.tournament_title)
        self.assertEqual(place['stage'], 'League')

    def test_a_seat_waiting_inside_one_tournament_names_no_tournament(self):
        from . import match_shape
        from .tests_draw_early import draw_early, groups_into_knockout
        t, creator, first, ko = groups_into_knockout()
        draw_early(ko, creator)
        m = ko.matches.filter(round_number=1).order_by('match_number').first()
        place = match_shape.match_row(m)['slots'][0]['from_place']
        self.assertIsNone(place['tournament'])


class NameTests(TestCase):
    """The feeds panel read "demo_amara" beside a bracket reading "Amara
    Nwosu" (walk, 17 October 2026): every name is the one every screen shows."""

    def test_feed_rows_and_the_recall_preview_use_display_names(self):
        q, q_stage, finals, f_stage, line, creator = fired_pair(block_draw=False)
        first = qualifier_reg(line, 1)
        first.user.full_name = 'Amara Full Name'
        first.user.save(update_fields=['full_name'])
        row = feeds.rows(finals)['incoming'][0]
        names = {qr['registration_id']: qr['name'] for qr in row['qualifiers']}
        self.assertEqual(names[first.id], 'Amara Full Name')
        self.assertEqual(feeds.recall_preview(first)['name'], 'Amara Full Name')
