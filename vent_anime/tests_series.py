"""Uploading a comic, and who may read a chapter of it.

`access.may_read` is the one function every screen asks, so most of what is
here is that function against the five ways in and the four refusals. The rest
is the upload path and the fact that a private comic is invisible in the API
rather than only on the screen.
"""
import tempfile
import uuid
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import UserWallet, Users

from . import access
from .models import (Chapter, ChapterPurchase, Page, Series,
                     SeriesSubscription)


def make_user(i, *, coins=0):
    user = Users.objects.create(
        username='an%s' % i, email='an%s@test.co' % i,
        login_session_token='ant%s' % str(i).zfill(11),
        login_session_created_at=timezone.now(), is_active=True)
    UserWallet.objects.create(user_wallet_id=uuid.uuid4().hex[:10], user=user,
                              wallet_balance=coins)
    return user


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION='Bearer %s' % user.login_session_token)
    return c


def a_png():
    """The smallest real PNG, so an upload test uploads an actual image."""
    raw = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00'
           b'\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc'
           b'\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`'
           b'\x82')
    return SimpleUploadedFile('page.png', raw, content_type='image/png')


#: Uploads go to a temporary directory, never the live MEDIA_ROOT. Old test
#: runs left 16,418 one-pixel files in `media/` and made local walks look
#: broken, which is inbox row 231.
@override_settings(ANIME_ENABLED=True, MEDIA_ROOT=tempfile.mkdtemp())
class UploadingTests(TestCase):

    def setUp(self):
        self.author = make_user(1)
        self.client = client_for(self.author)

    def test_a_comic_is_created_with_a_slug_from_its_title(self):
        res = self.client.post('/anime/series/', {
            'title': 'The Lagos Job', 'kind': 'manga',
            'genres': 'action,drama', 'visibility': 'public',
        }, format='json')
        self.assertEqual(res.status_code, 201)
        series = Series.objects.get()
        self.assertEqual(series.slug, 'the-lagos-job')
        self.assertEqual(series.genres, ['action', 'drama'])

    def test_a_title_is_required(self):
        res = self.client.post('/anime/series/', {'kind': 'manga'},
                               format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'TITLE_REQUIRED')

    def test_an_unknown_genre_is_refused_rather_than_stored(self):
        res = self.client.post('/anime/series/', {
            'title': 'X', 'genres': 'not-a-genre'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'BAD_GENRE')

    def test_a_comic_starts_private(self):
        self.client.post('/anime/series/', {'title': 'Quiet'}, format='json')
        self.assertEqual(Series.objects.get().visibility, 'private')

    def test_a_chapter_takes_real_image_files(self):
        self.client.post('/anime/series/', {'title': 'Pages'}, format='json')
        series = Series.objects.get()
        res = self.client.post(
            '/anime/series/%s/chapters/' % series.slug,
            {'number': '1', 'title': 'The first one',
             'pages': [a_png(), a_png()]}, format='multipart')
        self.assertEqual(res.status_code, 201)
        chapter = Chapter.objects.get()
        self.assertEqual(Page.objects.filter(chapter=chapter).count(), 2)
        self.assertEqual([p.number for p in chapter.pages.all()], [1, 2])

    def test_two_chapters_cannot_share_a_number(self):
        self.client.post('/anime/series/', {'title': 'Twice'}, format='json')
        series = Series.objects.get()
        self.client.post('/anime/series/%s/chapters/' % series.slug,
                         {'number': '1'}, format='multipart')
        res = self.client.post('/anime/series/%s/chapters/' % series.slug,
                               {'number': '1'}, format='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'CHAPTER_EXISTS')

    def test_a_volume_groups_chapters(self):
        self.client.post('/anime/series/', {'title': 'Bound'}, format='json')
        series = Series.objects.get()
        self.client.post('/anime/series/%s/volumes/' % series.slug,
                         {'number': 1, 'title': 'Book one'}, format='json')
        res = self.client.post('/anime/series/%s/chapters/' % series.slug,
                               {'number': '1', 'volume': 1}, format='multipart')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Chapter.objects.get().volume.title, 'Book one')

    def test_somebody_else_cannot_add_a_chapter(self):
        self.client.post('/anime/series/', {'title': 'Mine'}, format='json')
        series = Series.objects.get()
        stranger = make_user(2)
        res = client_for(stranger).post(
            '/anime/series/%s/chapters/' % series.slug, {'number': '1'},
            format='multipart')
        self.assertEqual(res.status_code, 403)

    def test_editing_one_field_does_not_wipe_the_rest(self):
        """The marketplace shipped the other way round once."""
        self.client.post('/anime/series/', {
            'title': 'Keep', 'synopsis': 'A long synopsis',
            'pricing': 'per_chapter', 'chapter_price_vc': 5}, format='json')
        series = Series.objects.get()
        res = self.client.patch('/anime/series/%s/' % series.slug,
                                {'title': 'Kept'}, format='json')
        self.assertEqual(res.status_code, 200)
        series.refresh_from_db()
        self.assertEqual(series.title, 'Kept')
        self.assertEqual(series.synopsis, 'A long synopsis')
        self.assertEqual(series.chapter_price_vc, 5)

    def test_a_rename_keeps_the_old_address_working(self):
        self.client.post('/anime/series/', {'title': 'Before',
                                            'visibility': 'public'},
                         format='json')
        series = Series.objects.get()
        old = series.slug
        self.client.patch('/anime/series/%s/' % old, {'title': 'After'},
                          format='json')
        res = self.client.get('/anime/series/%s/' % old)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['message'], 'moved')
        self.assertIn('after', res.data['data']['url'])


@override_settings(ANIME_ENABLED=True)
class WhoMayReadTests(TestCase):

    def setUp(self):
        self.author = make_user(3)
        self.reader = make_user(4, coins=100)
        self.series = Series.objects.create(
            author=self.author, title='Readable', visibility='public',
            pricing='free')
        self.chapter = Chapter.objects.create(series=self.series, number=1)

    def test_a_free_public_chapter_opens(self):
        self.assertIsNone(access.may_read(self.reader, self.chapter))

    def test_a_stranger_signed_out_can_read_a_free_chapter(self):
        self.assertIsNone(access.may_read(None, self.chapter))

    def test_a_private_comic_is_not_found_rather_than_refused(self):
        """A refusal that admits it is there is a leak."""
        self.series.visibility = 'private'
        self.series.save(update_fields=['visibility'])
        refusal = access.may_read(self.reader, self.chapter)
        self.assertEqual(refusal.code, 'NOT_FOUND')

    def test_the_author_reads_their_own_private_comic(self):
        self.series.visibility = 'private'
        self.series.save(update_fields=['visibility'])
        self.assertIsNone(access.may_read(self.author, self.chapter))

    def test_a_paid_chapter_names_its_price(self):
        self.series.pricing = 'per_chapter'
        self.series.chapter_price_vc = 7
        self.series.save(update_fields=['pricing', 'chapter_price_vc'])
        refusal = access.may_read(self.reader, self.chapter)
        self.assertEqual(refusal.code, 'CHAPTER_REQUIRED')
        self.assertEqual(refusal.needs_coins, 7)

    def test_buying_a_chapter_opens_it(self):
        self.series.pricing = 'per_chapter'
        self.series.chapter_price_vc = 7
        self.series.save(update_fields=['pricing', 'chapter_price_vc'])
        ChapterPurchase.objects.create(user=self.reader, chapter=self.chapter,
                                       coins=7)
        self.assertIsNone(access.may_read(self.reader, self.chapter))

    def test_a_subscription_opens_the_whole_series(self):
        self.series.pricing = 'subscription'
        self.series.subscription_price_vc = 20
        self.series.save(update_fields=['pricing', 'subscription_price_vc'])
        self.assertEqual(
            access.may_read(self.reader, self.chapter).code,
            'SUBSCRIPTION_REQUIRED')
        SeriesSubscription.objects.create(
            user=self.reader, series=self.series,
            until=timezone.now() + timedelta(days=1))
        self.assertIsNone(access.may_read(self.reader, self.chapter))

    def test_a_lapsed_subscription_does_not(self):
        self.series.pricing = 'subscription'
        self.series.save(update_fields=['pricing'])
        SeriesSubscription.objects.create(
            user=self.reader, series=self.series,
            until=timezone.now() - timedelta(minutes=1))
        self.assertEqual(access.may_read(self.reader, self.chapter).code,
                         'SUBSCRIPTION_REQUIRED')

    def test_a_chapter_ahead_of_its_date_asks_for_early_access(self):
        self.chapter.published_at = timezone.now() + timedelta(days=3)
        self.chapter.early_access_vc = 4
        self.chapter.save(update_fields=['published_at', 'early_access_vc'])
        refusal = access.may_read(self.reader, self.chapter)
        self.assertEqual(refusal.code, 'EARLY_ACCESS_REQUIRED')
        self.assertEqual(refusal.needs_coins, 4)

    def test_an_unreleased_chapter_with_no_early_price_opens_to_nobody(self):
        self.chapter.published_at = timezone.now() + timedelta(days=3)
        self.chapter.save(update_fields=['published_at'])
        self.assertEqual(access.may_read(self.reader, self.chapter).code,
                         'NOT_OUT_YET')

    def test_the_date_passing_opens_it_to_everybody(self):
        self.chapter.published_at = timezone.now() - timedelta(minutes=1)
        self.chapter.early_access_vc = 4
        self.chapter.save(update_fields=['published_at', 'early_access_vc'])
        self.assertIsNone(access.may_read(self.reader, self.chapter))

    def test_an_unknown_pricing_mode_refuses_rather_than_opens(self):
        """A typo in a column must never open a paid chapter."""
        self.series.pricing = 'gibberish'
        self.series.save(update_fields=['pricing'])
        self.assertEqual(access.may_read(self.reader, self.chapter).code,
                         'NOT_AVAILABLE')


@override_settings(ANIME_ENABLED=True)
class BrowsingTests(TestCase):

    def setUp(self):
        self.author = make_user(5)
        self.other = make_user(6)
        Series.objects.create(author=self.author, title='Public one',
                              visibility='public', genres=['action'])
        Series.objects.create(author=self.author, title='Private one',
                              visibility='private')

    def test_the_list_shows_only_public_comics(self):
        res = APIClient().get('/anime/series/')
        titles = [s['title'] for s in res.data['data']['series']]
        self.assertEqual(titles, ['Public one'])

    def test_an_author_can_ask_for_their_own(self):
        res = client_for(self.author).get('/anime/series/?mine=1')
        titles = sorted(s['title'] for s in res.data['data']['series'])
        self.assertEqual(titles, ['Private one', 'Public one'])

    def test_asking_for_mine_signed_out_is_refused(self):
        res = APIClient().get('/anime/series/?mine=1')
        self.assertEqual(res.status_code, 401)

    def test_the_genre_filter_narrows(self):
        res = APIClient().get('/anime/series/?genre=action')
        self.assertEqual(len(res.data['data']['series']), 1)
        res = APIClient().get('/anime/series/?genre=horror')
        self.assertEqual(len(res.data['data']['series']), 0)

    def test_search_looks_at_the_title(self):
        res = APIClient().get('/anime/series/?q=public')
        self.assertEqual(len(res.data['data']['series']), 1)

    def test_a_boosted_comic_comes_first(self):
        quiet = Series.objects.create(author=self.other, title='Aaa quiet',
                                      visibility='public')
        loud = Series.objects.create(
            author=self.other, title='Zzz loud', visibility='public',
            boosted_until=timezone.now() + timedelta(days=1))
        res = APIClient().get('/anime/series/?sort=title')
        titles = [s['title'] for s in res.data['data']['series']]
        self.assertEqual(titles[0], loud.title)
        self.assertIn(quiet.title, titles)

    def test_a_private_comic_is_a_404_by_address(self):
        private = Series.objects.get(title='Private one')
        res = APIClient().get('/anime/series/%s/' % private.slug)
        self.assertEqual(res.status_code, 404)
