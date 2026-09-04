"""Serialization helpers for the vent_event app.

The V-ENT backend serializes with plain functions returning dicts (see
`vent_tournament.views`) rather than DRF ModelSerializers, because most payloads
compose derived fields (computed status, absolute media URLs, ticket availability).
These helpers keep that house style but centralize event shaping so the listing,
detail, and create endpoints all emit an identical Event object.
"""

from urllib.parse import quote_plus

from django.utils import timezone


def map_search_url(event):
    """A maps search for the venue, or '' when there is nothing to search for.

    Google Maps rather than a chosen provider: it is what opens on both phone
    platforms in Nigeria without an app install, and the URL form is stable.
    Virtual events get nothing, because there is nowhere to go.
    """
    if (event.event_type or '').lower() == 'virtual':
        return ''
    parts = [p for p in (event.venue_name, event.location) if p]
    if not parts:
        return ''
    # The venue name and the address together. Either alone is ambiguous in a
    # city with more than one of anything.
    return 'https://www.google.com/maps/search/?api=1&query=%s' % quote_plus(
        ', '.join(parts))


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
        # The address a person sees. Every event has had one in the database
        # since the slug migration, and no serializer sent it, so every card on
        # the site linked by primary key. `my-events` sent it and the public
        # listing did not, which is why only one of the two obeyed the rule.
        'slug': event.slug,
        'name': event.name,
        'description': event.desc,
        'desc': event.desc,
        'event_type': event.event_type,
        'category': event.category,
        'start_date': event.start_date,
        'end_date': event.end_date,
        'location': event.location,
        # Getting there. `location` is a line somebody typed, which is enough to
        # print on a ticket and not enough to travel to.
        'venue_name': event.venue_name,
        'map_link': event.map_link,
        # A search, not a pin, and named so nobody mistakes it for one. An
        # organiser who drops a real pin gets `map_link` above; everybody else
        # gets the address handed to a map rather than nothing at all. Kept
        # separate because a search for "The Dome, Lagos" can land on the wrong
        # Dome, and a page that presented that as the venue would be lying.
        'map_search_url': map_search_url(event),
        # The pin, for the map drawn on the page. Null where the organiser has
        # not given one, and the page says so rather than centring on nowhere.
        'latitude': float(event.latitude) if event.latitude is not None else None,
        'longitude': float(event.longitude) if event.longitude is not None else None,
        'directions': event.directions,
        'virtual_link': event.event_link,
        'self_check_in': event.self_check_in,
        'self_check_in_opens_minutes': event.self_check_in_opens_minutes,
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
        # Whether there is a published run of show to link to. On the CARD as
        # well as the detail, because the sitemap reads the listing and a page
        # nothing points at is a page nobody finds. A field that is added to
        # the detail payload only, and left on the card to be "done later", is
        # the same bug in slower motion.
        'has_run_of_show': _has_public_run_of_show(event),
    }


def _has_public_run_of_show(event):
    """Whether a run of show exists here that anybody may open.

    Reads an annotation when the queryset supplied one, so a listing of forty
    events asks once rather than forty times. Falls back to its own query for
    a single record, where one more query costs nothing and the alternative is
    every caller having to remember to annotate.

    Only `public` counts. A link only sheet is unlisted by definition, and a
    page advertising it would be the one thing that unlists it.
    """
    annotated = getattr(event, 'has_public_run_sheet', None)
    if annotated is not None:
        return bool(annotated)
    from vent_tournament.models import RunSheet
    return RunSheet.objects.filter(event=event,
                                   visibility=RunSheet.PUBLIC).exists()


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
        # How many one email may hold. The buyer's form needs it so it can cap
        # the quantity there rather than refusing after they have filled in a
        # form, and the edit screen needs it to draw its own switch.
        'max_tickets_per_email': event.max_tickets_per_email,
        # What the organiser typed the prices in. The page converts for the
        # reader; the charge is still settled in naira.
        'currency': getattr(event, 'currency', 'NGN') or 'NGN',
        'sponsors': [serialize_sponsor(request, s) for s in event.sponsors.all()
                     if s.kind == 'sponsor'],
        'partners': [serialize_sponsor(request, s) for s in event.sponsors.all()
                     if s.kind == 'partner'],
        'social_links': social_links_dict(event),
        'linked_tournaments': _linked_tournaments(request, event),
        'vendors_count': event.vendor_invites.count(),
    })
    return data
