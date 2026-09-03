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

    def test_every_demo_card_carries_both_images(self):
        """A card with no art draws the fallback, which is a worse demo."""
        self.seed('--demo')
        for card in GameCard.objects.all():
            self.assertTrue(card.image_url, card.name)
            self.assertTrue(card.frame_url, card.name)

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
