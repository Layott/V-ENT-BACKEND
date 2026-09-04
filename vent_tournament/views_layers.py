"""Text layers on any overlay: four addresses, one implementation, two prefixes.

    GET    /tournament/<ref>/studio/sessions/<id>/element/<kind>/layers/
    POST   /tournament/<ref>/studio/sessions/<id>/element/<kind>/layers/
    POST   /tournament/<ref>/studio/sessions/<id>/element/<kind>/layers/<layer>/
    DELETE /tournament/<ref>/studio/sessions/<id>/element/<kind>/layers/<layer>/

    GET    /tournament/<ref>/overlays/<overlay>/layers/
    POST   /tournament/<ref>/overlays/<overlay>/layers/
    POST   /tournament/<ref>/overlays/<overlay>/layers/<layer>/
    DELETE /tournament/<ref>/overlays/<overlay>/layers/<layer>/

Every one of them also exists under `/event/<ref>/`, mounted from this same
module through the adapters at the bottom, exactly as `views_runsheet.py` mounts
its six. An event broadcast has a studio and uploaded overlays precisely as a
tournament does, and a feature built for one of them is a feature half this
platform does not have.

A layer on a studio graphic and a layer on an uploaded file differ in nothing
but what they hang off, so they share a validator (`text_layers.apply`), a
serializer (`text_layers.serialize`) and this file. What differs is one lookup
each.

Permission is `may_run_production(user, owner)`, the same answer the studio and
the overlay upload already use. Anonymous fails it because it takes no account
to compare, and the refusal carries the code the console translates by.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import text_layers
from .models import BroadcastElement, OverlayTextLayer, TournamentOverlay
from .production_access import (
    REFUSAL_CODE, find_owner, may_run_production, viewer as _viewer)


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, field=None,
         extra=None):
    """The platform's envelope, plus what the console needs to write a sentence.

    `field` says which box is wrong and `data` carries the real limits or the
    real list, because a sentence built here cannot be translated and a console
    that has to hardcode "between 8 and 400" is a console that will be wrong
    the day the range moves.
    """
    body = {'status': 'error', 'data': extra or {}, 'message': message,
            'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http)


def _refuse(kind):
    noun = 'event' if kind == 'event' else 'tournament'
    return _err('Only the organiser can change the overlays for this %s.' % noun,
                REFUSAL_CODE[kind], status.HTTP_403_FORBIDDEN)


def _not_found(kind):
    return _err('%s not found.' % ('Event' if kind == 'event' else 'Tournament'),
                'NOT_FOUND', status.HTTP_404_NOT_FOUND)


def _gone(what='No such text layer.'):
    return _err(what, 'NOT_FOUND', status.HTTP_404_NOT_FOUND)


def _owner_or_refusal(request, kind, ref):
    """The thing this address names, once the asker is allowed to touch it."""
    owner = find_owner(kind, ref)
    if owner is None:
        return None, _not_found(kind)
    if not may_run_production(_viewer(request), owner):
        return None, _refuse(kind)
    return owner, None


def _payload(rows):
    """What every one of these routes answers with.

    The whole list every time, not the row that changed. An operator presses
    save and reads the list back; a console patching its own copy is a console
    that disagrees with the server the first time two people are on it.
    """
    return {'layers': text_layers.serialize_many(rows),
            'text_options': text_layers.catalogue()}


def _all(**owner):
    """Every layer on one overlay, read back from the database.

    Read back rather than patched in memory, so what the console draws after a
    save is what the next reader will get. A response built from the list as it
    was before the write is how a reorder appears to have done nothing.
    """
    return list(OverlayTextLayer.objects.filter(**owner))


def _create(request, rows, **owner):
    """One new layer on whatever `owner` names."""
    data = request.data if isinstance(request.data, dict) else {}
    row = OverlayTextLayer(**owner)
    # Straight after the ones already there, so a layer added mid show paints
    # on top rather than under something.
    row.order = min(len(rows), text_layers.ORDER[1])
    try:
        text_layers.apply(row, data)
    except text_layers.LayerError as err:
        return _err(str(err), err.code, field=err.field, extra=err.data)
    if text_layers.is_empty(row):
        return _err('Say what the text should read.', 'VALIDATION_FAILED',
                    field='text')
    row.save()
    return _ok(_payload(_all(**owner)), 'Text added.')


def _edit(request, row, **owner):
    data = request.data if isinstance(request.data, dict) else {}
    try:
        text_layers.apply(row, data)
    except text_layers.LayerError as err:
        return _err(str(err), err.code, field=err.field, extra=err.data)
    if text_layers.is_empty(row):
        return _err('Say what the text should read.', 'VALIDATION_FAILED',
                    field='text')
    row.save()
    return _ok(_payload(_all(**owner)), 'Saved.')


# ---------------------------------------------------------------------------
# Layers on a studio graphic
# ---------------------------------------------------------------------------

def _element_row(owner, session_id, element_kind, writing):
    """The graphic these layers hang off, made only when something is written.

    A GET must not create a row: the console opens every graphic's panel as the
    operator scrolls, and a read that writes would fill the table with elements
    nobody ever put on air.
    """
    session = owner.broadcast_sessions.filter(pk=session_id).first()
    if session is None:
        return None, None, _gone('No such broadcast.')
    kinds = [k for k, _label in BroadcastElement.kinds_for(
        'event' if session.event_id else 'tournament')]
    if element_kind not in kinds:
        # A bracket on an event, a programme on a tournament. Named, so the
        # console can say which rather than show a bare 404.
        return None, None, _err(
            'There is no %s graphic here.' % element_kind.replace('_', ' '),
            'UNKNOWN_ELEMENT', status.HTTP_404_NOT_FOUND, field='kind')
    if writing and not session.is_live:
        return None, None, _err('This broadcast has ended. Start a new one.',
                                'BROADCAST_ENDED', status.HTTP_409_CONFLICT)

    row = session.elements.filter(kind=element_kind).first()
    if row is None and writing:
        row = BroadcastElement.objects.create(
            session=session, kind=element_kind, payload={})
    return session, row, None


def element_layers(request, kind, ref, session_id, element_kind):
    """GET the layers on one graphic, POST a new one."""
    owner, err = _owner_or_refusal(request, kind, ref)
    if err:
        return err
    _session, element, err = _element_row(owner, session_id, element_kind,
                                          request.method == 'POST')
    if err:
        return err

    rows = text_layers.for_element(element)
    if request.method == 'GET':
        return _ok(_payload(rows), 'Text layers')
    return _create(request, rows, element=element)


def element_layer_detail(request, kind, ref, session_id, element_kind, layer_id):
    """POST to correct one layer, DELETE to remove it."""
    owner, err = _owner_or_refusal(request, kind, ref)
    if err:
        return err
    _session, element, err = _element_row(owner, session_id, element_kind, True)
    if err:
        return err

    row = OverlayTextLayer.objects.filter(pk=layer_id, element=element).first()
    if row is None:
        return _gone()

    if request.method == 'DELETE':
        row.delete()
        return _ok(_payload(_all(element=element)), 'Removed.')
    return _edit(request, row, element=element)


# ---------------------------------------------------------------------------
# Layers on a file the organiser uploaded
# ---------------------------------------------------------------------------

def _overlay_row(owner, kind, overlay_id):
    """The uploaded file, and only if it belongs to the thing in the address.

    Filtered by owner rather than looked up by id alone. Without that, an
    organiser of one tournament could name any overlay id on the platform and
    write text onto somebody else's broadcast.
    """
    if kind == 'event':
        return TournamentOverlay.objects.filter(pk=overlay_id,
                                                event=owner).first()
    return TournamentOverlay.objects.filter(pk=overlay_id,
                                            tournament=owner).first()


def overlay_layers(request, kind, ref, overlay_id):
    """GET the layers on one uploaded file, POST a new one."""
    owner, err = _owner_or_refusal(request, kind, ref)
    if err:
        return err
    overlay = _overlay_row(owner, kind, overlay_id)
    if overlay is None:
        return _gone('No such overlay.')

    rows = list(overlay.text_layers.all())
    if request.method == 'GET':
        return _ok(_payload(rows), 'Text layers')
    return _create(request, rows, overlay=overlay)


def overlay_layer_detail(request, kind, ref, overlay_id, layer_id):
    """POST to correct one layer, DELETE to remove it."""
    owner, err = _owner_or_refusal(request, kind, ref)
    if err:
        return err
    overlay = _overlay_row(owner, kind, overlay_id)
    if overlay is None:
        return _gone('No such overlay.')

    row = OverlayTextLayer.objects.filter(pk=layer_id, overlay=overlay).first()
    if row is None:
        return _gone()

    if request.method == 'DELETE':
        row.delete()
        return _ok(_payload(_all(overlay=overlay)), 'Removed.')
    return _edit(request, row, overlay=overlay)


# ---------------------------------------------------------------------------
# The two mounts
# ---------------------------------------------------------------------------
#
# Django's URL conf names a function and each kind needs its own entry point, so
# every view above gets a pair of one line adapters. Written exactly as
# `views_runsheet._mount` writes them, including `@api_view`, which is the part
# that matters: a bare `def` that merely CALLS an `@api_view` function does not
# inherit its csrf exemption, so `CsrfViewMiddleware` refuses every POST and
# DELETE with "CSRF cookie not set" before any of this module is reached. The
# fault then looks like "saving is broken" rather than like a routing mistake,
# and reading still works because GET is not checked.

def _mount(view, kind, methods):
    """One kind's entry point for `view`, with DRF's exemptions intact."""
    @api_view(methods)
    def adapter(request, *args, **kwargs):
        first = kwargs.pop('event_id', None) or kwargs.pop('tournament_id', None)
        return view(request, kind, first, *args, **kwargs)
    adapter.__name__ = '%s_%s' % (kind, getattr(view, '__name__', 'view'))
    return adapter


tournament_element_layers = _mount(element_layers, 'tournament', ['GET', 'POST'])
tournament_element_layer_detail = _mount(element_layer_detail, 'tournament',
                                         ['POST', 'DELETE'])
tournament_overlay_layers = _mount(overlay_layers, 'tournament', ['GET', 'POST'])
tournament_overlay_layer_detail = _mount(overlay_layer_detail, 'tournament',
                                         ['POST', 'DELETE'])

event_element_layers = _mount(element_layers, 'event', ['GET', 'POST'])
event_element_layer_detail = _mount(element_layer_detail, 'event',
                                    ['POST', 'DELETE'])
event_overlay_layers = _mount(overlay_layers, 'event', ['GET', 'POST'])
event_overlay_layer_detail = _mount(overlay_layer_detail, 'event',
                                    ['POST', 'DELETE'])
