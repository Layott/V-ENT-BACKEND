"""Entry requirements: setting them, seeing what you owe, and checking them.

Three audiences, three shapes:

  * the organiser composes the list and reviews what people send
  * somebody thinking about entering sees exactly what they still owe, BEFORE
    they pay anything
  * the registration path asks whether they may enter, and is told which
    requirement stopped them
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

import logging

from . import partner_check
from . import requirements as req
from .models import EntryRequirement, EntrySubmission, Tournament

logger = logging.getLogger(__name__)


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _row(requirement):
    spec = req.KINDS.get(requirement.kind, {})
    return {
        'id': requirement.id,
        'kind': requirement.kind,
        'label': spec.get('label', requirement.kind),
        'checked_by': spec.get('check'),
        'config': requirement.config or {},
        'required': requirement.required,
        'order': requirement.order,
    }


def _tournament_or_none(tournament_id):
    return Tournament.objects.filter(pk=tournament_id).first()


def _may_manage(user, tournament):
    return (tournament.tournament_creator_id == user.user_id
            or may_override(user, 'cancel_tournament'))


# --------------------------------------------------------------------------
# The organiser
# --------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AllowAny])
def entry_requirements(request, tournament_id):
    """GET /tournament/<id>/requirements/ - what this tournament asks for.

    Public, because somebody deciding whether to enter should be able to read
    what is required before they have an account, let alone an entry fee.
    """
    tournament = _tournament_or_none(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    rows = [_row(r) for r in tournament.entry_requirements.all()]
    return _ok({
        'tournament': tournament.tournament_id,
        'requirements': rows,
        'catalogue': req.kind_catalogue(),
        # An empty list is the normal case and means open to everyone.
        'open_to_everyone': not rows,
    }, 'Entry requirements')


@api_view(['PUT'])
def set_entry_requirements(request, tournament_id):
    """PUT /tournament/<id>/requirements/set/ - replace the list, in order."""
    user, err = actor_from_request(request)
    if err:
        return err

    tournament = _tournament_or_none(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if not _may_manage(user, tournament):
        return _err('These are not your tournament\'s requirements to change.',
                    'NOT_YOURS', status.HTTP_403_FORBIDDEN)

    raw = request.data.get('requirements')
    if not isinstance(raw, list):
        return _err('Send the requirements as a list, in the order they apply.',
                    'VALIDATION_FAILED', field='requirements')

    cleaned = []
    for index, item in enumerate(raw):
        try:
            cleaned.append((index, req.clean(item)))
        except req.RequirementError as exc:
            return _err(str(exc), 'VALIDATION_FAILED',
                        field=getattr(exc, 'field', None))

    kinds = [c['kind'] for _i, c in cleaned]
    if len(set(kinds)) != len(kinds):
        return _err('The same requirement cannot be added twice.',
                    'VALIDATION_FAILED', field='requirements')

    # Replaced wholesale, so the order on screen is the order stored. Anything
    # somebody already submitted against a requirement that survives is kept,
    # which is why they are matched by kind rather than deleted and recreated.
    existing = {r.kind: r for r in tournament.entry_requirements.all()}
    keep = set()
    for order, data in cleaned:
        row = existing.get(data['kind'])
        if row is None:
            row = EntryRequirement(tournament=tournament, kind=data['kind'])
        row.config = data['config']
        row.required = data['required']
        row.order = order
        row.save()
        keep.add(row.pk)

    tournament.entry_requirements.exclude(pk__in=keep).delete()

    return _ok({'requirements': [_row(r) for r in tournament.entry_requirements.all()]},
               'Requirements saved.')


# --------------------------------------------------------------------------
# The entrant
# --------------------------------------------------------------------------

def _submissions_for(tournament, user):
    rows = EntrySubmission.objects.filter(
        requirement__tournament=tournament, user=user
    ).select_related('requirement')
    return {s.requirement.kind: {'status': s.status, 'note': s.note,
                                 'value': s.value} for s in rows}


@api_view(['GET'])
def my_entry_status(request, tournament_id):
    """GET /tournament/<id>/requirements/mine/ - what I still owe.

    Before paying anything. Telling somebody what they need AFTER they have
    filled in a form and pressed pay is how a registration flow loses people.
    """
    user, err = actor_from_request(request)
    if err:
        return err

    tournament = _tournament_or_none(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    rows = [_row(r) for r in tournament.entry_requirements.all()]
    results = req.evaluate(
        rows, user, tournament=tournament,
        submissions=_submissions_for(tournament, user))

    return _ok({
        'requirements': results,
        'outstanding': req.blocking(results),
        'may_enter': not req.blocking(results),
    }, 'Your entry status')


@api_view(['POST'])
def submit_requirement(request, tournament_id, requirement_id):
    """POST /tournament/<id>/requirements/<rid>/submit/ - send what was asked for."""
    user, err = actor_from_request(request)
    if err:
        return err

    requirement = EntryRequirement.objects.filter(
        pk=requirement_id, tournament_id=tournament_id).first()
    if requirement is None:
        return _err('No such requirement on this tournament.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if req.KINDS.get(requirement.kind, {}).get('check') == req.AUTOMATIC:
        return _err('This one is checked automatically; there is nothing to send.',
                    'NOT_SUBMITTABLE')

    value = request.data.get('value')
    if value in (None, '', {}, []):
        return _err('Fill this in before sending it.', 'VALIDATION_FAILED', field='value')

    # Sending again replaces what was there and puts it back in the queue, so
    # somebody refused for a typo can fix it without asking anybody.
    submission, _created = EntrySubmission.objects.update_or_create(
        requirement=requirement, user=user,
        defaults={'value': value, 'status': 'pending', 'note': '',
                  'reviewed_by': None, 'reviewed_at': None},
    )

    # A requirement that names a partner is asked of the partner, once, here -
    # not on every page render, and not by a person if the partner can answer.
    #
    # Every failure leaves it pending: no verification URL, a timeout, a 500, a
    # body that is not JSON, a 200 in a shape we do not recognise. Blocking a
    # registration because somebody else's server is down is not a trade worth
    # making, and a login page served with a 200 must never read as approval.
    message = 'Sent. The organiser will check it.'
    if requirement.kind == 'partner_verified':
        config = requirement.config or {}
        partner = partner_check.partner_for(config.get('partner'))
        outcome = partner_check.ask(
            partner, config.get('field_label') or 'username',
            value if isinstance(value, str) else str(value))

        if outcome.checked:
            submission.status = 'approved' if outcome.verified else 'refused'
            submission.note = outcome.detail or (
                '' if outcome.verified
                else 'The partner does not recognise that. Check it and send it again.')
            submission.reviewed_at = timezone.now()
            # reviewed_by stays null: a partner is not a person, and recording
            # one as the reviewer would put a name against a decision nobody
            # made.
            submission.save(update_fields=['status', 'note', 'reviewed_at'])
            message = ('Confirmed by the partner.' if outcome.verified
                       else 'The partner did not recognise that.')
        elif outcome.reason != 'no_partner':
            # Worth saying out loud. Silence here looks identical to the
            # partner simply not being configured.
            logger.info('partner verification fell back to review: %s', outcome.reason)

    return _ok({'status': submission.status}, message)


# --------------------------------------------------------------------------
# The review queue
# --------------------------------------------------------------------------

@api_view(['GET'])
def review_queue(request, tournament_id):
    """GET /tournament/<id>/requirements/queue/ - everything waiting on a person."""
    user, err = actor_from_request(request)
    if err:
        return err

    tournament = _tournament_or_none(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if not _may_manage(user, tournament):
        return _err('This is not your tournament\'s queue.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    wanted = request.GET.get('status', 'pending')
    rows = EntrySubmission.objects.filter(
        requirement__tournament=tournament
    ).select_related('requirement', 'user')
    if wanted != 'all':
        rows = rows.filter(status=wanted)

    return _ok({
        'submissions': [
            {
                'id': s.id,
                'requirement': _row(s.requirement),
                'user': {'id': s.user_id, 'username': s.user.username},
                'value': s.value,
                'status': s.status,
                'note': s.note,
                'submitted_at': s.submitted_at,
            }
            for s in rows
        ],
        'counts': {
            'pending': EntrySubmission.objects.filter(
                requirement__tournament=tournament, status='pending').count(),
        },
    }, 'Review queue')


@api_view(['POST'])
def review_submission(request, tournament_id, submission_id):
    """POST /tournament/<id>/requirements/queue/<sid>/ - accept or refuse one."""
    user, err = actor_from_request(request)
    if err:
        return err

    tournament = _tournament_or_none(tournament_id)
    if tournament is None:
        return _err('No such tournament.', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if not _may_manage(user, tournament):
        return _err('This is not your tournament\'s queue.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    submission = EntrySubmission.objects.filter(
        pk=submission_id, requirement__tournament=tournament).first()
    if submission is None:
        return _err('No such submission.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    decision = str(request.data.get('decision') or '').strip().lower()
    if decision not in ('approved', 'refused'):
        return _err('Decision must be approved or refused.', 'VALIDATION_FAILED',
                    field='decision')

    note = str(request.data.get('note') or '')[:1000]
    if decision == 'refused' and not note:
        # Refusing without a reason leaves somebody to guess what to change,
        # and they will send exactly the same thing again.
        return _err('Say why, so they know what to fix.', 'VALIDATION_FAILED',
                    field='note')

    submission.status = decision
    submission.note = note
    submission.reviewed_by = user
    submission.reviewed_at = timezone.now()
    submission.save(update_fields=['status', 'note', 'reviewed_by', 'reviewed_at'])

    return _ok({'status': submission.status}, 'Recorded.')
