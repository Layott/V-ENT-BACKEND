"""Uploading a comic, browsing comics, and reading one.

Every payload is built by `_series_row` or `_chapter_row` here, so a card, a
detail page and an author's own list carry the same field names. The fault this
avoids is the one the project rule names: a screen fed by a different shape of
the same thing, discovered when a feature works on one of them.
"""
from decimal import Decimal, InvalidOperation

from django.core.files.images import get_image_dimensions
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import premium
from vent_auth.actors import actor_from_request
from vent_auth.slugs import resolve_or_redirect
from vent_auth.views_notifications import create_notification

from . import access, catalogue, money
from .models import (AnimeAd, Bookmark, Chapter, ChapterComment, Page,
                     PromoMessage, ReaderSettings, ReadingProgress, Series,
                     SeriesFollow, SeriesRating, SeriesSubscription, Volume)


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'data': {}, 'message': message,
                     'code': code}, status=http_status)


def _viewer(request):
    """The account behind this request, or None. Never an error.

    Most of this module is readable signed out, so a missing token is a normal
    state rather than a refusal.
    """
    user, _ = actor_from_request(request)
    return user


def _media(request, field):
    if not field:
        return None
    try:
        return request.build_absolute_uri(field.url)
    except Exception:                                       # noqa: BLE001
        return None


def _person(request, user):
    from vent_auth.views_admin import _person_for_admin
    try:
        return _person_for_admin(request, user)
    except Exception:                                       # noqa: BLE001
        return {'username': user.username, 'avatar': None}


def _series_row(request, series, viewer=None, deep=False):
    """One shape for a comic, everywhere it appears."""
    row = {
        'slug': series.slug,
        'title': series.title,
        'synopsis': series.synopsis,
        'kind': series.kind,
        'kind_label': catalogue.label(catalogue.KINDS, series.kind),
        'status': series.status,
        'cover': _media(request, series.cover),
        'genres': series.genres or [],
        'tags': series.tags or [],
        'visibility': series.visibility,
        'pricing': series.pricing,
        'chapter_price_vc': series.chapter_price_vc,
        'subscription_price_vc': series.subscription_price_vc,
        'rating': series.rating,
        'ratings': series.rating_count,
        'views': series.views,
        'boosted': series.is_boosted(),
        'chapters': series.chapters.count(),
        'author': _person(request, series.author),
        'created_at': series.created_at,
        'updated_at': series.updated_at,
        # The mode this kind wants, so a manhwa opens as a strip without the
        # author having to know what a reading mode is.
        'default_mode': catalogue.DEFAULT_MODE.get(series.kind, 'single'),
        # On the ROW as well as the detail, because the author's own list is
        # where a chapter is filed into one, and a field that lands on one of
        # the two surfaces is the same bug in slower motion.
        'volumes': [{'number': v.number, 'title': v.title}
                    for v in series.volumes.all()],
    }

    if viewer is not None and viewer.is_authenticated:
        row['mine'] = series.author_id == viewer.user_id
        row['following'] = SeriesFollow.objects.filter(
            user=viewer, series=series).exists()
        subscription = SeriesSubscription.objects.filter(
            user=viewer, series=series).first()
        row['subscribed_until'] = (subscription.until
                                   if subscription and subscription.is_live()
                                   else None)
        rating = SeriesRating.objects.filter(user=viewer, series=series).first()
        row['my_rating'] = rating.stars if rating else None
        progress = ReadingProgress.objects.filter(
            user=viewer, series=series).select_related('chapter').first()
        row['continue'] = ({'chapter': progress.chapter.slug,
                            'page': progress.page_number}
                           if progress else None)
    else:
        row['mine'] = False
        row['following'] = False
        row['subscribed_until'] = None
        row['my_rating'] = None
        row['continue'] = None

    if deep:
        row['chapter_list'] = [
            _chapter_row(request, c, viewer) for c in
            series.chapters.select_related('series', 'volume').all()
        ]
    return row


def _chapter_row(request, chapter, viewer=None, with_pages=False):
    refusal = access.may_read(viewer, chapter)
    row = {
        'slug': chapter.slug,
        'number': float(chapter.number),
        'title': chapter.title,
        'volume': chapter.volume.number if chapter.volume_id else None,
        'published_at': chapter.published_at,
        'early': chapter.is_early(),
        'early_access_vc': chapter.early_access_vc,
        'pages': chapter.pages.count(),
        'series': chapter.series.slug,
        'series_title': chapter.series.title,
        # The one answer every screen asks, from the one function that decides.
        'can_read': refusal is None,
        'refusal': refusal.as_dict() if refusal else None,
    }
    if with_pages and refusal is None:
        row['page_list'] = [{
            'number': p.number,
            'image': _media(request, p.image),
            'alt': p.alt,
            'width': p.width,
            'height': p.height,
        } for p in chapter.pages.all()]
    return row


def _find_series(reference):
    return resolve_or_redirect(reference, entity_type='anime_series',
                               id_field='series_id', model=Series)


def _find_chapter(reference):
    return resolve_or_redirect(reference, entity_type='anime_chapter',
                               id_field='chapter_id', model=Chapter)


# ---------------------------------------------------------------------------
# The catalogue itself
# ---------------------------------------------------------------------------

@api_view(['GET'])
def anime_catalogue(request):
    """Genres, kinds, statuses, modes and themes, from one place.

    The frontend reads this rather than holding its own copy: the format
    catalogue drifted into five copies once already on this platform.
    """
    return _ok({
        'kinds': catalogue.KINDS,
        'statuses': catalogue.STATUSES,
        'genres': catalogue.GENRES,
        'pricing': catalogue.PRICING,
        'modes': catalogue.READING_MODES,
        'themes': catalogue.THEMES,
        'free_theme': catalogue.FREE_THEME,
        'attributes': catalogue.ATTRIBUTES,
        'room_privacy': catalogue.ROOM_PRIVACY,
        'annotation_kinds': catalogue.ANNOTATION_KINDS,
    }, 'What the anime module knows the names of.')


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
def series_list(request):
    """Browse, or upload a new comic."""
    viewer = _viewer(request)

    if request.method == 'POST':
        if viewer is None:
            return _err('You need to be signed in to do that.',
                        'NOT_AUTHENTICATED', status.HTTP_401_UNAUTHORIZED)
        title = str(request.data.get('title') or '').strip()
        if not title:
            return _err('Give it a title.', 'TITLE_REQUIRED')

        kind = request.data.get('kind') or 'manga'
        if kind not in catalogue.KINDS:
            return _err('That is not one of the kinds.', 'BAD_KIND')

        pricing = request.data.get('pricing') or 'free'
        if pricing not in catalogue.PRICING:
            return _err('That is not one of the pricing options.', 'BAD_PRICING')

        visibility = request.data.get('visibility') or 'private'
        if visibility not in catalogue.VISIBILITY:
            return _err('That is not one of the visibilities.', 'BAD_VISIBILITY')

        genres = request.data.get('genres') or []
        if isinstance(genres, str):
            genres = [g.strip() for g in genres.split(',') if g.strip()]
        unknown = [g for g in genres if g not in catalogue.GENRES]
        if unknown:
            return _err('Unknown genre: %s' % unknown[0], 'BAD_GENRE')

        tags = request.data.get('tags') or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]

        series = Series(
            author=viewer, title=title[:160],
            synopsis=str(request.data.get('synopsis') or '')[:4000],
            kind=kind, pricing=pricing, visibility=visibility,
            status=request.data.get('status') or 'ongoing',
            genres=genres[:8], tags=[str(t)[:40] for t in tags][:12],
            chapter_price_vc=int(request.data.get('chapter_price_vc') or 0),
            subscription_price_vc=int(
                request.data.get('subscription_price_vc') or 0),
        )
        if request.FILES.get('cover'):
            series.cover = request.FILES['cover']
        series.save()
        return _ok(_series_row(request, series, viewer),
                   'Your comic is created.', status.HTTP_201_CREATED)

    qs = Series.objects.select_related('author')

    mine = str(request.GET.get('mine') or '').lower() in ('1', 'true', 'yes')
    if mine:
        if viewer is None:
            return _err('You need to be signed in to do that.',
                        'NOT_AUTHENTICATED', status.HTTP_401_UNAUTHORIZED)
        qs = qs.filter(author=viewer)
    else:
        # Public means public. A private comic is invisible to everybody but
        # its author, in the API, not only on the screen.
        qs = qs.filter(visibility='public')

    search = str(request.GET.get('q') or '').strip()
    if search:
        qs = qs.filter(Q(title__icontains=search)
                       | Q(synopsis__icontains=search))

    genre = request.GET.get('genre')
    if genre:
        qs = qs.filter(genres__icontains=genre)
    tag = request.GET.get('tag')
    if tag:
        qs = qs.filter(tags__icontains=tag)
    state = request.GET.get('status')
    if state in catalogue.STATUSES:
        qs = qs.filter(status=state)
    kind = request.GET.get('kind')
    if kind in catalogue.KINDS:
        qs = qs.filter(kind=kind)

    rows = [_series_row(request, s, viewer) for s in qs[:200]]

    sort = request.GET.get('sort') or 'rating'
    if sort == 'newest':
        rows.sort(key=lambda r: r['created_at'], reverse=True)
    elif sort == 'views':
        rows.sort(key=lambda r: r['views'], reverse=True)
    elif sort == 'title':
        rows.sort(key=lambda r: r['title'].lower())
    else:
        # Unrated last rather than first: a comic nobody has rated is not a
        # badly rated one, and sorting them together buries everything new.
        rows.sort(key=lambda r: (r['rating'] is None, -(r['rating'] or 0)))

    # A boost lifts a comic in the ORDER, which is a real effect on a real
    # list, rather than drawing a badge on it. It never changes the numbers.
    rows.sort(key=lambda r: not r['boosted'])

    return _ok({'series': rows, 'count': len(rows)}, 'Comics.')


@api_view(['GET', 'PATCH', 'DELETE'])
def series_detail(request, reference):
    viewer = _viewer(request)
    series, moved = _find_series(reference)
    if moved:
        return _ok({'url': '/anime/manga/%s' % moved}, 'moved')
    if series is None:
        return _err('No such comic.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        if not access.may_see_series(viewer, series):
            return _err('No such comic.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        # Counted once per read of the detail, which is the closest thing to a
        # view this has. Not on the list, or browsing would inflate it.
        Series.objects.filter(pk=series.pk).update(views=series.views + 1)
        series.views += 1
        return _ok(_series_row(request, series, viewer, deep=True), 'A comic.')

    if viewer is None or series.author_id != viewer.user_id:
        return _err('That is not yours.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    if request.method == 'DELETE':
        series.delete()
        return _ok({}, 'Deleted.')

    # Merge from what is STORED, then overlay the request, so a screen sending
    # one field does not wipe the rest. The marketplace edit shipped the other
    # way round and asked for a price nobody was changing.
    for field in ('title', 'synopsis', 'status', 'kind', 'pricing',
                  'visibility'):
        if field in request.data:
            value = request.data.get(field)
            if field == 'kind' and value not in catalogue.KINDS:
                return _err('That is not one of the kinds.', 'BAD_KIND')
            if field == 'pricing' and value not in catalogue.PRICING:
                return _err('That is not one of the pricing options.',
                            'BAD_PRICING')
            if field == 'visibility' and value not in catalogue.VISIBILITY:
                return _err('That is not one of the visibilities.',
                            'BAD_VISIBILITY')
            setattr(series, field, value)
    for field in ('chapter_price_vc', 'subscription_price_vc'):
        if field in request.data:
            setattr(series, field, max(0, int(request.data.get(field) or 0)))
    if 'genres' in request.data:
        genres = request.data.get('genres') or []
        if isinstance(genres, str):
            genres = [g.strip() for g in genres.split(',') if g.strip()]
        series.genres = [g for g in genres if g in catalogue.GENRES][:8]
    if 'tags' in request.data:
        tags = request.data.get('tags') or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]
        series.tags = [str(t)[:40] for t in tags][:12]
    if request.FILES.get('cover'):
        series.cover = request.FILES['cover']
    series.save()
    return _ok(_series_row(request, series, viewer, deep=True), 'Saved.')


@api_view(['POST'])
def series_volumes(request, reference):
    viewer = _viewer(request)
    series, _moved = _find_series(reference)
    if series is None:
        return _err('No such comic.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if viewer is None or series.author_id != viewer.user_id:
        return _err('That is not yours.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)
    number = int(request.data.get('number') or 0)
    if number <= 0:
        return _err('Which volume?', 'NUMBER_REQUIRED')
    volume, created = Volume.objects.get_or_create(
        series=series, number=number,
        defaults={'title': str(request.data.get('title') or '')[:160]})
    if not created and 'title' in request.data:
        volume.title = str(request.data.get('title') or '')[:160]
        volume.save(update_fields=['title'])
    return _ok({'number': volume.number, 'title': volume.title},
               'Volume saved.')


@api_view(['POST'])
def series_chapters(request, reference):
    """Upload a chapter, with its pages, as real files."""
    viewer = _viewer(request)
    series, _moved = _find_series(reference)
    if series is None:
        return _err('No such comic.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if viewer is None or series.author_id != viewer.user_id:
        return _err('That is not yours.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    try:
        number = Decimal(str(request.data.get('number') or '')).quantize(
            Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return _err('Which chapter number?', 'NUMBER_REQUIRED')
    if Chapter.objects.filter(series=series, number=number).exists():
        return _err('There is already a chapter %s.' % number,
                    'CHAPTER_EXISTS')

    volume = None
    if request.data.get('volume'):
        volume = Volume.objects.filter(
            series=series, number=int(request.data['volume'])).first()

    published_at = _as_instant(request.data.get('published_at'))
    chapter = Chapter(
        series=series, volume=volume, number=number,
        title=str(request.data.get('title') or '')[:200],
        published_at=published_at or None,
        early_access_vc=max(0, int(request.data.get('early_access_vc') or 0)),
    )
    chapter.save()

    files = request.FILES.getlist('pages')
    for index, image in enumerate(files, start=1):
        width = height = 0
        try:
            width, height = get_image_dimensions(image)
        except Exception:                                   # noqa: BLE001
            pass
        Page.objects.create(chapter=chapter, number=index, image=image,
                            alt=str(request.data.get('alt_%s' % index) or '')[:300],
                            width=width or 0, height=height or 0)

    # Everybody following the comic is told, through the platform's own
    # notifications rather than a second system.
    if series.visibility == 'public' and not chapter.is_early():
        for follow in SeriesFollow.objects.filter(
                series=series).select_related('user'):
            try:
                create_notification(
                    follow.user,
                    category='anime_chapter',
                    title='%s #%s' % (series.title, chapter.number),
                    body='A new chapter is out.',
                    link='/anime/read/%s' % chapter.slug)
            except Exception:                               # noqa: BLE001
                # A missed notification is a missed line in a list. A failed
                # upload is somebody's work lost.
                pass

    return _ok(_chapter_row(request, chapter, viewer),
               'Chapter uploaded.', status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
def chapter_detail(request, reference):
    """Read a chapter, or edit your own."""
    viewer = _viewer(request)
    chapter, moved = _find_chapter(reference)
    if moved:
        return _ok({'url': '/anime/read/%s' % moved}, 'moved')
    if chapter is None:
        return _err('No such chapter.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        refusal = access.may_read(viewer, chapter)
        if refusal and refusal.code == 'NOT_FOUND':
            return _err('No such chapter.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        data = _chapter_row(request, chapter, viewer, with_pages=True)
        data['series_row'] = _series_row(request, chapter.series, viewer)
        data['neighbours'] = _neighbours(chapter)
        data['ads'] = _ads(request, viewer)
        if refusal:
            # 200 with the refusal named, not a 403: the screen has to draw the
            # cover, the title and what it would cost, and a refusal with no
            # body is a screen that can only say "no".
            return _ok(data, 'You cannot read that one yet.')
        return _ok(data, 'A chapter.')

    if viewer is None or chapter.series.author_id != viewer.user_id:
        return _err('That is not yours.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    if request.method == 'DELETE':
        chapter.delete()
        return _ok({}, 'Deleted.')

    if 'title' in request.data:
        chapter.title = str(request.data.get('title') or '')[:200]
    if 'published_at' in request.data:
        chapter.published_at = _as_instant(request.data.get('published_at'))
    if 'early_access_vc' in request.data:
        chapter.early_access_vc = max(
            0, int(request.data.get('early_access_vc') or 0))
    chapter.save()
    return _ok(_chapter_row(request, chapter, viewer), 'Saved.')


def _as_instant(value):
    """A date from a request, as an aware datetime, or None.

    Django would coerce this on save, but the instance in memory keeps whatever
    it was given, and `Chapter.is_early()` is asked BEFORE the next read: on the
    upload path that compared a string to a datetime and raised. So it is parsed
    here, once, at the edge.

    Naive input is read as UTC rather than guessed at. The browser sends an
    instant through `localInputToISO`, which is the rule; anything naive that
    reaches here came from a script rather than a person.
    """
    if not value:
        return None
    if hasattr(value, 'tzinfo'):
        parsed = value
    else:
        from django.utils.dateparse import parse_datetime
        parsed = parse_datetime(str(value))
        if parsed is None:
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def _neighbours(chapter):
    """The chapter before and after, so the reader can turn the page."""
    before = Chapter.objects.filter(
        series_id=chapter.series_id,
        number__lt=chapter.number).order_by('-number').first()
    after = Chapter.objects.filter(
        series_id=chapter.series_id,
        number__gt=chapter.number).order_by('number').first()
    return {
        'previous': before.slug if before else None,
        'next': after.slug if after else None,
    }


def _ads(request, viewer):
    """What to show this reader. Empty for premium, and empty when there are none.

    Never a placeholder: where there are no rows the reader renders nothing at
    all rather than a grey box labelled "ad".
    """
    if not access.shows_ads(viewer):
        return []
    return [{
        'title': ad.title,
        'image': _media(request, ad.image),
        'url': ad.url,
    } for ad in AnimeAd.objects.filter(is_active=True)[:3]]
