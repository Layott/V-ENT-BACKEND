# -*- coding: utf-8 -*-
"""The formation catalogue, and the things about it that can silently rot.

CEO, 4 September 2026: "i know there is a lot more formations than these."
There were eight. The reason nobody noticed is that eight formations is a
perfectly working feature; it is just the wrong number, and no test can tell
you a number is wrong unless somebody writes down what right looks like.

So this pins the count, and pins the properties that make a formation usable:
eleven slots, on the pitch, with a position on each, and a name the picker and
the validator both accept. Every one of those has a way of going wrong that
draws a plausible pitch and refuses a legitimate save.
"""

from django.test import TestCase

from vent_cards import formations as f
from vent_cards.models import Lineup


class TheCatalogueTests(TestCase):
    def test_there_are_more_than_the_original_eight(self):
        """The whole point of the change, written down so it cannot slip back."""
        self.assertGreaterEqual(len(f.FORMATION_KEYS), 28)

    def test_the_list_and_the_dict_agree(self):
        """A key in one and not the other is offered and then refused."""
        self.assertEqual(sorted(f.FORMATION_KEYS), sorted(f.FORMATIONS))

    def test_no_formation_is_listed_twice(self):
        self.assertEqual(len(f.FORMATION_KEYS), len(set(f.FORMATION_KEYS)))

    def test_every_formation_has_eleven(self):
        for key, slots in f.FORMATIONS.items():
            self.assertEqual(len(slots), 11, '%s has %d' % (key, len(slots)))

    def test_the_indices_are_zero_to_ten_in_order(self):
        for key, slots in f.FORMATIONS.items():
            self.assertEqual([s['index'] for s in slots], list(range(11)), key)

    def test_every_slot_is_on_the_pitch(self):
        """A percentage outside 0 to 100 draws a card off the edge of the pitch."""
        for key, slots in f.FORMATIONS.items():
            for slot in slots:
                self.assertTrue(0 <= slot['x'] <= 100, '%s %s' % (key, slot))
                self.assertTrue(0 <= slot['y'] <= 100, '%s %s' % (key, slot))

    def test_every_slot_has_a_position(self):
        for key, slots in f.FORMATIONS.items():
            for slot in slots:
                self.assertTrue(slot['position'].strip(), '%s %s' % (key, slot))

    def test_every_formation_starts_with_a_keeper(self):
        for key, slots in f.FORMATIONS.items():
            self.assertEqual(slots[0]['position'], 'GK', key)

    def test_no_two_slots_sit_on_top_of_each_other(self):
        """Two cards at one point is a formation with a hidden player."""
        for key, slots in f.FORMATIONS.items():
            points = {(s['x'], s['y']) for s in slots}
            self.assertEqual(len(points), 11, '%s has overlapping slots' % key)

    def test_no_two_cards_overlap_when_they_are_drawn(self):
        """Distinct coordinates are not enough: a CARD has a size.

        The picker draws a card at about an eighth of the pitch's width, and a
        card is 1.4 times as tall as it is wide, so it is also about an eighth
        of the pitch's height. Two slots closer than that in BOTH directions
        cover each other on screen.

        This is not theory. On 4 September 2026 the goalkeeper's card sat over
        the middle centre back in every back three and back five, and nothing
        could have told anybody: the coordinates were distinct, the tests were
        green, and it is only visible in a browser.
        """
        span = 13  # per cent of the pitch, a little over a card's own size
        for key, slots in f.FORMATIONS.items():
            for i, a in enumerate(slots):
                for b in slots[i + 1:]:
                    close = (abs(a['x'] - b['x']) < span
                             and abs(a['y'] - b['y']) < span)
                    self.assertFalse(close,
                                     '%s: %s at (%s,%s) covers %s at (%s,%s)'
                                     % (key, a['position'], a['x'], a['y'],
                                        b['position'], b['x'], b['y']))

    def test_the_name_fits_the_column_it_is_stored_in(self):
        """A 17-character formation is offered, picked, and truncated on save."""
        limit = Lineup._meta.get_field('formation').max_length
        for key in f.FORMATION_KEYS:
            self.assertLessEqual(len(key), limit, key)

    def test_the_default_is_one_of_them(self):
        self.assertIn(f.DEFAULT_FORMATION, f.FORMATIONS)

    def test_the_number_of_defenders_matches_the_name(self):
        """A formation called 5-3-2 with four defenders is a typo nobody sees.

        Only the first number is checked, because EA's later numbers describe
        bands rather than counts and a 4-3-3 (2) legitimately reads 4-1-2-3.

        A defender is a defensive position IN THE BACK BAND, and both halves
        are needed. `3-5-2 (2)` carries LWB and RWB, and in that shape they are
        two of the five in midfield rather than two more defenders, which is
        exactly what their depth on the pitch says. Counting by position alone
        called it a back five, which is what this test caught when it was
        first written.
        """
        back = {'CB', 'LB', 'RB', 'LWB', 'RWB'}
        for key, slots in f.FORMATIONS.items():
            want = int(key.split('-')[0])
            got = sum(1 for s in slots
                      if s['position'] in back and s['y'] < 40)
            self.assertEqual(got, want, '%s has %d defenders' % (key, got))


class TheCatalogueAsServedTests(TestCase):
    def test_catalogue_carries_the_bench_sizes(self):
        """The picker draws the bench from these; hardcoding them is the fault
        this catalogue exists to prevent."""
        for entry in f.catalogue():
            self.assertEqual(entry['subs'], f.SUBS)
            self.assertEqual(entry['reserves'], f.RESERVES)
            self.assertEqual(entry['total_slots'], f.TOTAL_SLOTS)

    def test_the_slot_count_adds_up(self):
        self.assertEqual(f.TOTAL_SLOTS, 11 + f.SUBS + f.RESERVES)

    def test_a_bench_slot_has_no_position_of_its_own(self):
        """Any card may sit on the bench, which is how EA works."""
        self.assertEqual(f.slot_position('4-3-3', f.FIRST_SUB), '')

    def test_a_pitch_slot_takes_its_position_from_the_formation(self):
        self.assertEqual(f.slot_position('4-3-3', 0), 'GK')
        self.assertEqual(f.slot_position('3-5-2', 1), 'CB')

    def test_an_unknown_formation_is_refused_rather_than_guessed(self):
        self.assertFalse(f.is_known('4-4-3'))
        self.assertIsNone(f.get('4-4-3'))
        self.assertEqual(f.slot_position('4-4-3', 0), '')

    def test_a_name_with_stray_space_still_resolves(self):
        """The picker sends what the server sent it, but a hand-built request
        or a copied value carries whitespace and should not be a refusal."""
        self.assertTrue(f.is_known(' 4-3-3 '))
