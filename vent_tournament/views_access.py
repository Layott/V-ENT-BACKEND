"""Who may enter a tournament: invite codes, and the approval queue.

Three things the PRD asks for that the platform had settings for and no
machinery behind:

  "generate unique URLs or codes for the tournament that can be shared with
   specific groups or teams (generate up to 64 codes for free users)"

  "Automated or manual procedure to accept or decline teams from your
   tournament (select yes or no)"

  "An option to download the text in txt, docx, pdf and xls should be available"

The download is here as plain text and CSV. Those two open in everything an
organiser is likely to have, including a phone, and a spreadsheet reads the CSV
directly. Word and PDF are a rendering job rather than a data one and would sit
behind a library nobody needs to run a tournament.
"""
import csv
import io
import secrets
import string

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override

from .models import Tournament, TournamentInvite, TournamentRegistration

# No I, O, 0 or 1. These get read off a phone screen and typed by somebody in a
# hurry, and those four are the pairs that get mistyped.
ALPHABET = ''.join(c for c in string.ascii_uppercase + string.digits
                   if c not in 'IO01')

FREE_CODE_LIMIT = 64


def _ok(data, message='OK', http=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': {}},
                    status=http)


def _tournament(ref):
    from vent_auth.slugs import resolve_or_redirect
    try:
        found, _moved = resolve_or_redirect(
            ref, entity_type='tournament', id_field='tournament_id',
            model=Tournament)
        if found is not None:
            return found
    except Exception:
        pass
    if str(ref).isdigit():
        return Tournament.objects.filter(pk=int(ref)).first()
    return Tournament.objects.filter(slug=ref).first()


def _organiser(request, tournament):
    user, err = actor_from_request(request)
    if err:
        return None, err
    if tournament.tournament_creator_id == user.user_id:
        return user, None
    if may_override(user, 'cancel_tournament'):
        return user, None
    return None, _err('Only the tournament organizer can do that.',
                      'ONLY_TOURNAMENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN)


def _new_code(length=8):
    return ''.join(secrets.choice(ALPHABET) for _ in range(length))


def _invite_row(invite, base_url=''):
    return {
        'id': invite.pk,
        'code': invite.code,
        'label': invite.label,
        'max_uses': invite.max_uses,
        'used_count': invite.used_count,
        'spent': invite.spent,
        # The link is the code on the end of the address, so an organiser can
        # send one thing rather than a link and a code to paste into it.
        'url': ('%s?invite=%s' % (base_url, invite.code)) if base_url else None,
    }


@api_view(['GET', 'POST', 'DELETE'])
def invites(request, tournament_id):
    """List, mint or clear the codes that open this tournament."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    user, err = _organiser(request, tournament)
    if err:
        return err

    base = '%s/tournaments/%s' % (
        request.data.get('base_url') or '', tournament.slug or tournament.pk)

    if request.method == 'GET':
        rows = TournamentInvite.objects.filter(tournament=tournament)
        return _ok({
            'invites': [_invite_row(i) for i in rows],
            'count': rows.count(),
            'limit': FREE_CODE_LIMIT,
        }, 'Invite codes')

    if request.method == 'DELETE':
        # Only the untouched ones. A code somebody has already registered with
        # is part of the record of how they got in.
        removed = TournamentInvite.objects.filter(
            tournament=tournament, used_count=0).delete()[0]
        return _ok({'removed': removed}, 'Unused codes cleared.')

    # POST: mint a batch, or take the ones the organiser typed.
    given = request.data.get('codes')
    label = str(request.data.get('label') or '')[:120]
    try:
        max_uses = max(1, int(request.data.get('max_uses') or 1))
    except (TypeError, ValueError):
        return _err('Uses has to be a whole number.', 'INVALID_NUMBER')

    existing = TournamentInvite.objects.filter(tournament=tournament).count()

    if isinstance(given, list) and given:
        wanted = [str(c).strip().upper() for c in given if str(c).strip()]
    elif isinstance(given, str) and given.strip():
        # Uploaded or pasted: one per line, or comma separated. Both are what
        # somebody actually produces from a spreadsheet.
        wanted = [c.strip().upper()
                  for c in given.replace(',', '\n').splitlines() if c.strip()]
    else:
        try:
            count = int(request.data.get('count') or 0)
        except (TypeError, ValueError):
            return _err('How many has to be a whole number.', 'INVALID_NUMBER')
        if count < 1:
            return _err('Say how many codes to make, or send the codes themselves.',
                        'VALIDATION_ERROR')
        if existing + count > FREE_CODE_LIMIT:
            return _err(
                'That would be more than %s codes. Remove some first.'
                % FREE_CODE_LIMIT, 'CODE_LIMIT', status.HTTP_409_CONFLICT)
        wanted = []
        seen = set(TournamentInvite.objects.filter(tournament=tournament)
                   .values_list('code', flat=True))
        while len(wanted) < count:
            code = _new_code()
            if code not in seen:
                seen.add(code)
                wanted.append(code)

    if not wanted:
        return _err('No codes given.', 'VALIDATION_ERROR')
    if existing + len(wanted) > FREE_CODE_LIMIT:
        return _err('That would be more than %s codes. Remove some first.'
                    % FREE_CODE_LIMIT, 'CODE_LIMIT', status.HTTP_409_CONFLICT)

    made = []
    with transaction.atomic():
        for code in wanted:
            invite, created = TournamentInvite.objects.get_or_create(
                tournament=tournament, code=code,
                defaults={'label': label, 'max_uses': max_uses,
                          'created_by': user})
            if created:
                made.append(invite)

    return _ok({'invites': [_invite_row(i, base) for i in made],
                'made': len(made),
                'total': TournamentInvite.objects.filter(tournament=tournament).count()},
               '%s code(s) ready.' % len(made), status.HTTP_201_CREATED)


@api_view(['GET'])
def invites_download(request, tournament_id):
    """The codes as a file. `?as=csv` for a spreadsheet, otherwise plain text.

    Not `?format=`: DRF reserves that for content negotiation, so asking for
    `format=csv` looks like a request for a renderer that does not exist and
    answers 404 rather than the file.
    """
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    _user, err = _organiser(request, tournament)
    if err:
        return err

    rows = TournamentInvite.objects.filter(tournament=tournament)
    wanted = str(request.GET.get('as') or 'txt').lower()
    stem = (tournament.slug or str(tournament.pk))

    if wanted == 'csv':
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['code', 'label', 'uses_allowed', 'uses_taken', 'spent'])
        for invite in rows:
            writer.writerow([invite.code, invite.label, invite.max_uses,
                             invite.used_count, 'yes' if invite.spent else 'no'])
        body = buffer.getvalue()
        content_type = 'text/csv'
        name = '%s-invite-codes.csv' % stem
    else:
        # One per line and nothing else, because the usual next step is pasting
        # them into a message.
        body = '\n'.join(i.code for i in rows) + '\n'
        content_type = 'text/plain'
        name = '%s-invite-codes.txt' % stem

    response = Response(body, content_type=content_type)
    response['Content-Disposition'] = 'attachment; filename="%s"' % name
    return response


@api_view(['GET', 'POST'])
def registrations(request, tournament_id):
    """The entrants waiting, and the organiser accepting or declining them."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    _user, err = _organiser(request, tournament)
    if err:
        return err

    if request.method == 'GET':
        rows = (TournamentRegistration.objects
                .filter(tournament=tournament)
                .select_related('team', 'user')
                .order_by('registered_at'))
        return _ok({
            'approval_required': tournament.approve_registrations,
            'registrations': [{
                'id': r.pk,
                'status': r.status,
                'entry_fee_paid': r.entry_fee_paid,
                'registered_at': r.registered_at,
                'name': (r.team.team_name if r.team_id
                         else (r.user.full_name or r.user.username) if r.user_id
                         else ''),
                'type': 'team' if r.team_id else 'user',
            } for r in rows],
            'pending': rows.filter(status='pending').count(),
        }, 'Registrations')

    decision = str(request.data.get('decision') or '').strip().lower()
    if decision not in ('accept', 'decline'):
        return _err('Say accept or decline.', 'VALIDATION_ERROR')

    ids = request.data.get('registration_ids')
    if not isinstance(ids, list) or not ids:
        return _err('Send the registrations to decide on.', 'VALIDATION_ERROR')

    rows = TournamentRegistration.objects.filter(tournament=tournament, pk__in=ids)
    if rows.count() != len(set(ids)):
        # Somebody else's registration in the list changes nothing at all.
        return _err('Those registrations are not in this tournament.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    # Declining does not refund here. An entry fee already taken is a refund
    # decision with money attached, and quietly reversing it inside a bulk
    # accept/decline is how somebody's balance changes without a record.
    new_status = 'confirmed' if decision == 'accept' else 'withdrawn'
    with transaction.atomic():
        rows.update(status=new_status)

    return _ok({'updated': rows.count(), 'status': new_status},
               'Accepted.' if decision == 'accept' else 'Declined.')
