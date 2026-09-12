# -*- coding: utf-8 -*-
"""The seed path: a catalogue with no scraper.

The scraper needs a desktop, a VPN and sometimes a person to answer Cloudflare.
Everything after the catalogue needs cards and does not care where they came
from, so this path exists and everything downstream is testable without any of
that.
"""

import json
import os
import tempfile
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from vent_cards.models import GameCard


def a_file(body):
    handle, path = tempfile.mkstemp(suffix='.json')
    with os.fdopen(handle, 'w', encoding='utf-8') as fh:
        json.dump(body, fh)
    return path


class SeedTests(TestCase):
    def seed(self, *args):
        out = StringIO()
        call_command('seed_cards', *args, stdout=out)
        return out.getvalue()

    def test_the_demo_squad_is_big_enough_to_pick_an_eleven(self):
        self.seed('--demo')
        self.assertGreaterEqual(GameCard.objects.count(), 23)

    def test_no_demo_card_claims_a_face_it_cannot_prove(self):
        """CEO, 4 September 2026: "Are the player cards on the website the
        actual cards from futbin?"

        They are not, and the portraits made that worse rather than better. The
        seed built a Futbin CDN address out of an EA player id, and the ids in
        that table are hand-written. Rendered side by side, the famous ones
        were right and the guessed ones were not: "Victor Osimhen" showed Phil
        Foden, "Wilfred Ndidi" and "Samuel Chukwueze" showed white players,
        and Bruno Onyemaechi answered 404.

        A demo showing one player's face under another player's name is worse
        than one with no faces. Real portraits come from the scraper, which
        reads the address off the page rather than building it.
        """
        self.seed('--demo')
        for card in GameCard.objects.all():
            self.assertEqual(card.image_url, '', card.name)

    def test_no_demo_card_claims_frame_art_that_does_not_exist(self):
        """This test used to assert the OPPOSITE, and it was asserting a lie.

        The seed set `frame_url` to
        `cdn.futbin.com/design/img/cards/tiny/<item_type>.png`, which answers
        404. Checked on 4 September 2026. So every seeded card asked for a file
        that is not there, failed, and fell back to a plain coloured band, and
        the CEO reported the cards as carrying no design.

        A URL known not to resolve is worse than no URL: the card waits on a
        request that will fail before it can draw anything. `FutCard` builds the
        whole card from the data now, so the frame is a bonus layer and its
        absence is the normal case.
        """
        self.seed('--demo')
        for card in GameCard.objects.all():
            self.assertEqual(card.frame_url, '', card.name)

    def test_demo_stats_are_the_player_rather_than_one_row_repeated(self):
        """Every demo card carried 80/80/80/80/60/75, which taught nobody
        anything about a feature whose whole point is the numbers."""
        self.seed('--demo')
        shapes = {tuple(sorted(c.stats.items()))
                  for c in GameCard.objects.all()}
        self.assertGreaterEqual(len(shapes), 20)

    def test_a_keeper_gets_a_keepers_six_and_not_an_outfielders(self):
        """PAC on a goalkeeper is wrong in a way a viewer sees immediately."""
        self.seed('--demo')
        keeper = GameCard.objects.filter(position='GK').first()
        self.assertIsNotNone(keeper)
        self.assertIn('div', keeper.stats)
        self.assertNotIn('pac', keeper.stats)

        outfield = GameCard.objects.filter(position='ST').first()
        self.assertIn('pac', outfield.stats)
        self.assertNotIn('div', outfield.stats)

    def test_the_demo_has_a_keeper_and_a_striker_so_a_squad_is_possible(self):
        self.seed('--demo')
        positions = set(GameCard.objects.values_list('position', flat=True))
        self.assertIn('GK', positions)
        self.assertIn('ST', positions)

    def test_seeding_twice_changes_nothing_the_second_time(self):
        self.seed('--demo')
        before = GameCard.objects.count()
        output = self.seed('--demo')
        self.assertEqual(GameCard.objects.count(), before)
        self.assertIn('0 added', output)

    def test_names_are_slugged_so_a_search_finds_them(self):
        self.seed('--demo')
        self.assertEqual(GameCard.objects.get(name='Kylian Mbappe').slug,
                         'kylian_mbappe')

    def test_a_file_is_read_in_the_same_shape_the_scraper_posts(self):
        path = a_file({'cards': [{'source_id': 'f1', 'name': 'File Card',
                                  'rating': 88, 'position': 'CM'}]})
        try:
            self.seed('--file', path)
        finally:
            os.unlink(path)
        self.assertTrue(GameCard.objects.filter(name='File Card').exists())

    def test_a_bare_list_works_too(self):
        path = a_file([{'source_id': 'f2', 'name': 'Bare Card', 'rating': 80}])
        try:
            self.seed('--file', path)
        finally:
            os.unlink(path)
        self.assertTrue(GameCard.objects.filter(name='Bare Card').exists())

    def test_a_row_missing_its_identity_is_skipped_not_stored(self):
        path = a_file([{'name': 'No Id', 'rating': 80}])
        try:
            output = self.seed('--file', path)
        finally:
            os.unlink(path)
        self.assertIn('1 skipped', output)
        self.assertEqual(GameCard.objects.count(), 0)

    def test_a_thin_file_does_not_erase_what_is_already_there(self):
        """The same rule the ingest endpoint keeps: absent means leave alone."""
        self.seed('--demo')
        # Given a picture by hand, the way a scrape would, so the point of the
        # test is the thin file and not where the picture came from.
        GameCard.objects.filter(source_id='231747').update(
            image_url='https://cdn.futbin.com/content/fifa25/img/players/231747.png')
        before = GameCard.objects.get(source_id='231747')
        self.assertTrue(before.image_url)

        path = a_file([{'source_id': '231747', 'name': 'Kylian Mbappe',
                        'rating': 92}])
        try:
            self.seed('--file', path)
        finally:
            os.unlink(path)

        after = GameCard.objects.get(source_id='231747')
        self.assertEqual(after.rating, 92, 'the rating should have moved')
        self.assertEqual(after.image_url, before.image_url,
                         'the picture should not have been erased')

    def test_asking_for_neither_is_refused(self):
        with self.assertRaises(CommandError):
            self.seed()

    def test_a_file_that_is_not_json_is_refused_by_name(self):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w', encoding='utf-8') as fh:
            fh.write('this is not json')
        try:
            with self.assertRaises(CommandError):
                self.seed('--file', path)
        finally:
            os.unlink(path)
