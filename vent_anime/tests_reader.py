"""What a reader does, and what premium changes.

The premium half is the part worth holding: a theme is gated and the reading
modes are NOT, because the modes are how somebody reads at all and the look is a
decoration. Getting that backwards would put a paywall in front of a manhwa
being readable.
"""
import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Notification, UserWallet, Users

from .models import (AnimeAd, Bookmark, Chapter, ChapterComment,
                     ReaderSettings, ReadingProgress, Series, SeriesFollow,
                     SeriesRating, SeriesSubscription)


def make_user(i, *, coins=0, is_premium=False):
    user = Users.objects.create(
        username='ax%s' % i, email='ax%s@test.co' % i,
        login_session_token='axt%s' % str(i).zfill(11),
        login_session_created_at=timezone.now(), is_active=True,
        is_premium=is_premium)
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user,
                              wallet_balance=coins)
    return user


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return c


@override_settings(ANIME_ENABLED=True)
class FollowingAndRatingTests(TestCase):

    def setUp(self):
        self.author = make_user(1)
        self.reader = make_user(2)
        self.series = Series.objects.create(
            author=self.author, title='Follow me', visibility='public')
        self.chapter = Chapter.objects.create(series=self.series, number=1)

    def test_following_is_a_toggle(self):
        url = '/anime/series/%s/follow/' % self.series.slug
        res = client_for(self.reader).post(url, {}, format='json')
        self.assertTrue(res.data['data']['following'])
        res = client_for(self.reader).post(url, {}, format='json')
        self.assertFalse(res.data['data']['following'])
        self.assertEqual(SeriesFollow.objects.count(), 0)

    def test_a_follower_is_told_when_a_chapter_lands(self):
        SeriesFollow.objects.create(user=self.reader, series=self.series)
        client_for(self.author).post(
            '/anime/series/%s/chapters/' % self.series.slug, {'number': '2'},
            format='multipart')
        self.assertTrue(Notification.objects.filter(
            user=self.reader, category='anime_chapter').exists())

    def test_nobody_is_told_about_a_chapter_that_is_not_out(self):
        SeriesFollow.objects.create(user=self.reader, series=self.series)
        client_for(self.author).post(
            '/anime/series/%s/chapters/' % self.series.slug,
            {'number': '3',
             'published_at': (timezone.now() + timedelta(days=2)).isoformat()},
            format='multipart')
        self.assertFalse(Notification.objects.filter(
            user=self.reader, category='anime_chapter').exists())

    def test_a_rating_is_one_to_five(self):
        url = '/anime/series/%s/rate/' % self.series.slug
        self.assertEqual(
            client_for(self.reader).post(url, {'stars': 6},
                                         format='json').status_code, 400)
        res = client_for(self.reader).post(url, {'stars': 4}, format='json')
        self.assertEqual(res.data['data']['rating'], 4.0)

    def test_changing_a_rating_does_not_count_twice(self):
        url = '/anime/series/%s/rate/' % self.series.slug
        client_for(self.reader).post(url, {'stars': 5}, format='json')
        res = client_for(self.reader).post(url, {'stars': 1}, format='json')
        self.assertEqual(res.data['data']['ratings'], 1)
        self.assertEqual(res.data['data']['rating'], 1.0)

    def test_an_unrated_comic_has_no_rating_rather_than_zero(self):
        """Zero would bury everything new under everything mediocre."""
        res = APIClient().get('/anime/series/')
        self.assertIsNone(res.data['data']['series'][0]['rating'])


@override_settings(ANIME_ENABLED=True)
class KeepingYourPlaceTests(TestCase):

    def setUp(self):
        self.author = make_user(3)
        self.reader = make_user(4)
        self.series = Series.objects.create(
            author=self.author, title='Long one', visibility='public')
        self.one = Chapter.objects.create(series=self.series, number=1)
        self.two = Chapter.objects.create(series=self.series, number=2)

    def test_progress_is_per_series_not_per_chapter(self):
        client_for(self.reader).post(
            '/anime/chapters/%s/progress/' % self.one.slug, {'page': 3},
            format='json')
        client_for(self.reader).post(
            '/anime/chapters/%s/progress/' % self.two.slug, {'page': 1},
            format='json')
        self.assertEqual(ReadingProgress.objects.count(), 1)
        row = ReadingProgress.objects.get()
        self.assertEqual(row.chapter_id, self.two.chapter_id)

    def test_the_series_says_where_to_carry_on(self):
        client_for(self.reader).post(
            '/anime/chapters/%s/progress/' % self.two.slug, {'page': 7},
            format='json')
        res = client_for(self.reader).get(
            '/anime/series/%s/' % self.series.slug)
        self.assertEqual(res.data['data']['continue'],
                         {'chapter': self.two.slug, 'page': 7})

    def test_a_bookmark_is_a_toggle_on_a_page(self):
        url = '/anime/chapters/%s/bookmarks/' % self.one.slug
        client_for(self.reader).post(url, {'page': 5, 'note': 'that panel'},
                                     format='json')
        self.assertEqual(Bookmark.objects.count(), 1)
        client_for(self.reader).post(url, {'page': 5}, format='json')
        self.assertEqual(Bookmark.objects.count(), 0)

    def test_my_list_gathers_everything_in_one_answer(self):
        SeriesFollow.objects.create(user=self.reader, series=self.series)
        client_for(self.reader).post(
            '/anime/chapters/%s/progress/' % self.one.slug, {'page': 2},
            format='json')
        client_for(self.reader).post(
            '/anime/chapters/%s/bookmarks/' % self.one.slug, {'page': 4},
            format='json')
        res = client_for(self.reader).get('/anime/my-list/')
        data = res.data['data']
        self.assertEqual(len(data['following']), 1)
        self.assertEqual(len(data['reading']), 1)
        self.assertEqual(len(data['bookmarks']), 1)

    def test_my_list_needs_an_account(self):
        self.assertEqual(APIClient().get('/anime/my-list/').status_code, 401)


@override_settings(ANIME_ENABLED=True)
class CommentingTests(TestCase):

    def setUp(self):
        self.author = make_user(5)
        self.reader = make_user(6)
        self.series = Series.objects.create(
            author=self.author, title='Talkative', visibility='public')
        self.chapter = Chapter.objects.create(series=self.series, number=1)

    def test_a_comment_lands_on_the_chapter(self):
        res = client_for(self.reader).post(
            '/anime/chapters/%s/comments/' % self.chapter.slug,
            {'body': 'that ending'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(ChapterComment.objects.count(), 1)

    def test_a_reply_knows_its_parent(self):
        first = client_for(self.reader).post(
            '/anime/chapters/%s/comments/' % self.chapter.slug,
            {'body': 'top'}, format='json')
        client_for(self.author).post(
            '/anime/chapters/%s/comments/' % self.chapter.slug,
            {'body': 'reply', 'parent': first.data['data']['id']},
            format='json')
        self.assertEqual(ChapterComment.objects.filter(
            parent__isnull=False).count(), 1)

    def test_a_stranger_reads_but_does_not_write(self):
        self.assertEqual(APIClient().get(
            '/anime/chapters/%s/comments/' % self.chapter.slug).status_code,
            200)
        self.assertEqual(APIClient().post(
            '/anime/chapters/%s/comments/' % self.chapter.slug,
            {'body': 'hi'}, format='json').status_code, 401)


@override_settings(ANIME_ENABLED=True)
class PremiumTests(TestCase):

    def setUp(self):
        self.free = make_user(7)
        self.paid = make_user(8, is_premium=True)
        self.series = Series.objects.create(
            author=self.paid, title='Boosted', visibility='public')
        self.free_series = Series.objects.create(
            author=self.free, title='Not boosted', visibility='public')

    def test_reading_modes_are_free(self):
        res = client_for(self.free).post('/anime/reader-settings/',
                                         {'mode': 'vertical'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(ReaderSettings.objects.get(user=self.free).mode,
                         'vertical')

    def test_the_font_size_is_free(self):
        res = client_for(self.free).post('/anime/reader-settings/',
                                         {'font_scale': 130}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['font_scale'], 130)

    def test_a_theme_is_premium(self):
        res = client_for(self.free).post('/anime/reader-settings/',
                                         {'theme': 'sepia'}, format='json')
        self.assertEqual(res.status_code, 402)
        self.assertEqual(res.data['code'], 'PREMIUM_REQUIRED')

    def test_the_free_theme_is_always_allowed(self):
        res = client_for(self.free).post('/anime/reader-settings/',
                                         {'theme': 'dark'}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_premium_may_choose_a_theme(self):
        res = client_for(self.paid).post('/anime/reader-settings/',
                                         {'theme': 'sepia'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['theme'], 'sepia')

    def test_the_screen_is_told_before_the_press(self):
        """Both halves: the API refuses AND the payload says whether to offer it."""
        res = client_for(self.free).get('/anime/reader-settings/')
        self.assertFalse(res.data['data']['may_theme'])
        res = client_for(self.paid).get('/anime/reader-settings/')
        self.assertTrue(res.data['data']['may_theme'])

    def test_boosting_needs_premium(self):
        res = client_for(self.free).post(
            '/anime/series/%s/boost/' % self.free_series.slug, {},
            format='json')
        self.assertEqual(res.status_code, 402)

    def test_a_boost_lifts_the_comic_in_the_list(self):
        client_for(self.paid).post(
            '/anime/series/%s/boost/' % self.series.slug, {'days': 3},
            format='json')
        res = APIClient().get('/anime/series/?sort=title')
        self.assertEqual(res.data['data']['series'][0]['title'], 'Boosted')

    def test_boosting_twice_extends_it(self):
        url = '/anime/series/%s/boost/' % self.series.slug
        client_for(self.paid).post(url, {'days': 3}, format='json')
        first = Series.objects.get(pk=self.series.pk).boosted_until
        client_for(self.paid).post(url, {'days': 3}, format='json')
        second = Series.objects.get(pk=self.series.pk).boosted_until
        self.assertAlmostEqual((second - first).days, 3, delta=1)

    def test_only_the_author_boosts_their_own(self):
        other = make_user(9, is_premium=True)
        res = client_for(other).post(
            '/anime/series/%s/boost/' % self.series.slug, {}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_a_promo_reaches_subscribers_and_followers(self):
        reader = make_user(10)
        SeriesFollow.objects.create(user=reader, series=self.series)
        res = client_for(self.paid).post(
            '/anime/series/%s/promo/' % self.series.slug,
            {'subject': 'Chapter 9 is up', 'body': 'Read it now'},
            format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['sent_to'], 1)
        self.assertTrue(Notification.objects.filter(
            user=reader, category='anime_promo').exists())

    def test_a_promo_needs_premium(self):
        res = client_for(self.free).post(
            '/anime/series/%s/promo/' % self.free_series.slug,
            {'subject': 'x', 'body': 'y'}, format='json')
        self.assertEqual(res.status_code, 402)

    def test_a_promo_needs_something_to_say(self):
        res = client_for(self.paid).post(
            '/anime/series/%s/promo/' % self.series.slug, {'subject': 'only'},
            format='json')
        self.assertEqual(res.status_code, 400)


@override_settings(ANIME_ENABLED=True)
class AdvertisementTests(TestCase):

    def setUp(self):
        self.author = make_user(11)
        self.free = make_user(12)
        self.paid = make_user(13, is_premium=True)
        self.series = Series.objects.create(
            author=self.author, title='With ads', visibility='public')
        self.chapter = Chapter.objects.create(series=self.series, number=1)

    def test_with_no_ads_at_all_nobody_sees_a_slot(self):
        """Never a placeholder. An empty grey box is a shipped placeholder."""
        res = client_for(self.free).get(
            '/anime/chapters/%s/' % self.chapter.slug)
        self.assertEqual(res.data['data']['ads'], [])

    def test_a_free_reader_sees_a_real_ad(self):
        AnimeAd.objects.create(title='Buy a controller', url='https://x.test')
        res = client_for(self.free).get(
            '/anime/chapters/%s/' % self.chapter.slug)
        self.assertEqual(len(res.data['data']['ads']), 1)

    def test_premium_sees_none(self):
        AnimeAd.objects.create(title='Buy a controller')
        res = client_for(self.paid).get(
            '/anime/chapters/%s/' % self.chapter.slug)
        self.assertEqual(res.data['data']['ads'], [])

    def test_an_inactive_ad_is_not_shown(self):
        AnimeAd.objects.create(title='Old campaign', is_active=False)
        res = client_for(self.free).get(
            '/anime/chapters/%s/' % self.chapter.slug)
        self.assertEqual(res.data['data']['ads'], [])
