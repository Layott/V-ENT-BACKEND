"""An organiser changing who is behind an event, after it exists.

Sponsors and partners could only be set inside the creation wizard. After that
they were frozen: a sponsor who signed on in week three could not be added, one
who pulled out could not be removed, and a logo uploaded at the wrong size could
not be replaced. The event page rendered them and nothing could write them.

That is the shape this codebase keeps producing, and it is worth naming: the
data model was complete, the read path was complete, and there was no way in.

Two things carried over from the rest of the app.

**A sponsor and a partner are one model with a different word on it.** Splitting
them would mean writing this file twice, and the first field added to one would
silently be missing from the other.

**A logo is replaced, never edited in place.** Uploading a new file leaves the
old one on disk rather than deleting it, because a half-finished save that has
already removed the previous logo leaves the event with no artwork at all.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from .models import Sponsor, SponsorLink

MAX_NAME = 100
MAX_URL = 500


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _event_and_permission(request, event_id):
    """The event, and whether this caller may change who backs it."""
    user, err = actor_from_request(request)
    if err:
        return None, None, err

    from .views import _event_by_ref
    event = _event_by_ref(event_id)
    if event is None:
        return None, None, _err('Event not found.', 'NOT_FOUND',
                                status.HTTP_404_NOT_FOUND)

    if event.creator_id != user.user_id and not may_override(user, 'manage_events'):
        return None, None, _err(
            'Only the event organizer can change its sponsors.',
            'ONLY_EVENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)

    return event, user, None


def serialize_sponsor(sponsor, request=None):
    """The same shape the event page already reads, so nothing has two forms."""
    logo = None
    if sponsor.logo:
        logo = sponsor.logo.url
        if request is not None and logo and logo.startswith('/'):
            logo = request.build_absolute_uri(logo)
    elif sponsor.logo_url:
        logo = sponsor.logo_url

    return {
        'id': sponsor.sponsor_id,
        'name': sponsor.name,
        'kind': sponsor.kind,
        'logo': logo,
        'website': sponsor.website,
        'sort_order': sponsor.sort_order,
        'links': [
            {'platform': link.platform, 'url': link.url}
            for link in sponsor.links.all()
        ] if hasattr(sponsor, 'links') else [],
    }


def _clean_kind(raw, current='sponsor'):
    kind = (raw or current or 'sponsor').strip().lower()
    return kind if kind in ('sponsor', 'partner') else 'sponsor'


@api_view(['GET', 'POST'])
def event_sponsors(request, event_id):
    """GET the list for the edit form, POST to add one.

    GET is organiser-only on purpose even though the same rows are public on the
    event page: this returns the editing view, including rows an organiser has
    added but not yet given artwork to.
    """
    event, _user, err = _event_and_permission(request, event_id)
    if err:
        return err

    if request.method == 'GET':
        rows = event.sponsors.all().prefetch_related('links')
        return _ok({'sponsors': [serialize_sponsor(s, request) for s in rows]},
                   'Sponsors and partners')

    name = (request.data.get('name') or '').strip()
    if not name:
        return _err('Give the sponsor a name.', 'VALIDATION_ERROR', field='name')

    # Appended, not inserted. The order an organiser added them in is the order
    # they were agreed in, which is the order that matters to the people paying.
    last = event.sponsors.order_by('-sort_order').first()
    sponsor = Sponsor.objects.create(
        event=event,
        name=name[:MAX_NAME],
        kind=_clean_kind(request.data.get('kind')),
        logo=request.FILES.get('logo'),
        logo_url=(request.data.get('logo_url') or '').strip()[:MAX_URL] or None,
        website=(request.data.get('website') or '').strip()[:MAX_URL] or None,
        sort_order=(last.sort_order + 1) if last else 0,
    )
    return _ok({'sponsor': serialize_sponsor(sponsor, request)},
               'Added.', status.HTTP_201_CREATED)


@api_view(['PUT', 'PATCH', 'DELETE'])
def event_sponsor(request, event_id, sponsor_id):
    """Change or remove one of them."""
    event, _user, err = _event_and_permission(request, event_id)
    if err:
        return err

    sponsor = get_object_or_404(Sponsor, sponsor_id=sponsor_id, event=event)

    if request.method == 'DELETE':
        sponsor.delete()
        return _ok({'removed': sponsor_id}, 'Removed.')

    if 'name' in request.data:
        name = (request.data.get('name') or '').strip()
        if not name:
            return _err('Give the sponsor a name.', 'VALIDATION_ERROR',
                        field='name')
        sponsor.name = name[:MAX_NAME]

    if 'kind' in request.data:
        sponsor.kind = _clean_kind(request.data.get('kind'), sponsor.kind)

    if 'website' in request.data:
        sponsor.website = (request.data.get('website') or '').strip()[:MAX_URL] or None

    # A new file replaces the old one. An empty value is left alone rather than
    # treated as "remove the logo": a form that submits every field would then
    # wipe the artwork every time somebody corrected a spelling.
    if request.FILES.get('logo'):
        sponsor.logo = request.FILES['logo']
        sponsor.logo_url = None
    elif (request.data.get('logo_url') or '').strip():
        sponsor.logo_url = request.data['logo_url'].strip()[:MAX_URL]

    sponsor.save()
    return _ok({'sponsor': serialize_sponsor(sponsor, request)}, 'Saved.')
