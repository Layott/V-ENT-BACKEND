# -*- coding: utf-8 -*-
"""Where a graphic sits on the frame, and the one thing that must not change.

CEO, 4 September 2026: "SHould also be able to move the position of overlays,
whether they load in at the centre bottom or center top, or top right or top
left or middle or middle right, etc. this mostly affect lower thirds."

The dangerous part of this feature is not the nine positions. It is the
default: a default of anything but `as_designed` moves every graphic already on
air the moment it ships, silently, on somebody's live broadcast. That is what
the first test here exists to hold, and it is why `as_designed` is first in the
list as well as the default.
"""

from django.test import TestCase

from vent_tournament import presentation
from vent_tournament.presentation import PresentationError


class TheDefaultMovesNothingTests(TestCase):
    def test_the_default_position_is_as_designed(self):
        """The whole safety of this feature. Do not change it."""
        self.assertEqual(presentation.DEFAULTS['position'], 'as_designed')

    def test_as_designed_is_offered_first(self):
        self.assertEqual(presentation.POSITIONS[0], 'as_designed')

    def test_the_default_offsets_are_zero(self):
        self.assertEqual(presentation.DEFAULTS['offset_x'], 0)
        self.assertEqual(presentation.DEFAULTS['offset_y'], 0)

    def test_an_overlay_that_sets_nothing_is_unmoved(self):
        look = presentation.resolve(None, None)
        self.assertEqual(look['position'], 'as_designed')
        self.assertEqual(look['offset_x'], 0)
        self.assertEqual(look['offset_y'], 0)


class TheNinePositionsTests(TestCase):
    def test_every_corner_edge_and_the_middle_are_offered(self):
        for name in ['top_left', 'top_centre', 'top_right',
                     'middle_left', 'centre', 'middle_right',
                     'bottom_left', 'bottom_centre', 'bottom_right']:
            self.assertIn(name, presentation.POSITIONS, name)

    def test_there_are_nine_places_plus_as_designed(self):
        self.assertEqual(len(presentation.POSITIONS), 10)

    def test_each_one_is_accepted(self):
        for name in presentation.POSITIONS:
            self.assertEqual(presentation.clean({'position': name}),
                             {'position': name})

    def test_a_position_nobody_offers_is_refused_by_name(self):
        """Refused rather than dropped: an operator who set it and saw it
        ignored cannot tell a typo from a feature that does not work."""
        with self.assertRaises(PresentationError) as caught:
            presentation.clean({'position': 'bottom_middle'})
        self.assertIn('A position is one of', str(caught.exception))
        self.assertEqual(caught.exception.field, 'position')

    def test_american_spelling_is_not_quietly_accepted(self):
        """`centre` is the spelling this codebase uses everywhere else. A
        second accepted spelling is a second value meaning one thing."""
        with self.assertRaises(PresentationError):
            presentation.clean({'position': 'center'})


class TheNudgeTests(TestCase):
    def test_a_nudge_in_both_directions_is_accepted(self):
        self.assertEqual(
            presentation.clean({'offset_x': 40, 'offset_y': -120}),
            {'offset_x': 40, 'offset_y': -120})

    def test_a_nudge_may_be_negative(self):
        self.assertEqual(presentation.clean({'offset_y': -800}),
                         {'offset_y': -800})

    def test_a_nudge_that_would_put_it_off_the_frame_is_refused(self):
        for value in (900, -900):
            with self.assertRaises(PresentationError) as caught:
                presentation.clean({'offset_x': value})
            self.assertIn('between', str(caught.exception))

    def test_the_limit_itself_is_allowed(self):
        limit = presentation.OFFSET_LIMIT
        self.assertEqual(presentation.clean({'offset_x': limit}),
                         {'offset_x': limit})

    def test_words_are_not_pixels(self):
        with self.assertRaises(PresentationError) as caught:
            presentation.clean({'offset_x': 'a bit left'})
        self.assertIn('whole number of pixels', str(caught.exception))

    def test_a_number_sent_as_text_still_works(self):
        """A form sends strings, and refusing that would be pedantry."""
        self.assertEqual(presentation.clean({'offset_x': '60'}),
                         {'offset_x': 60})


class TwoLevelsTests(TestCase):
    """A broadcast sets its house style once; any single graphic may differ."""

    def test_a_session_default_reaches_an_element_that_says_nothing(self):
        look = presentation.resolve({'position': 'bottom_centre'}, None)
        self.assertEqual(look['position'], 'bottom_centre')

    def test_an_element_beats_the_session(self):
        look = presentation.resolve({'position': 'bottom_centre'},
                                    {'position': 'top_right'})
        self.assertEqual(look['position'], 'top_right')

    def test_an_element_can_return_to_its_own_design(self):
        """Setting a house style must not trap one graphic away from where it
        was drawn to sit."""
        look = presentation.resolve({'position': 'bottom_centre'},
                                    {'position': 'as_designed'})
        self.assertEqual(look['position'], 'as_designed')

    def test_the_nudge_merges_the_same_way(self):
        look = presentation.resolve({'offset_y': -40}, {'offset_y': 12})
        self.assertEqual(look['offset_y'], 12)

    def test_position_travels_with_the_rest_of_the_look(self):
        look = presentation.resolve({'entry': 'fade'},
                                    {'position': 'middle_right'})
        self.assertEqual(look['entry'], 'fade')
        self.assertEqual(look['position'], 'middle_right')
        self.assertEqual(look['exit'], 'fade')


class TheCatalogueTests(TestCase):
    """The console keeps no list of its own, so it cannot drift from this one."""

    def test_the_catalogue_carries_the_positions(self):
        self.assertEqual(presentation.catalogue()['positions'],
                         presentation.POSITIONS)

    def test_the_catalogue_carries_the_nudge_limit(self):
        self.assertEqual(presentation.catalogue()['offset_limit'],
                         presentation.OFFSET_LIMIT)

    def test_the_catalogue_defaults_include_position(self):
        self.assertEqual(presentation.catalogue()['defaults']['position'],
                         'as_designed')

    def test_the_console_takes_the_LIST_from_the_server(self):
        """The console may hold LABELS for these, because a label has to live
        somewhere and a missing one falls back to the raw name. What it must
        not hold is the LIST: a second list is the same-list-in-two-places
        fault, which this codebase has recorded four times, and it shows up as
        a position an operator can pick and the server then refuses.
        """
        import os
        import re
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        panel = os.path.join(os.path.dirname(here), 'V-ENT-FRONTEND', 'src',
                             'components', 'studio', 'StudioPanel.js')
        if not os.path.exists(panel):
            self.skipTest('frontend not beside this checkout')
        with open(panel, encoding='utf-8') as handle:
            source = handle.read()

        self.assertIn('positions', source,
                      'the console never reads the catalogue of positions')

        # An array literal holding two or more of them IS a second list.
        names = '|'.join(re.escape(p) for p in presentation.POSITIONS)
        second_list = re.compile(
            r"""\[[^\]]*?['"](?:%s)['"][^\]]*?,[^\]]*?['"](?:%s)['"]"""
            % (names, names), re.S)
        found = second_list.search(source)
        self.assertIsNone(found,
                          'the console holds its own list of positions: %s'
                          % (found.group(0)[:80] if found else ''))


class TheUploadedHalfTests(TestCase):
    """An organiser's own file is moved by editing it, and the prompt says how.

    The studio's Sits control moves V-ENT's own graphics, which an organiser
    cannot edit. An uploaded overlay is the opposite case: they own every rule
    in it already, so the useful thing is telling the tool that generates it
    where a graphic belongs and how much edge to leave.
    """

    def test_the_prompt_says_where_a_graphic_sits(self):
        from vent_tournament.views_overlays import (DESIGNER_PROMPT_EVENT,
                                                    DESIGNER_PROMPT_TOURNAMENT)
        for prompt in (DESIGNER_PROMPT_TOURNAMENT, DESIGNER_PROMPT_EVENT):
            self.assertIn('WHERE IT SITS ON THE FRAME', prompt)

    def test_the_prompt_gives_the_safe_area_in_pixels(self):
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT
        self.assertIn('96px', DESIGNER_PROMPT_TOURNAMENT)
        self.assertIn('54px', DESIGNER_PROMPT_TOURNAMENT)

    def test_the_prompt_names_where_a_lower_third_belongs(self):
        """The CEO's own words: "this mostly affect lower thirds"."""
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT
        self.assertIn('lower third belongs', DESIGNER_PROMPT_TOURNAMENT)

    def test_the_prompt_warns_against_anchoring_all_four_edges(self):
        """Setting left and right together stretches a graphic across the
        frame the moment a name is longer than the placeholder."""
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT
        self.assertIn('never all four', DESIGNER_PROMPT_TOURNAMENT)
