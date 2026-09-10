"""What a reader does: pay, follow, rate, comment, mark a page, keep a place.

Split from `views_series` for length rather than for principle. Everything here
reads the same `_series_row` and `_chapter_row`, so a screen never sees a second
shape of the same thing.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import premium
from vent_auth.actors import actor_from_request
from vent_auth.models import UserWallet, Users
from vent_auth.views_notifications import create_notification

from . import access, catalogue, money
from .models import (Bookmark, ChapterComment, PromoMessage, ReaderSettings,
                     ReadingProgress, Series, SeriesFollow, SeriesRating,
                     SeriesSubscription)
from .views_series import (_chapter_row, _err, _find_chapter, _find_series,
                           _ok, _person, _series_row, _viewer)


def _need_user(request):
    user, err = actor_from_request(request)
    return user, err


@api_view(['POST'])
def chapter_buy(request, reference):
    """Pay for a chapter: early access, or a paid series."""
    user, err = _need_user(request)
    if err:
        return err
    chapter, _moved = _find_chapter(reference)
    if chapter is None:
        return _err('No such chapter.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    reason = 'early' if chapter.is_early() else 'chapter'
    try:
        row, _tx = money.buy_chapter(user, chapter, reason=reason)
    except money.PaymentError as exc:
        http = status.HTTP_400_BAD_REQUEST
        if exc.code == 'INSUFFICIENT_FUNDS':
            http = status.HTTP_402_PAYMENT_REQUIRED
        elif exc.code == 'ALREADY_BOUGHT':
            http = status.HTTP_409_CONFLICT
        return _err(exc.message, exc.code, http)

    wallet = UserWallet.objects.filter(user=user).first()
    return _ok({
        'coins': row.coins,
        'reason': row.reason,
        'balance_vc': wallet.wallet_balance if wallet else 0,
        'chapter': _chapter_row(request, chapter, user, with_pages=True),
    }, 'It is yours to read.')


@api_view(['POST'])
def series_subscribe(request, reference):
    user, err = _need_user(request)
    if err:
        return err
    series, _moved = _find_series(reference)
    if series is None:
        return _err('No such comic.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    try:
        row, _tx = money.subscribe(user, series,
                                   months=request.data.get('months') or 1)
    except money.PaymentError as exc:
        http = status.HTTP_400_BAD_REQUEST
        if exc.code == 'INSUFFICIENT_FUNDS':
            http = status.HTTP_402_PAYMENT_REQUIRED
        return _err(exc.message, exc.code, http)

    wallet = UserWallet.objects.filter(user=user).first()
    return _ok({
        'until': row.until,
        'balance_vc': wallet.wallet_balance if wallet else 0,
    }, 'You are subscribed.')


@api_view(['POST'])
def series_follow(request, reference):
    """Follow or unfollow. Free, and about being told rather than access."""
    user, err = _need_user(request)
    if err:
        return err
    series, _moved = _find_series(reference)
    if series is None:
        return _err('No such comic.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    row = SeriesFollow.objects.filter(user=user, series=series).first()
    if row:
        row.delete()
        return _ok({'following': False}, 'You are not following it any more.')
    SeriesFollow.objects.create(user=user, series=series)
    return _ok({'following': True}, 'You will be told when a chapter lands.')


@api_view(['POST'])
def series_rate(request, reference):
    user, err = _need_user(request)
    if err:
        return err
    series, _moved = _find_series(reference)
    if series is None:
        return _err('No such comic.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    try:
        stars = int(request.data.get('stars'))
    except (TypeError, ValueError):
        return _err('How many stars?', 'STARS_REQUIRED')
    if not 1 <= stars <= 5:
        return _err('One to five.', 'BAD_STARS')

    row = SeriesRating.objects.filter(user=user, series=series).first()
    if row:
        # The totals are kept on the series so a list can sort by rating
        # without joining. Changing a rating adjusts them rather than
        # recounting, which is why the old value has to come off first.
        series.rating_total = series.rating_total - row.stars + stars
        row.stars = stars
        row.save(update_fields=['stars'])
    else:
        SeriesRating.objects.create(user=user, series=series, stars=stars)
        series.rating_total += stars
        series.rating_count += 1
    series.save(update_fields=['rating_total', 'rating_count'])
    return _ok({'rating': series.rating, 'ratings': series.rating_count,
                'my_rating': stars}, 'Rated.')


@api_view(['GET', 'POST'])
def chapter_comments(request, reference):
    viewer = _viewer(request)
    chapter, _moved = _find_chapter(reference)
    if chapter is None:
        return _err('No such chapter.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'POST':
        user, err = _need_user(request)
        if err:
            return err
        body = str(request.data.get('body') or '').strip()
        if not body:
            return _err('Say something.', 'BODY_REQUIRED')
        parent = None
        if request.data.get('parent'):
            parent = ChapterComment.objects.filter(
                comment_id=request.data['parent'], chapter=chapter).first()
        row = ChapterComment.objects.create(chapter=chapter, author=user,
                                            parent=parent, body=body[:4000])
        return _ok(_comment_row(request, row), 'Said.',
                   status.HTTP_201_CREATED)

    rows = ChapterComment.objects.filter(
        chapter=chapter, is_removed=False).select_related('author')
    return _ok({'comments': [_comment_row(request, r) for r in rows]},
               'Comments.')


def _comment_row(request, row):
    return {
        'id': row.comment_id,
        'body': row.body,
        'author': _person(request, row.author),
        'parent': row.parent_id,
        'created_at': row.created_at,
    }


@api_view(['POST'])
def chapter_progress(request, reference):
    """Where somebody got to, so they can carry on later."""
    user, err = _need_user(request)
    if err:
        return err
    chapter, _moved = _find_chapter(reference)
    if chapter is None:
        return _err('No such chapter.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    page = max(1, int(request.data.get('page') or 1))
    row, _created = ReadingProgress.objects.update_or_create(
        user=user, series=chapter.series,
        defaults={'chapter': chapter, 'page_number': page})
    return _ok({'chapter': chapter.slug, 'page': row.page_number}, 'Saved.')


@api_view(['GET', 'POST', 'DELETE'])
def chapter_bookmarks(request, reference):
    user, err = _need_user(request)
    if err:
        return err
    chapter, _moved = _find_chapter(reference)
    if chapter is None:
        return _err('No such chapter.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'POST':
        page = max(1, int(request.data.get('page') or 1))
        row, created = Bookmark.objects.get_or_create(
            user=user, chapter=chapter, page_number=page,
            defaults={'note': str(request.data.get('note') or '')[:200]})
        if not created:
            row.delete()
            return _ok({'bookmarked': False}, 'Bookmark removed.')
        return _ok({'bookmarked': True, 'page': page}, 'Bookmarked.')

    if request.method == 'DELETE':
        Bookmark.objects.filter(user=user, chapter=chapter).delete()
        return _ok({'bookmarked': False}, 'Bookmarks cleared.')

    rows = Bookmark.objects.filter(user=user, chapter=chapter)
    return _ok({'bookmarks': [{'page': r.page_number, 'note': r.note}
                              for r in rows]}, 'Bookmarks.')


@api_view(['GET'])
def my_list(request):
    """Everything one reader has a relationship with, on one screen."""
    user, err = _need_user(request)
    if err:
        return err

    following = SeriesFollow.objects.filter(
        user=user).select_related('series', 'series__author')
    progress = ReadingProgress.objects.filter(
        user=user).select_related('series', 'chapter', 'series__author')
    subscriptions = SeriesSubscription.objects.filter(
        user=user).select_related('series', 'series__author')
    bookmarks = Bookmark.objects.filter(
        user=user).select_related('chapter', 'chapter__series')

    return _ok({
        'following': [_series_row(request, f.series, user) for f in following],
        'reading': [{
            'series': _series_row(request, p.series, user),
            'chapter': p.chapter.slug,
            'chapter_number': float(p.chapter.number),
            'page': p.page_number,
            'updated_at': p.updated_at,
        } for p in progress],
        'subscriptions': [{
            'series': _series_row(request, s.series, user),
            'until': s.until,
            'live': s.is_live(),
        } for s in subscriptions],
        'bookmarks': [{
            'chapter': b.chapter.slug,
            'series': b.chapter.series.slug,
            'series_title': b.chapter.series.title,
            'page': b.page_number,
            'note': b.note,
        } for b in bookmarks],
    }, 'Your list.')


@api_view(['GET', 'POST'])
def reader_settings(request):
    """How somebody likes to read. Themes are premium; modes and size are not."""
    user, err = _need_user(request)
    if err:
        return err

    row, _created = ReaderSettings.objects.get_or_create(user=user)

    if request.method == 'POST':
        mode = request.data.get('mode')
        if mode is not None:
            if mode not in catalogue.READING_MODES:
                return _err('That is not a reading mode.', 'BAD_MODE')
            row.mode = mode
        theme = request.data.get('theme')
        if theme is not None:
            if theme not in catalogue.THEMES:
                return _err('That is not a theme.', 'BAD_THEME')
            if not access.may_use_theme(user, theme):
                # Refused with the code the screen branches on, and the screen
                # says so BEFORE the press as well. Both halves.
                return _err('Themes are a premium feature.',
                            'PREMIUM_REQUIRED', status.HTTP_402_PAYMENT_REQUIRED)
            row.theme = theme
        if 'font_scale' in request.data:
            row.font_scale = max(70, min(
                int(request.data.get('font_scale') or 100), 200))
        row.save()

    return _ok({
        'mode': row.mode,
        'theme': row.theme,
        'font_scale': row.font_scale,
        'may_theme': premium.has_premium(user),
        'free_theme': catalogue.FREE_THEME,
    }, 'Reader settings.')


# ---------------------------------------------------------------------------
# The author's premium tools
# ---------------------------------------------------------------------------

@api_view(['POST'])
def series_boost(request, reference):
    """Premium: lift a comic in the browse order for a week."""
    user, err = _need_user(request)
    if err:
        return err
    series, _moved = _find_series(reference)
    if series is None:
        return _err('No such comic.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if series.author_id != user.user_id:
        return _err('That is not yours.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)
    if not premium.has_premium(user):
        return _err('Boosting is a premium feature.', 'PREMIUM_REQUIRED',
                    status.HTTP_402_PAYMENT_REQUIRED)

    from datetime import timedelta
    days = max(1, min(int(request.data.get('days') or 7), 30))
    now = timezone.now()
    # Extends rather than overwrites, so boosting twice buys two weeks.
    start = (series.boosted_until if series.boosted_until
             and series.boosted_until > now else now)
    series.boosted_until = start + timedelta(days=days)
    series.save(update_fields=['boosted_until'])
    return _ok({'boosted_until': series.boosted_until},
               'It will show first while the boost lasts.')


@api_view(['POST'])
def series_promo(request, reference):
    """Premium: write to the people subscribed to this comic."""
    user, err = _need_user(request)
    if err:
        return err
    series, _moved = _find_series(reference)
    if series is None:
        return _err('No such comic.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if series.author_id != user.user_id:
        return _err('That is not yours.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)
    if not premium.has_premium(user):
        return _err('Writing to your subscribers is a premium feature.',
                    'PREMIUM_REQUIRED', status.HTTP_402_PAYMENT_REQUIRED)

    subject = str(request.data.get('subject') or '').strip()
    body = str(request.data.get('body') or '').strip()
    if not subject or not body:
        return _err('It needs a subject and something to say.',
                    'SUBJECT_AND_BODY_REQUIRED')

    # Subscribers, and followers, because somebody following a free comic is
    # exactly who a message about it is for. Counted once per person.
    now = timezone.now()
    ids = set(SeriesSubscription.objects.filter(
        series=series, until__gt=now).values_list('user_id', flat=True))
    ids |= set(SeriesFollow.objects.filter(
        series=series).values_list('user_id', flat=True))
    ids.discard(user.user_id)

    sent = 0
    for reader in Users.objects.filter(user_id__in=ids):
        try:
            create_notification(reader, category='anime_promo',
                                title=subject[:140], body=body[:900],
                                link='/anime/manga/%s' % series.slug)
            sent += 1
        except Exception:                                   # noqa: BLE001
            pass

    row = PromoMessage.objects.create(series=series, author=user,
                                      subject=subject[:140], body=body[:4000],
                                      sent_to=sent)
    return _ok({'sent_to': row.sent_to, 'created_at': row.created_at},
               'Sent to %s reader(s).' % sent)
