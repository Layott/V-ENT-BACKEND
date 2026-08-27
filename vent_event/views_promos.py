"""Influencer links, promo codes, and who else may run an event.

All three are the organiser's to create, change and remove. An admin holding
`manage_events` may act too, through the same endpoints, so the console and the
organiser's own screen cannot drift apart about what these things are.

Managers are the one gated case: an event may only be handed to somebody else
when it belongs to an organisation. A personal event is one person's, and the
door list, the attendee data and the promo codes go with management of it.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override
from vent_auth.models import Users

from .models import Event, EventManager, EventPromo, EventReferral, TicketTier


def event_by_ref(ref):
    """An event by slug or by id, so an organiser URL can carry either.

    Numbers are still accepted because `?id=` links were shared before the slug
    rule, and the rule is that every address an event ever had keeps working.
    """
    from django.http import Http404

    ref = str(ref)
    if ref.isdigit():
        event = Event.objects.filter(event_id=int(ref)).first()
        if event:
            return event
    event = Event.objects.filter(slug=ref).first()
    if event:
        return event
    raise Http404('No event matches %s' % ref)


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'data': {}, 'message': message, 'code': code},
                    status=http_status)


def _actor_for_event(request, event):
    """(user, error). The organiser, a manager, or an admin who may override."""
    user, auth_error = actor_from_request(request)
    if auth_error is not None:
        return None, auth_error

    if event.creator_id == user.user_id:
        return user, None
    if EventManager.objects.filter(event=event, user=user, role='manager').exists():
        return user, None
    if may_override(user, 'manage_events'):
        return user, None

    return None, _err('Only the event organizer can do this.',
                      'ONLY_EVENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)


def _referral_row(r):
    return {
        'id': r.id,
        'name': r.name,
        'code': r.code,
        'url': r.url,
        'sponsor_id': r.sponsor_id,
        'allocation': r.allocation,
        'sold': r.sold,
        'remaining': r.remaining,
        'is_active': r.is_active,
    }


def _promo_row(p):
    return {
        'id': p.id,
        'code': p.code,
        'kind': p.kind,
        'value': str(p.value),
        'referral_id': p.referral_id,
        'referral_name': p.referral.name if p.referral_id else None,
        'tier_id': p.tier_id,
        'max_tickets': p.max_tickets,
        'used_tickets': p.used_tickets,
        'remaining': p.remaining,
        'starts_at': p.starts_at,
        'ends_at': p.ends_at,
        'is_active': p.is_active,
    }


def _manager_row(m):
    return {
        'id': m.id,
        'user_id': m.user_id,
        'username': m.user.username,
        'role': m.role,
    }


# --------------------------------------------------------------------- referrals

@api_view(['GET', 'POST'])
def event_referrals(request, event_id):
    """GET / POST /event/{id}/referrals/ - the influencer links on this event."""
    event = event_by_ref(event_id)
    user, err = _actor_for_event(request, event)
    if err:
        return err

    if request.method == 'GET':
        rows = event.referrals.all()
        return _ok({'results': [_referral_row(r) for r in rows], 'count': rows.count()})

    name = (request.data.get('name') or '').strip()
    code = (request.data.get('code') or '').strip()
    if not name:
        return _err('The influencer needs a name.', 'VALIDATION_FAILED')
    if not code:
        return _err('The link needs a code.', 'VALIDATION_FAILED')
    if EventReferral.objects.filter(event=event, code__iexact=code).exists():
        return _err('That code is already used on this event.', 'REFERRAL_EXISTS',
                    status.HTTP_409_CONFLICT)

    try:
        allocation = int(request.data.get('allocation') or 0)
    except (TypeError, ValueError):
        return _err('The allocation must be a number.', 'VALIDATION_FAILED')
    if allocation < 0:
        return _err('The allocation cannot be negative.', 'VALIDATION_FAILED')

    referral = EventReferral.objects.create(
        event=event, name=name, code=code,
        url=(request.data.get('url') or '').strip(),
        sponsor_id=request.data.get('sponsor_id') or None,
        allocation=allocation,
    )
    return _ok(_referral_row(referral), 'Link added.', status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
def event_referral_detail(request, event_id, referral_id):
    """PATCH / DELETE /event/{id}/referrals/{rid}/"""
    event = event_by_ref(event_id)
    user, err = _actor_for_event(request, event)
    if err:
        return err
    referral = get_object_or_404(EventReferral, id=referral_id, event=event)

    if request.method == 'DELETE':
        # Deleting a link that already sold tickets would erase the record of
        # who is owed for them, so it is retired instead.
        if referral.sold:
            referral.is_active = False
            referral.save(update_fields=['is_active'])
            return _ok(_referral_row(referral),
                       'That link already sold tickets, so it was switched off rather than deleted.')
        referral.delete()
        return _ok({}, 'Link removed.')

    updated = []
    for field in ('name', 'url'):
        if field in request.data:
            setattr(referral, field, (request.data.get(field) or '').strip())
            updated.append(field)

    if 'code' in request.data:
        code = (request.data.get('code') or '').strip()
        if not code:
            return _err('The link needs a code.', 'VALIDATION_FAILED')
        if (EventReferral.objects.filter(event=event, code__iexact=code)
                .exclude(pk=referral.pk).exists()):
            return _err('That code is already used on this event.', 'REFERRAL_EXISTS',
                        status.HTTP_409_CONFLICT)
        referral.code = code
        updated.append('code')

    if 'allocation' in request.data:
        try:
            allocation = int(request.data.get('allocation') or 0)
        except (TypeError, ValueError):
            return _err('The allocation must be a number.', 'VALIDATION_FAILED')
        if allocation and allocation < referral.sold:
            return _err('That is fewer tickets than this link has already sold.',
                        'ALLOCATION_BELOW_SOLD')
        referral.allocation = allocation
        updated.append('allocation')

    if 'is_active' in request.data:
        referral.is_active = str(request.data.get('is_active')).lower() in ('1', 'true', 'yes')
        updated.append('is_active')

    if not updated:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    referral.save(update_fields=updated)
    return _ok(_referral_row(referral), 'Link updated.')


# ------------------------------------------------------------------------ promos

@api_view(['GET', 'POST'])
def event_promos(request, event_id):
    """GET / POST /event/{id}/promos/ - the discount codes on this event."""
    event = event_by_ref(event_id)
    user, err = _actor_for_event(request, event)
    if err:
        return err

    if request.method == 'GET':
        rows = event.promos.select_related('referral')
        return _ok({'results': [_promo_row(p) for p in rows], 'count': rows.count()})

    code = (request.data.get('code') or '').strip()
    if not code:
        return _err('The promo needs a code.', 'VALIDATION_FAILED')
    if EventPromo.objects.filter(event=event, code__iexact=code).exists():
        return _err('That code is already used on this event.', 'PROMO_EXISTS',
                    status.HTTP_409_CONFLICT)

    kind = request.data.get('kind') or EventPromo.PERCENT
    if kind not in (EventPromo.PERCENT, EventPromo.AMOUNT):
        return _err('A promo is either a percentage or an amount.', 'VALIDATION_FAILED')

    try:
        value = float(request.data.get('value') or 0)
    except (TypeError, ValueError):
        return _err('The discount must be a number.', 'VALIDATION_FAILED')
    if value <= 0:
        return _err('The discount has to be more than zero.', 'VALIDATION_FAILED')
    if kind == EventPromo.PERCENT and value > 100:
        return _err('A percentage discount cannot be more than 100.', 'VALIDATION_FAILED')

    try:
        max_tickets = int(request.data.get('max_tickets') or 0)
    except (TypeError, ValueError):
        return _err('The ticket limit must be a number.', 'VALIDATION_FAILED')

    referral_id = request.data.get('referral_id') or None
    if referral_id and not EventReferral.objects.filter(event=event, id=referral_id).exists():
        return _err('That influencer is not on this event.', 'REFERRAL_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    tier_id = request.data.get('tier_id') or None
    if tier_id and not TicketTier.objects.filter(event=event, id=tier_id).exists():
        return _err('That ticket tier is not on this event.', 'TIER_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    promo = EventPromo.objects.create(
        event=event, code=code, kind=kind, value=value,
        referral_id=referral_id, tier_id=tier_id,
        max_tickets=max(max_tickets, 0),
        starts_at=request.data.get('starts_at') or None,
        ends_at=request.data.get('ends_at') or None,
    )
    return _ok(_promo_row(promo), 'Promo created.', status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
def event_promo_detail(request, event_id, promo_id):
    """PATCH / DELETE /event/{id}/promos/{pid}/"""
    event = event_by_ref(event_id)
    user, err = _actor_for_event(request, event)
    if err:
        return err
    promo = get_object_or_404(EventPromo, id=promo_id, event=event)

    if request.method == 'DELETE':
        # A code somebody already used stays, switched off: the orders that
        # carry it still need it to explain what they paid.
        if promo.used_tickets:
            promo.is_active = False
            promo.save(update_fields=['is_active'])
            return _ok(_promo_row(promo),
                       'That code has already been used, so it was switched off rather than deleted.')
        promo.delete()
        return _ok({}, 'Promo removed.')

    updated = []

    if 'code' in request.data:
        code = (request.data.get('code') or '').strip()
        if not code:
            return _err('The promo needs a code.', 'VALIDATION_FAILED')
        if (EventPromo.objects.filter(event=event, code__iexact=code)
                .exclude(pk=promo.pk).exists()):
            return _err('That code is already used on this event.', 'PROMO_EXISTS',
                        status.HTTP_409_CONFLICT)
        promo.code = code
        updated.append('code')

    if 'value' in request.data:
        try:
            value = float(request.data.get('value') or 0)
        except (TypeError, ValueError):
            return _err('The discount must be a number.', 'VALIDATION_FAILED')
        if value <= 0:
            return _err('The discount has to be more than zero.', 'VALIDATION_FAILED')
        kind = request.data.get('kind') or promo.kind
        if kind == EventPromo.PERCENT and value > 100:
            return _err('A percentage discount cannot be more than 100.', 'VALIDATION_FAILED')
        promo.value = value
        updated.append('value')

    if 'kind' in request.data:
        kind = request.data.get('kind')
        if kind not in (EventPromo.PERCENT, EventPromo.AMOUNT):
            return _err('A promo is either a percentage or an amount.', 'VALIDATION_FAILED')
        promo.kind = kind
        updated.append('kind')

    if 'max_tickets' in request.data:
        try:
            max_tickets = int(request.data.get('max_tickets') or 0)
        except (TypeError, ValueError):
            return _err('The ticket limit must be a number.', 'VALIDATION_FAILED')
        if max_tickets and max_tickets < promo.used_tickets:
            return _err('That is fewer tickets than this code has already been used on.',
                        'LIMIT_BELOW_USED')
        promo.max_tickets = max(max_tickets, 0)
        updated.append('max_tickets')

    if 'referral_id' in request.data:
        referral_id = request.data.get('referral_id') or None
        if referral_id and not EventReferral.objects.filter(event=event, id=referral_id).exists():
            return _err('That influencer is not on this event.', 'REFERRAL_NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        promo.referral_id = referral_id
        updated.append('referral')

    for field in ('starts_at', 'ends_at'):
        if field in request.data:
            setattr(promo, field, request.data.get(field) or None)
            updated.append(field)

    if 'is_active' in request.data:
        promo.is_active = str(request.data.get('is_active')).lower() in ('1', 'true', 'yes')
        updated.append('is_active')

    if not updated:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')

    promo.save(update_fields=updated)
    return _ok(_promo_row(promo), 'Promo updated.')


# ---------------------------------------------------------------------- managers

@api_view(['GET', 'POST'])
def event_managers(request, event_id):
    """GET / POST /event/{id}/managers/ - who else may run this event.

    POST is refused unless the event belongs to an organisation.
    """
    event = event_by_ref(event_id)
    user, err = _actor_for_event(request, event)
    if err:
        return err

    if request.method == 'GET':
        rows = event.managers.select_related('user')
        return _ok({
            'results': [_manager_row(m) for m in rows],
            'count': rows.count(),
            # The screen needs to know whether to offer the control at all,
            # rather than offering it and having the save refused.
            'can_add': bool(event.organization_id),
        })

    if not event.organization_id:
        return _err('Only an event that belongs to an organisation can be shared with other people.',
                    'EVENT_NOT_IN_ORGANISATION', status.HTTP_409_CONFLICT)

    # Only the creator hands out management. A manager adding more managers is
    # how an event quietly acquires people nobody chose.
    if event.creator_id != user.user_id and not may_override(user, 'manage_events'):
        return _err('Only the event organizer can add managers.',
                    'ONLY_EVENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)

    username = (request.data.get('username') or '').strip()
    target = Users.objects.filter(username__iexact=username).first()
    if target is None:
        return _err('No member with that username.', 'USER_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if target.user_id == event.creator_id:
        return _err('They already own this event.', 'ALREADY_THE_ORGANIZER')

    role = request.data.get('role') or 'manager'
    if role not in ('manager', 'door'):
        return _err('That is not a role on an event.', 'VALIDATION_FAILED')

    manager, created = EventManager.objects.get_or_create(
        event=event, user=target, defaults={'role': role, 'added_by': user})
    if not created:
        manager.role = role
        manager.save(update_fields=['role'])

    return _ok(_manager_row(manager), 'Added.',
               status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(['DELETE'])
def event_manager_detail(request, event_id, manager_id):
    """DELETE /event/{id}/managers/{mid}/ - take management back."""
    event = event_by_ref(event_id)
    user, err = _actor_for_event(request, event)
    if err:
        return err

    if event.creator_id != user.user_id and not may_override(user, 'manage_events'):
        return _err('Only the event organizer can remove managers.',
                    'ONLY_EVENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)

    manager = get_object_or_404(EventManager, id=manager_id, event=event)
    manager.delete()
    return _ok({}, 'Removed.')
