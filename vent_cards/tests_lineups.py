# -*- coding: utf-8 -*-
"""Lineups, and the deadline the organiser sets for them.

CEO, 3 September 2026: "The submission time should be a feature and something
the tournament organizers should be able to set."
"""

from datetime import time, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_cards import formations
from vent_cards.models import GameCard, Lineup, LineupRules
from vent_tournament.models import Tournament


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=(name + 'k' * 16)[:16])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


def a_card(source_id, name, rating=85, position='ST'):
    from vent_cards.views import slugify_name
    return GameCard.objects.create(
        source='futbin', source_id=str(source_id), name=name,
        slug=slugify_name(name), rating=rating, position=position,
        item_type='gold', stats={'pac': 90},
        image_url='https://cdn.futbin.com/img/players/%s.png' % source_id,
        frame_url='https://cdn.futbin.com/img/cards/tiny/gold.png')


class LineupTestCase(TestCase):
    def setUp(self):
        self.organiser, self.org_auth = a_user('ln_org')
        self.player, self.auth = a_user('ln_player')
        self.other, self.other_auth = a_user('ln_other')

        game = Games.objects.create(game_title='EA FC LINEUP')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Lineup Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='individual',
            entry_fee='Free', is_draft=False, bracket_type='round_robin')
        self.ref = self.tournament.slug or self.tournament.tournament_id

        # Lineups on, no deadline: open.
        LineupRules.objects.create(tournament=self.tournament, enabled=True)

        # Twenty-three distinct people, so a full squad can be built.
        self.cards = [a_card(i, 'Player %02d' % i, 80 + (i % 15))
                      for i in range(23)]

    def url(self, tail=''):
        return '/tournament/%s/lineup%s' % (self.ref, tail)

    def eleven(self):
        return [{'slot_index': i, 'card_id': self.cards[i].id} for i in range(11)]

    def save(self, slots=None, formation='4-3-3', auth=None):
        return self.client.post(
            self.url('/'),
            data={'formation': formation, 'slots': slots if slots is not None
                  else self.eleven()},
            content_type='application/json', **(auth or self.auth))


class BuildingALineupTests(LineupTestCase):

    def test_a_player_saves_an_eleven(self):
        res = self.save()
        self.assertEqual(res.status_code, 200, res.content[:400])
        lineup = res.json()['data']['lineup']
        self.assertEqual(lineup['formation'], '4-3-3')
        self.assertEqual(len(lineup['slots']), 11)
        self.assertTrue(lineup['complete'])
        # Saving is not submitting: eleven cards saved is a draft until the
        # player says it is their answer. See tests_review.
        self.assertEqual(lineup['status'], 'draft')
        self.assertIsNone(lineup['submitted_at'])

    def test_each_slot_keeps_the_position_it_was_picked_for(self):
        self.save()
        slots = self.client.get(self.url('/'), **self.auth).json()['data']['lineup']['slots']
        by_index = {s['slot_index']: s['position'] for s in slots}
        self.assertEqual(by_index[0], 'GK')
        self.assertEqual(by_index[9], 'ST')

    def test_a_bench_is_allowed_and_optional(self):
        slots = self.eleven() + [
            {'slot_index': 11, 'card_id': self.cards[11].id},
            {'slot_index': 12, 'card_id': self.cards[12].id}]
        res = self.save(slots)
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(len(res.json()['data']['lineup']['slots']), 13)

    def test_a_full_twenty_three_fits(self):
        slots = [{'slot_index': i, 'card_id': self.cards[i].id} for i in range(23)]
        res = self.save(slots)
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_a_slot_beyond_the_squad_is_refused(self):
        res = self.save([{'slot_index': 23, 'card_id': self.cards[0].id}])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'BAD_SLOT')

    def test_two_cards_in_one_slot_is_refused(self):
        res = self.save([{'slot_index': 0, 'card_id': self.cards[0].id},
                         {'slot_index': 0, 'card_id': self.cards[1].id}])
        self.assertEqual(res.json()['code'], 'DUPLICATE_SLOT')

    def test_the_same_card_twice_is_refused(self):
        res = self.save([{'slot_index': 0, 'card_id': self.cards[0].id},
                         {'slot_index': 1, 'card_id': self.cards[0].id}])
        self.assertEqual(res.json()['code'], 'DUPLICATE_CARD')

    def test_two_cards_of_the_same_person_are_refused_by_name(self):
        """Gold Mbappe and TOTY Mbappe are one man and cannot both play."""
        gold = a_card(900, 'Kylian Mbappé', 91)
        toty = a_card(901, 'Kylian Mbappe', 98)
        self.assertEqual(gold.slug, toty.slug)

        res = self.save([{'slot_index': 0, 'card_id': gold.id},
                         {'slot_index': 1, 'card_id': toty.id}])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'DUPLICATE_PLAYER')
        self.assertIn('Mbapp', res.json()['message'])

    def test_a_card_that_is_not_in_the_catalogue_is_refused(self):
        res = self.save([{'slot_index': 0, 'card_id': 99999}])
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'CARD_NOT_FOUND')

    def test_a_formation_nobody_offers_is_refused(self):
        res = self.save(formation='9-0-1')
        self.assertEqual(res.json()['code'], 'UNKNOWN_FORMATION')

    def test_every_formation_offered_can_actually_be_saved(self):
        """A picker offering a formation the server refuses is worse than none."""
        for key in formations.FORMATION_KEYS:
            res = self.save(formation=key)
            self.assertEqual(res.status_code, 200,
                             '%s was refused: %s' % (key, res.content[:200]))

    def test_saving_again_replaces_rather_than_piles_up(self):
        self.save()
        self.save([{'slot_index': 0, 'card_id': self.cards[5].id}])
        lineup = Lineup.objects.get(tournament=self.tournament, user=self.player)
        self.assertEqual(lineup.slots.count(), 1)
        self.assertEqual(Lineup.objects.filter(user=self.player).count(), 1)

    def test_signed_out_cannot_read_or_save_a_lineup(self):
        self.assertEqual(self.client.get(self.url('/')).status_code, 401)
        res = self.client.post(self.url('/'), data={'formation': '4-3-3', 'slots': []},
                               content_type='application/json')
        self.assertEqual(res.status_code, 401)

    def test_one_player_cannot_write_another_players_lineup(self):
        """There is no route to somebody else's: the save is always your own."""
        self.save()
        self.save([{'slot_index': 0, 'card_id': self.cards[3].id}],
                  auth=self.other_auth)
        mine = Lineup.objects.get(tournament=self.tournament, user=self.player)
        theirs = Lineup.objects.get(tournament=self.tournament, user=self.other)
        self.assertEqual(mine.slots.count(), 11)
        self.assertEqual(theirs.slots.count(), 1)

    def test_a_broadcast_can_read_a_lineup_with_no_session(self):
        """A team sheet is public, and OBS carries no cookie."""
        self.save()
        res = self.client.get('/tournament/%s/lineup/ln_player/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(len(res.json()['data']['lineup']['slots']), 11)

    def test_the_organiser_list_counts_who_has_submitted(self):
        self.save()
        self.save([{'slot_index': 0, 'card_id': self.cards[1].id}],
                  auth=self.other_auth)
        body = self.client.get('/tournament/%s/lineups/' % self.ref).json()['data']
        self.assertEqual(body['count'], 2)
        self.assertEqual(body['submitted'], 1, 'only the full eleven counts')


class TheDeadlineIsTheOrganisersTests(LineupTestCase):
    """CEO: the submission time is a feature the organiser sets."""

    def rules(self, **fields):
        return self.client.post(
            self.url('-rules/'), data=fields, content_type='application/json',
            **self.org_auth)

    def window(self):
        res = self.client.get(self.url('-rules/'))
        self.assertEqual(res.status_code, 200, res.content[:200])
        return res.json()['data']['rules']

    def test_anybody_can_see_the_deadline(self):
        """A deadline nobody can see until they try to save is a surprise."""
        res = self.client.get(self.url('-rules/'))
        self.assertEqual(res.status_code, 200)
        self.assertIn('state', res.json()['data']['rules'])

    def test_the_organiser_sets_a_closing_time(self):
        when = timezone.now() + timedelta(days=2)
        res = self.rules(closes_at=when.isoformat())
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(self.window()['state'], 'open')

    def test_past_the_deadline_a_save_is_refused_and_says_when_it_closed(self):
        closed = timezone.now() - timedelta(hours=1)
        self.rules(closes_at=closed.isoformat())

        res = self.save()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'LINEUPS_CLOSED')
        self.assertTrue(res.json()['data']['closes_at'],
                        'a refusal must carry the time it closed')

    def test_a_weekly_deadline_is_a_standing_rule(self):
        """"Lineups in by Thursday ten" should not need retyping every week."""
        self.rules(weekly_day=3, weekly_time='10:00:00')
        window = self.window()
        self.assertEqual(window['state'], 'open')
        self.assertTrue(window['closes_at'], 'it must say when it next closes')

    def test_before_it_opens_nobody_can_save(self):
        soon = timezone.now() + timedelta(days=1)
        later = timezone.now() + timedelta(days=3)
        self.rules(opens_at=soon.isoformat(), closes_at=later.isoformat())
        self.assertEqual(self.window()['state'], 'not_yet')
        self.assertEqual(self.save().status_code, 409)

    def test_the_organiser_can_lock_by_hand_before_the_deadline(self):
        self.rules(closes_at=(timezone.now() + timedelta(days=2)).isoformat())
        self.rules(locked_by_hand=True)
        self.assertEqual(self.window()['state'], 'closed')
        self.assertEqual(self.save().status_code, 409)

    def test_the_organiser_can_reopen_after_the_deadline(self):
        """Somebody's power went out. A deadline that cannot be lifted ruins it."""
        self.rules(closes_at=(timezone.now() - timedelta(hours=1)).isoformat())
        self.assertEqual(self.save().status_code, 409)

        self.rules(reopened_by_hand=True)
        self.assertEqual(self.window()['state'], 'open')
        self.assertEqual(self.save().status_code, 200)

    def test_locking_and_reopening_cannot_both_be_true(self):
        self.rules(locked_by_hand=True)
        self.rules(reopened_by_hand=True)
        rules = LineupRules.objects.get(tournament=self.tournament)
        self.assertFalse(rules.locked_by_hand and rules.reopened_by_hand)

    def test_the_change_window_lets_a_limited_edit_through_afterwards(self):
        now = timezone.now()
        self.rules(closes_at=(now - timedelta(hours=2)).isoformat(),
                   changes_open_at=(now - timedelta(minutes=30)).isoformat(),
                   changes_close_at=(now + timedelta(minutes=30)).isoformat(),
                   changes_allowed=1)
        window = self.window()
        self.assertEqual(window['state'], 'changes_only')
        self.assertTrue(window['limited'])
        self.assertEqual(window['changes_allowed'], 1)
        self.assertEqual(self.save().status_code, 200)

    def test_outside_the_change_window_it_is_shut_again(self):
        now = timezone.now()
        self.rules(closes_at=(now - timedelta(days=2)).isoformat(),
                   changes_open_at=(now - timedelta(days=1)).isoformat(),
                   changes_close_at=(now - timedelta(hours=20)).isoformat())
        self.assertEqual(self.window()['state'], 'closed')

    def test_lineups_off_means_there_is_no_picker_at_all(self):
        self.rules(enabled=False)
        self.assertEqual(self.window()['state'], 'off')
        res = self.save()
        self.assertEqual(res.json()['code'], 'LINEUPS_OFF')

    def test_a_tournament_with_no_rules_row_has_lineups_off(self):
        """The safe default: no picker on the hundreds of non-EAFC tournaments."""
        LineupRules.objects.filter(tournament=self.tournament).delete()
        self.assertEqual(self.window()['state'], 'off')

    def test_only_the_organiser_sets_the_deadline(self):
        res = self.client.post(self.url('-rules/'), data={'enabled': False},
                               content_type='application/json', **self.other_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_cannot_set_the_deadline(self):
        res = self.client.post(self.url('-rules/'), data={'enabled': False},
                               content_type='application/json')
        self.assertEqual(res.status_code, 401)

    def test_a_weekday_outside_the_week_is_refused_by_name(self):
        res = self.rules(weekly_day=9)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['data']['field'], 'weekly_day')


class FormationCatalogueTests(TestCase):
    def test_every_formation_has_exactly_eleven_slots(self):
        for key in formations.FORMATION_KEYS:
            slots = formations.get(key)
            self.assertEqual(len(slots), 11, '%s has %d' % (key, len(slots)))

    def test_every_formation_has_exactly_one_goalkeeper(self):
        for key in formations.FORMATION_KEYS:
            keepers = [s for s in formations.get(key) if s['position'] == 'GK']
            self.assertEqual(len(keepers), 1, key)

    def test_slot_indices_are_zero_to_ten_with_no_gaps(self):
        for key in formations.FORMATION_KEYS:
            self.assertEqual(sorted(s['index'] for s in formations.get(key)),
                             list(range(11)), key)

    def test_every_slot_is_on_the_pitch(self):
        for key in formations.FORMATION_KEYS:
            for slot in formations.get(key):
                self.assertTrue(0 <= slot['x'] <= 100, (key, slot))
                self.assertTrue(0 <= slot['y'] <= 100, (key, slot))

    def test_the_squad_is_twenty_three(self):
        self.assertEqual(formations.TOTAL_SLOTS, 23)
        self.assertEqual(formations.FIRST_SUB, 11)

    def test_a_bench_slot_has_no_position_because_anybody_may_sit_there(self):
        self.assertEqual(formations.slot_position('4-3-3', 11), '')
        self.assertEqual(formations.slot_position('4-3-3', 0), 'GK')

    def test_the_catalogue_lists_every_key(self):
        listed = [f['key'] for f in formations.catalogue()]
        self.assertEqual(listed, formations.FORMATION_KEYS)
        self.assertIn(formations.DEFAULT_FORMATION, listed)


class TheOverlayFollowsAChangeTests(LineupTestCase):
    """CEO: "updated automatically for each player".

    An element page redraws only when the feed's `version` moves. A lineup
    lives in its own table, so nothing in the tournament feed's version moved
    when a player changed their squad: the page compared the same version,
    skipped the redraw, and the squad depth graphic froze on the first lineup
    it ever saw. Found on production by changing a lineup and watching the
    overlay not follow.
    """

    def version(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content[:200])
        return res.json()['data']['version']

    def test_saving_a_lineup_moves_the_version(self):
        before = self.version()
        self.save()
        self.assertNotEqual(self.version(), before,
                            'the overlay would never have redrawn')

    def test_swapping_one_card_moves_the_version(self):
        """A count alone would not: eleven cards before, eleven after."""
        self.save()
        before = self.version()
        swapped = self.eleven()
        swapped[5] = {'slot_index': 5, 'card_id': self.cards[20].id}
        self.save(swapped)
        self.assertNotEqual(self.version(), before,
                            'a swap left the version identical')

    def test_changing_only_the_formation_moves_the_version(self):
        self.save()
        before = self.version()
        self.save(formation='4-4-2')
        self.assertNotEqual(self.version(), before)

    def test_the_version_holds_still_when_nothing_changed(self):
        """The other half: an overlay must not redraw four times a minute."""
        self.save()
        self.assertEqual(self.version(), self.version())

    def test_the_feed_carries_every_lineup_for_an_uploaded_overlay(self):
        self.save()
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        lineups = res.json()['data']['lineups']
        self.assertEqual(len(lineups), 1)
        self.assertEqual(lineups[0]['player'], 'ln_player')
        # Each slot carries the card's own fields, so a repeat can read them.
        self.assertIn('name', lineups[0]['slots'][0])
        self.assertIn('rating', lineups[0]['slots'][0])
