# -*- coding: utf-8 -*-
"""The card catalogue, and the lineups players build out of it.

    POST   /cards/ingest/                      the scraper posts here
    GET    /cards/search/                       find a card to pick
    GET    /cards/formations/                   the formation catalogue

    GET    /tournament/<t>/lineup/              my lineup, and the window
    POST   /tournament/<t>/lineup/              save it
    GET    /tournament/<t>/lineups/             the organiser's list
    GET    /tournament/<t>/lineup-rules/        the deadline, public
    POST   /tournament/<t>/lineup-rules/        the organiser sets it
    GET    /tournament/<t>/lineup/<username>/   one player's, for a broadcast
"""

import re
import unicodedata

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.models import Users
from vent_tournament.models import Tournament

from . import formations as formation_catalogue
from . import windows
from .models import GameCard, Lineup, LineupRules, LineupSlot


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, **extra):
    body = {'status': 'error', 'code': code, 'message': message, 'data': {}}
    body['data'].update(extra)
    return Response(body, status=http)


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def _tournament(key):
    if str(key).isdigit():
        found = Tournament.objects.filter(tournament_id=int(key)).first()
        if found:
            return found
    return Tournament.objects.filter(slug=str(key)).first()


def slugify_name(name):
    """A name with its accents stripped, for searching and for identity.

    Two cards sharing a slug are the SAME PERSON in different variants, which
    is what stops somebody fielding gold Mbappé and TOTY Mbappé at once.
    """
    text = unicodedata.normalize('NFKD', str(name or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


# ---------------------------------------------------------------- serialising

def serialize_card(card):
    return {
        'id': card.id,
        'source_id': card.source_id,
        'name': card.name,
        'slug': card.slug,
        'rating': card.rating,
        'position': card.position,
        'alt_positions': card.alt_positions or [],
        'club': card.club,
        'league': card.league,
        'nation': card.nation,
        'item_type': card.item_type,
        'variant': card.variant,
        'stats': card.stats or {},
        'weak_foot': card.weak_foot,
        'skill_moves': card.skill_moves,
        'price_coins': card.price_coins,
        # Both from Futbin: the portrait, and the card art behind it.
        'image_url': card.image_url,
        'frame_url': card.frame_url,
    }


def serialize_lineup(lineup):
    if lineup is None:
        return None
    return {
        'formation': lineup.formation,
        # Saving and submitting are different acts, so the state is its own
        # field: draft, submitted, accepted or rejected. `complete` is a
        # different question again - eleven cards is not the same as an answer.
        'status': lineup.status,
        'submitted_at': lineup.submitted_at,
        'reviewed_at': lineup.reviewed_at,
        'reviewed_by': (lineup.reviewed_by.username
                        if lineup.reviewed_by_id else None),
        'review_note': lineup.review_note,
        'updated_at': lineup.updated_at,
        'complete': lineup.is_complete,
        'player': lineup.user.username,
        # Each slot carries the card's own fields flattened onto it as well as
        # nested. An overlay repeating over `slots` reads a bare `name` or
        # `rating`, which is the documented rule for a repeat row; the nested
        # `card` stays for callers that want the whole object.
        'slots': [dict(
            serialize_card(s.card),
            slot_index=s.slot_index,
            position=s.position,
            card=serialize_card(s.card),
        ) for s in lineup.slots.select_related('card')],
    }


# ------------------------------------------------------------------- ingest

#: Fields a scrape may set. Anything else in a row is ignored rather than
#: refused, so a scraper that learns a new field does not need this deployed
#: first.
INGEST_FIELDS = [
    'name', 'rating', 'position', 'alt_positions', 'club', 'league', 'nation',
    'nation_id', 'item_type', 'variant', 'stats', 'weak_foot', 'skill_moves',
    'price_coins', 'image_url', 'frame_url',
]

#: A field that is missing from a scraped row leaves what is already stored
#: alone. A scrape that could not read the price must not erase the price.
#: Only these may be blanked deliberately, by sending an explicit null.
NEVER_BLANK = {'price_coins', 'image_url', 'frame_url', 'club', 'league',
               'nation', 'stats'}


def _ingest_key(request):
    return (request.headers.get('X-Cards-Key')
            or request.headers.get('X-CARDS-KEY') or '')


@api_view(['POST'])
@permission_classes([AllowAny])
def ingest(request):
    """The scraper posts a batch of cards here.

    A diff upsert: a row identical to what is stored is not written at all, so
    a delta scrape that finds nothing changed costs one read per card and no
    writes. The response says how many were added, changed and left alone,
    because a scraper with no feedback is a scraper nobody trusts.
    """
    expected = getattr(settings, 'CARDS_INGEST_KEY', '') or ''
    if not expected:
        return _err('Card ingest is not configured on this server.',
                    'INGEST_NOT_CONFIGURED', status.HTTP_503_SERVICE_UNAVAILABLE)
    if _ingest_key(request) != expected:
        return _err('That key is not right.', 'BAD_INGEST_KEY',
                    status.HTTP_401_UNAUTHORIZED)

    rows = request.data.get('cards')
    if not isinstance(rows, list):
        return _err('Send a list of cards.', 'VALIDATION_ERROR', field='cards')
    if len(rows) > 2000:
        return _err('Two thousand cards at a time at most.', 'TOO_MANY')

    source = str(request.data.get('source') or GameCard.SOURCE_FUTBIN)[:16]
    added = changed = unchanged = skipped = 0
    problems = []

    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        source_id = str(row.get('source_id') or '').strip()
        name = str(row.get('name') or '').strip()
        rating = row.get('rating')
        # Identity, a name and a rating are the minimum that makes a card. A
        # row missing one of them is reported rather than stored half-formed.
        if not source_id or not name or not rating:
            skipped += 1
            if len(problems) < 20:
                problems.append({'source_id': source_id or None,
                                 'why': 'needs source_id, name and rating'})
            continue

        card = GameCard.objects.filter(source=source, source_id=source_id).first()
        values = {}
        for field in INGEST_FIELDS:
            if field not in row:
                continue
            value = row[field]
            if value is None and field in NEVER_BLANK:
                # Absent is not the same as empty. See NEVER_BLANK.
                continue
            values[field] = value

        values['name'] = name
        values['slug'] = slugify_name(name)
        try:
            values['rating'] = int(rating)
        except (TypeError, ValueError):
            skipped += 1
            continue

        if card is None:
            values.setdefault('position', str(row.get('position') or '')[:8])
            GameCard.objects.create(source=source, source_id=source_id,
                                    last_seen_at=timezone.now(), **values)
            added += 1
            continue

        moved = [f for f, v in values.items() if getattr(card, f) != v]
        if not moved:
            # Seen, unchanged. Worth recording that it still exists.
            GameCard.objects.filter(pk=card.pk).update(last_seen_at=timezone.now())
            unchanged += 1
            continue

        for field in moved:
            setattr(card, field, values[field])
        card.last_seen_at = timezone.now()
        card.save(update_fields=moved + ['last_seen_at', 'updated_at'])
        changed += 1

    return _ok({'added': added, 'changed': changed, 'unchanged': unchanged,
                'skipped': skipped, 'problems': problems,
                'total': GameCard.objects.filter(source=source).count()},
               '%d added, %d changed, %d unchanged.' % (added, changed, unchanged))


# ------------------------------------------------------------------- search

@api_view(['GET'])
@permission_classes([AllowAny])
def search(request):
    """Find a card to put in a slot.

    Public: the catalogue is facts about a video game, and a signed-out visitor
    reading a broadcast's team sheet needs the same rows.
    """
    rows = GameCard.objects.all()

    query = str(request.GET.get('q') or '').strip()
    if query:
        rows = rows.filter(slug__icontains=slugify_name(query))

    position = str(request.GET.get('position') or '').strip().upper()
    if position:
        rows = rows.filter(position=position)

    item_type = str(request.GET.get('item_type') or '').strip().lower()
    if item_type:
        rows = rows.filter(item_type=item_type)

    for name, field in (('min_rating', 'rating__gte'),
                        ('max_rating', 'rating__lte')):
        raw = request.GET.get(name)
        if raw:
            try:
                rows = rows.filter(**{field: int(raw)})
            except (TypeError, ValueError):
                return _err('%s is a number.' % name, 'VALIDATION_ERROR',
                            field=name)

    try:
        limit = min(60, max(1, int(request.GET.get('limit') or 30)))
    except (TypeError, ValueError):
        limit = 30

    return _ok({'cards': [serialize_card(c) for c in rows[:limit]],
                'count': rows.count()})


@api_view(['GET'])
@permission_classes([AllowAny])
def formations(request):
    return _ok({'formations': formation_catalogue.catalogue(),
                'default': formation_catalogue.DEFAULT_FORMATION})
