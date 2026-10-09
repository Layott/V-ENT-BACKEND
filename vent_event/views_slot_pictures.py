"""Pictures of a pitch or stall, and the venue layout (inbox 419).

CEO, 8 October 2026: "The sell a pitch page should be \"sell a pitch/stall\".
And organizers should be able to upload pictures of how it'll look and then
users should be able to view it."

    POST   /event/<event>/slots/<id>/pictures/            add one      (organiser)
    DELETE /event/<event>/slots/<id>/pictures/<pic>/      remove one   (organiser)
    POST   /event/<event>/slots/<id>/pictures/order/      reorder      (organiser)
    POST   /event/<event>/venue-layout/                   set it       (organiser)
    DELETE /event/<event>/venue-layout/                   remove it    (organiser)

Who may do it is exactly who may edit the pitch: `_actor_for_event`, the same
guard as listing and editing a slot. The pictures are read through the slot
listing, which is public, because a buyer has to see what they are buying.

Every file goes through `uploads.files_refusal` before it reaches a model: the
bytes must be a real PNG, JPG or WebP within the size cap, and it is stored
under a random name.
"""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view

from vent_auth import uploads

from .models import VendorSlot, VendorSlotPicture
from .views_promos import _actor_for_event, event_by_ref
from .views_tickets import _error, _ok
from .views_vendor_slots import _slot_row, layout_url


def _slot_for(request, event_id, slot_id):
    """(event, slot, None) for an organiser, or (None, None, refusal)."""
    event = event_by_ref(event_id)
    if event is None:
        return None, None, _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    _actor, err = _actor_for_event(request, event)
    if err is not None:
        return None, None, err
    slot = VendorSlot.objects.filter(id=slot_id, event=event).first()
    if slot is None:
        return None, None, _error('That pitch does not exist.', 'NOT_FOUND',
                                  status.HTTP_404_NOT_FOUND)
    return event, slot, None


def _renumber(slot):
    for index, picture in enumerate(slot.pictures.order_by('position', 'id')):
        if picture.position != index:
            picture.position = index
            picture.save(update_fields=['position'])


@api_view(['POST'])
def slot_pictures(request, event_id, slot_id):
    event, slot, err = _slot_for(request, event_id, slot_id)
    if err is not None:
        return err
    if 'image' not in request.FILES:
        return _error('Choose a picture to add.', 'NO_IMAGE', status.HTTP_400_BAD_REQUEST)
    refused = uploads.files_refusal(request, 'image')
    if refused:
        return refused
    with transaction.atomic():
        count = slot.pictures.select_for_update().count()
        if count >= VendorSlotPicture.MAX_PER_SLOT:
            return _error('A pitch can have up to %d pictures. Remove one first.'
                          % VendorSlotPicture.MAX_PER_SLOT,
                          'TOO_MANY_PICTURES', status.HTTP_409_CONFLICT)
        VendorSlotPicture.objects.create(slot=slot, image=request.FILES['image'],
                                         position=count)
    return _ok({'slot': _slot_row(slot, request=request)}, 'Picture added.')


@api_view(['DELETE'])
def slot_picture_detail(request, event_id, slot_id, picture_id):
    event, slot, err = _slot_for(request, event_id, slot_id)
    if err is not None:
        return err
    # Found through the slot, which was found through the event the caller
    # manages: a picture id from somebody else's pitch is simply not found.
    picture = VendorSlotPicture.objects.filter(id=picture_id, slot=slot).first()
    if picture is None:
        return _error('That picture does not exist.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    with transaction.atomic():
        picture.image.delete(save=False)
        picture.delete()
        _renumber(slot)
    return _ok({'slot': _slot_row(slot, request=request)}, 'Picture removed.')


@api_view(['POST'])
def slot_pictures_order(request, event_id, slot_id):
    event, slot, err = _slot_for(request, event_id, slot_id)
    if err is not None:
        return err
    order = request.data.get('order')
    if not isinstance(order, list) or len(order) > VendorSlotPicture.MAX_PER_SLOT:
        return _error('Send the pictures in the order you want them.',
                      'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    try:
        ids = [int(x) for x in order]
    except (TypeError, ValueError):
        return _error('Send the pictures in the order you want them.',
                      'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    current = {p.id: p for p in slot.pictures.all()}
    if sorted(ids) != sorted(current):
        return _error('That is not the set of pictures this pitch has.',
                      'PICTURES_CHANGED', status.HTTP_409_CONFLICT)
    with transaction.atomic():
        for index, pid in enumerate(ids):
            picture = current[pid]
            if picture.position != index:
                picture.position = index
                picture.save(update_fields=['position'])
    return _ok({'slot': _slot_row(slot, request=request)}, 'Order saved.')


@api_view(['POST', 'DELETE'])
def venue_layout(request, event_id):
    event = event_by_ref(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    _actor, err = _actor_for_event(request, event)
    if err is not None:
        return err
    if request.method == 'DELETE':
        if event.venue_layout:
            event.venue_layout.delete(save=False)
        event.venue_layout = None
        event.save(update_fields=['venue_layout'])
        return _ok({'venue_layout': None}, 'Layout removed.')
    if 'image' not in request.FILES:
        return _error('Choose a picture of the layout.', 'NO_IMAGE', status.HTTP_400_BAD_REQUEST)
    refused = uploads.files_refusal(request, 'image')
    if refused:
        return refused
    if event.venue_layout:
        event.venue_layout.delete(save=False)
    event.venue_layout = request.FILES['image']
    event.save(update_fields=['venue_layout'])
    return _ok({'venue_layout': layout_url(event, request)}, 'Layout saved.')
