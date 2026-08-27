"""Serialization helpers for the vent_event app.

The V-ENT backend serializes with plain functions returning dicts (see
`vent_tournament.views`) rather than DRF ModelSerializers, because most payloads
compose derived fields (computed status, absolute media URLs, ticket availability).
These helpers keep that house style but centralize event shaping so the listing,
detail, and create endpoints all emit an identical Event object.
"""

from django.utils import timezone


def absolute_media_url(request, file_field, fallback_url=None):
    """Return an absolute URL for an ImageField, or a stored external URL fallback."""
    if file_field:
        try:
            url = file_field.url
        except ValueError:
            url = None
        if url:
            return request.build_absolute_uri(url) if request else url
    return fallback_url or None


def event_status(event):
    """Derive lifecycle status from the canonical start/end datetimes."""
    now = timezone.now()
    start = event.start_date
    end = event.end_date
    if start and end:
        if now < start:
            return 'upcoming'
        if start <= now <= end:
            return 'live'
        return 'ended'
    if start:
        return 'ended' if now > start else 'upcoming'
    return 'upcoming'


def serialize_ticket_tier(tier):
    quantity = tier.quantity or 0
    sold = tier.sold or 0
    return {
        'id': tier.id,
        'name': tier.name,
        'price': str(tier.price),
        'quantity': quantity,
        'sold': sold,
        'available': max(quantity - sold, 0),
        'perks': tier.perks or '',
    }


def serialize_sponsor(request, sponsor):
    """A sponsor or a partner, with somewhere to send whoever clicks the logo."""
    return {
        'id': sponsor.sponsor_id,
        'name': sponsor.name,
        'kind': sponsor.kind,
        'logo': absolute_media_url(request, sponsor.logo, sponsor.logo_url),
        'website': sponsor.website,
        'links': [
            {'platform': link.platform, 'url': link.url}
            for link in sponsor.links.all()
        ],
    }


def social_links_dict(event):
    """Return social links as a {platform: url} dict (matches the FE wizard shape)."""
    return {link.platform: link.url for link in event.social_links.all()}


def attendees_count(event):
    """Total tickets sold across all tiers (0 until Phase 2 ticketing goes live)."""
    return sum((tier.sold or 0) for tier in event.ticket_tiers.all())


def _banner(request, event):
    return absolute_media_url(request, event.banner, event.banner_url)


def serialize_event_card(request, event):
    """Compact shape for listing cards. Emits both `id` and `event_id`."""
    tiers = list(event.ticket_tiers.all())
    return {
        'id': event.event_id,
        'event_id': event.event_id,
        'name': event.name,
        'description': event.desc,
        'desc': event.desc,
        'event_type': event.event_type,
        'category': event.category,
        'start_date': event.start_date,
        'end_date': event.end_date,
        'location': event.location,
        'virtual_link': event.event_link,
        'entry_fee': str(event.entry_fee) if event.entry_fee is not None else '0',
        'capacity': event.capacity,
        'banner': _banner(request, event),
        'banner_image': _banner(request, event),
        'logo': absolute_media_url(request, event.logo),
        'status': event_status(event),
        'is_featured': event.is_featured,
        'featured': event.is_featured,
        'attendees_count': sum((t.sold or 0) for t in tiers),
        'interaction_count': event.interaction_count,
        'game': event.game.game_title if event.game else None,
        'ticket_types': [serialize_ticket_tier(t) for t in tiers],
    }


def _linked_tournaments(request, event):
    """Full tournament cards for the event's Tournaments tab.

    Imported lazily: views_linking imports the tournament app, which imports
    vent_auth, and this module is loaded during app setup.
    """
    from .models import EventTournamentLink
    from .views_linking import serialize_linked_tournament, _viewer

    links = (EventTournamentLink.objects
             .filter(event=event)
             .select_related('tournament', 'tournament__tournament_game'))
    if not links:
        return []
    viewer = _viewer(request)
    return [serialize_linked_tournament(request, link, viewer) for link in links]


def serialize_event_detail(request, event):
    """Full shape for the view-event page (Overview / Tickets / Schedule tabs)."""
    data = serialize_event_card(request, event)
    creator = event.creator
    data.update({
        'created_at': event.created_at,
        'last_updated': event.last_updated,
        'reg_start_date': event.reg_start_date,
        'reg_end_date': event.reg_end_date,
        'organizer': {
            'user_id': creator.user_id,
            'username': creator.username,
            'full_name': creator.full_name,
        } if creator else None,
        'sponsors': [serialize_sponsor(request, s) for s in event.sponsors.all()
                     if s.kind == 'sponsor'],
        'partners': [serialize_sponsor(request, s) for s in event.sponsors.all()
                     if s.kind == 'partner'],
        'social_links': social_links_dict(event),
        'linked_tournaments': _linked_tournaments(request, event),
        'vendors_count': event.vendor_invites.count(),
    })
    return data
