# -*- coding: utf-8 -*-
"""The card catalogue: ingest, search, and the rules that protect the data."""

from django.test import TestCase, override_settings

from vent_cards.models import GameCard
from vent_cards.views import slugify_name

KEY = 'test-ingest-key'
AUTH = {'HTTP_X_CARDS_KEY': KEY}


def a_row(source_id='1', name='Kylian Mbappe', rating=91, **extra):
    row = {
        'source_id': source_id, 'name': name, 'rating': rating,
        'position': 'ST', 'club': 'Real Madrid', 'league': 'LALIGA',
        'nation': 'France', 'item_type': 'gold',
        'stats': {'pac': 97, 'sho': 90, 'pas': 80, 'dri': 92, 'def': 36, 'phy': 78},
        'price_coins': 1_200_000,
        'image_url': 'https://cdn.futbin.com/img/players/231747.png',
        'frame_url': 'https://cdn.futbin.com/img/cards/tiny/gold.png',
    }
    row.update(extra)
    return row


@override_settings(CARDS_INGEST_KEY=KEY)
class IngestTests(TestCase):
    url = '/cards/ingest/'

    def post(self, cards, **headers):
        return self.client.post(self.url, data={'cards': cards},
                                content_type='application/json',
                                **dict(AUTH, **headers))

    def test_a_card_arrives_whole(self):
        res = self.post([a_row()])
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['added'], 1)

        card = GameCard.objects.get(source_id='1')
        self.assertEqual(card.name, 'Kylian Mbappe')
        self.assertEqual(card.rating, 91)
        self.assertEqual(card.stats['pac'], 97)
        self.assertEqual(card.price_coins, 1_200_000)
        # Both images, both from Futbin.
        self.assertIn('players', card.image_url)
        self.assertIn('cards', card.frame_url)

    def test_the_same_card_twice_is_not_written_twice(self):
        self.post([a_row()])
        res = self.post([a_row()])
        self.assertEqual(res.json()['data'], dict(res.json()['data'],
                                                  added=0, changed=0, unchanged=1))
        self.assertEqual(GameCard.objects.count(), 1)

    def test_a_price_that_moved_is_the_only_thing_written(self):
        self.post([a_row()])
        res = self.post([a_row(price_coins=980_000)])
        self.assertEqual(res.json()['data']['changed'], 1)
        self.assertEqual(GameCard.objects.get(source_id='1').price_coins, 980_000)

    def test_a_scrape_that_could_not_read_the_price_does_not_erase_it(self):
        """The fault this rule exists for: a partial row destroying a good one."""
        self.post([a_row()])
        thin = a_row()
        thin.pop('price_coins')
        self.post([thin])
        self.assertEqual(GameCard.objects.get(source_id='1').price_coins, 1_200_000)

    def test_an_explicit_null_price_is_also_left_alone(self):
        """Futbin shows a dash for an untradeable card. That is not zero."""
        self.post([a_row()])
        self.post([a_row(price_coins=None)])
        self.assertEqual(GameCard.objects.get(source_id='1').price_coins, 1_200_000)

    def test_a_row_missing_its_identity_is_reported_not_stored(self):
        res = self.post([{'name': 'Nobody', 'rating': 80}])
        self.assertEqual(res.json()['data']['skipped'], 1)
        self.assertTrue(res.json()['data']['problems'])
        self.assertEqual(GameCard.objects.count(), 0)

    def test_a_row_with_no_rating_is_skipped(self):
        res = self.post([a_row(rating=None)])
        self.assertEqual(res.json()['data']['skipped'], 1)

    def test_the_wrong_key_is_refused(self):
        res = self.client.post(self.url, data={'cards': [a_row()]},
                               content_type='application/json',
                               HTTP_X_CARDS_KEY='not-the-key')
        self.assertEqual(res.status_code, 401)
        self.assertEqual(GameCard.objects.count(), 0)

    def test_no_key_at_all_is_refused(self):
        res = self.client.post(self.url, data={'cards': [a_row()]},
                               content_type='application/json')
        self.assertEqual(res.status_code, 401)

    @override_settings(CARDS_INGEST_KEY='')
    def test_ingest_with_no_key_configured_refuses_everybody(self):
        """The safe default: an unconfigured write endpoint takes nothing."""
        res = self.post([a_row()])
        self.assertEqual(res.status_code, 503)
        self.assertEqual(GameCard.objects.count(), 0)

    def test_a_batch_larger_than_the_cap_is_refused(self):
        res = self.post([a_row(source_id=str(i)) for i in range(2001)])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'TOO_MANY')

    def test_a_batch_carries_many_cards(self):
        res = self.post([a_row(source_id=str(i), name='Player %d' % i)
                         for i in range(50)])
        self.assertEqual(res.json()['data']['added'], 50)


class SlugTests(TestCase):
    def test_accents_are_stripped_so_a_search_finds_the_name(self):
        self.assertEqual(slugify_name('Mbappé'), 'mbappe')
        self.assertEqual(slugify_name('Nicolò Barella'), 'nicolo_barella')
        self.assertEqual(slugify_name("N'Golo Kanté"), 'n_golo_kante')

    def test_two_cards_of_one_person_share_a_slug(self):
        """Which is what stops both being picked at once."""
        self.assertEqual(slugify_name('Kylian Mbappé'),
                         slugify_name('KYLIAN MBAPPE'))


@override_settings(CARDS_INGEST_KEY=KEY)
class SearchTests(TestCase):
    def setUp(self):
        self.client.post('/cards/ingest/', data={'cards': [
            a_row('1', 'Kylian Mbappé', 91, position='ST'),
            a_row('2', 'Erling Haaland', 92, position='ST', item_type='gold'),
            a_row('3', 'Virgil van Dijk', 90, position='CB'),
            a_row('4', 'Pelé', 98, position='CAM', item_type='icon'),
        ]}, content_type='application/json', **AUTH)

    def find(self, **params):
        query = '&'.join('%s=%s' % (k, v) for k, v in params.items())
        res = self.client.get('/cards/search/?' + query)
        self.assertEqual(res.status_code, 200, res.content[:200])
        return res.json()['data']['cards']

    def test_a_name_without_its_accents_still_finds_the_card(self):
        names = [c['name'] for c in self.find(q='Mbappe')]
        self.assertEqual(names, ['Kylian Mbappé'])

    def test_a_partial_name_works(self):
        self.assertTrue(self.find(q='haal'))

    def test_filtering_by_position(self):
        self.assertEqual({c['position'] for c in self.find(position='ST')}, {'ST'})

    def test_filtering_by_item_type(self):
        self.assertEqual([c['name'] for c in self.find(item_type='icon')], ['Pelé'])

    def test_filtering_by_rating(self):
        ratings = [c['rating'] for c in self.find(min_rating=92)]
        self.assertTrue(ratings)
        self.assertTrue(all(r >= 92 for r in ratings))

    def test_a_rating_that_is_not_a_number_is_refused_by_name(self):
        res = self.client.get('/cards/search/?min_rating=high')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['data']['field'], 'min_rating')

    def test_search_needs_no_account(self):
        """A catalogue of facts about a video game is public."""
        res = self.client.get('/cards/search/?q=pele')
        self.assertEqual(res.status_code, 200)

    def test_a_card_carries_both_images_to_whoever_draws_it(self):
        card = self.find(q='mbappe')[0]
        self.assertTrue(card['image_url'])
        self.assertTrue(card['frame_url'])
