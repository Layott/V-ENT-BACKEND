"""Applying to be a partner, running one, and reviewing the queue.

Three audiences, three sets of endpoints:

- anybody signed in can apply, and see their own applications
- an approved partner manages their own keys, within the scopes an admin granted
- an admin reviews applications, grants scopes, and approves SSO separately
"""
import logging
import re

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.views_profile import _user_from_bearer
from vent_auth import emails

from .models import Partner, PartnerApiKey, SCOPES, valid_scopes

logger = logging.getLogger(__name__)


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http_status)


def _err(message, code='ERROR', http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message, 'data': None},
                    status=http_status)


def _is_admin(user):
    return bool(getattr(user, 'is_staff', False) and getattr(user, 'admin_role', ''))


def _unique_slug(name):
    base = slugify(name)[:150] or 'partner'
    slug, n = base, 2
    while Partner.objects.filter(slug=slug).exists():
        slug = f'{base}-{n}'[:160]
        n += 1
    return slug


def _partner_row(p, *, include_private=False):
    row = {
        'id': p.partner_id,
        'name': p.name,
        'slug': p.slug,
        'status': p.status,
        'sso_status': p.sso_status,
        'website': p.website,
        'description': p.description,
        'contact_name': p.contact_name,
        'contact_email': p.contact_email,
        'requested_scopes': p.requested_scopes,
        'approved_scopes': p.approved_scopes,
        'created_at': p.created_at,
        'reviewed_at': p.reviewed_at,
        'review_note': p.review_note,
        'owner': p.owner.username if p.owner_id else None,
    }
    if include_private:
        row.update({
            'intended_use': p.intended_use,
            'legal_name': p.legal_name,
            'registration_number': p.registration_number,
            'privacy_policy_url': p.privacy_policy_url,
            'terms_url': p.terms_url,
            'data_protection_contact': p.data_protection_contact,
            'redirect_uris': p.redirect_uris,
            'sso_client_id': p.sso_client_id,
            'has_sso_secret': bool(p.sso_client_secret_hash),
            'keys': [
                {
                    'id': k.id,
                    'name': k.name,
                    'key_id': k.key_id,
                    'scopes': k.scopes,
                    'rate_limit_per_minute': k.rate_limit_per_minute,
                    'created_at': k.created_at,
                    'last_used_at': k.last_used_at,
                    'revoked_at': k.revoked_at,
                }
                for k in p.api_keys.all()
            ],
        })
    return row


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

@api_view(['GET'])
def scope_catalogue(request):
    """Every scope and what it opens. Public: an applicant needs to read this."""
    return _ok({'scopes': SCOPES}, 'Scopes')


@api_view(['POST'])
def apply_partner(request):
    """POST /partners/apply/ - ask for access. Nothing is granted here."""
    user, err = _user_from_bearer(request)
    if err:
        return err

    name = (request.data.get('name') or '').strip()
    contact_email = (request.data.get('contact_email') or '').strip().lower()
    if not name or not contact_email:
        return _err('A partner name and a contact email are required.', 'MISSING_FIELDS')

    if Partner.objects.filter(owner=user, status__in=('pending', 'approved')).exists():
        return _err(
            'You already have a partner application in flight or approved.',
            'ALREADY_APPLIED',
            status.HTTP_409_CONFLICT,
        )

    wants_sso = bool(request.data.get('wants_sso'))
    requested = valid_scopes(request.data.get('requested_scopes'))

    partner = Partner.objects.create(
        name=name[:140],
        slug=_unique_slug(name),
        owner=user,
        contact_name=(request.data.get('contact_name') or user.full_name or user.username)[:140],
        contact_email=contact_email,
        website=(request.data.get('website') or '')[:200],
        description=(request.data.get('description') or '')[:2000],
        intended_use=(request.data.get('intended_use') or '')[:2000],
        requested_scopes=requested,
        # SSO asks for more, and asking for it does not grant it.
        sso_status='requested' if wants_sso else 'none',
        legal_name=(request.data.get('legal_name') or '')[:200],
        registration_number=(request.data.get('registration_number') or '')[:80],
        privacy_policy_url=(request.data.get('privacy_policy_url') or '')[:200],
        terms_url=(request.data.get('terms_url') or '')[:200],
        data_protection_contact=(request.data.get('data_protection_contact') or '')[:254],
        redirect_uris=[u for u in (request.data.get('redirect_uris') or []) if _valid_redirect(u)][:10],
    )

    try:
        emails.send_partner_application_received(partner)
    except Exception:
        logger.warning('partner application email failed', exc_info=True)

    return _ok(_partner_row(partner, include_private=True),
               'Application received. An admin reviews it before anything is granted.',
               status.HTTP_201_CREATED)


def _valid_redirect(uri):
    """Where a partner may be sent back to after signing in.

    https only, no fragment, and no wildcards. localhost over http is allowed so
    a partner can build against it.
    """
    uri = str(uri or '').strip()
    if not uri or '#' in uri or '*' in uri:
        return False
    if uri.startswith('http://localhost') or uri.startswith('http://127.0.0.1'):
        return True
    return bool(re.match(r'^https://[^\s]+$', uri))


@api_view(['GET'])
def my_partners(request):
    """GET /partners/mine/ - what this account has applied for or runs."""
    user, err = _user_from_bearer(request)
    if err:
        return err
    rows = [
        _partner_row(p, include_private=True)
        for p in Partner.objects.filter(owner=user).prefetch_related('api_keys')
    ]
    return _ok({'partners': rows, 'scopes': SCOPES}, 'Your partner accounts')


# ---------------------------------------------------------------------------
# A partner managing itself
# ---------------------------------------------------------------------------

def _own_partner(request, partner_id):
    user, err = _user_from_bearer(request)
    if err:
        return None, None, err
    partner = Partner.objects.filter(pk=partner_id).first()
    if partner is None:
        return None, None, _err('No such partner.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if partner.owner_id != user.user_id and not _is_admin(user):
        return None, None, _err('That is not your partner account.', 'FORBIDDEN',
                                status.HTTP_403_FORBIDDEN)
    return user, partner, None


@api_view(['POST'])
def create_key(request, partner_id):
    """POST /partners/<id>/keys/ - issue a key. Shown once, never again."""
    user, partner, err = _own_partner(request, partner_id)
    if err:
        return err

    if not partner.is_active:
        return _err('This partner is not approved yet.', 'NOT_APPROVED', status.HTTP_403_FORBIDDEN)

    if partner.api_keys.filter(revoked_at__isnull=True).count() >= 5:
        return _err('Five live keys is the limit. Revoke one first.', 'TOO_MANY_KEYS')

    wanted = valid_scopes(request.data.get('scopes')) or list(partner.approved_scopes or [])
    granted = [s for s in wanted if partner.allows(s)]
    if not granted:
        return _err('None of those scopes are approved for this partner.', 'NO_SCOPES')

    key, plaintext = PartnerApiKey.issue(
        partner,
        name=(request.data.get('name') or 'Default key')[:120],
        scopes=granted,
        created_by=user,
    )
    return _ok(
        {
            'key': {'id': key.id, 'name': key.name, 'key_id': key.key_id, 'scopes': key.scopes},
            'secret': plaintext,
        },
        'Copy this key now. It is not shown again.',
        status.HTTP_201_CREATED,
    )


@api_view(['POST'])
def revoke_key(request, partner_id, key_id):
    user, partner, err = _own_partner(request, partner_id)
    if err:
        return err
    key = partner.api_keys.filter(pk=key_id, revoked_at__isnull=True).first()
    if key is None:
        return _err('No such live key.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    key.revoked_at = timezone.now()
    key.save(update_fields=['revoked_at'])
    return _ok({'key_id': key.key_id}, 'Key revoked. It stops working immediately.')


@api_view(['POST'])
def update_partner(request, partner_id):
    """A partner editing its own details. Scopes and status are not editable here."""
    user, partner, err = _own_partner(request, partner_id)
    if err:
        return err

    editable = ['website', 'description', 'intended_use', 'contact_name', 'contact_email',
                'legal_name', 'registration_number', 'privacy_policy_url', 'terms_url',
                'data_protection_contact']
    changed = []
    for field in editable:
        if field in request.data:
            setattr(partner, field, (request.data.get(field) or ''))
            changed.append(field)

    if 'redirect_uris' in request.data:
        uris = [u for u in (request.data.get('redirect_uris') or []) if _valid_redirect(u)]
        partner.redirect_uris = uris[:10]
        changed.append('redirect_uris')

    if request.data.get('request_sso') and partner.sso_status in ('none', 'rejected'):
        partner.sso_status = 'requested'
        changed.append('sso_status')

    if changed:
        partner.save()
    return _ok(_partner_row(partner, include_private=True), 'Saved.')


# ---------------------------------------------------------------------------
# Admin review
# ---------------------------------------------------------------------------

def _admin(request):
    user, err = _user_from_bearer(request)
    if err:
        return None, err
    if not _is_admin(user):
        return None, _err('Admins only.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    return user, None


@api_view(['GET'])
def admin_list(request):
    admin, err = _admin(request)
    if err:
        return err
    qs = Partner.objects.select_related('owner').prefetch_related('api_keys')
    state = request.GET.get('status')
    if state:
        qs = qs.filter(status=state)
    return _ok({
        'partners': [_partner_row(p, include_private=True) for p in qs],
        'scopes': SCOPES,
        'counts': {
            'pending': Partner.objects.filter(status='pending').count(),
            'approved': Partner.objects.filter(status='approved').count(),
            'sso_requested': Partner.objects.filter(sso_status='requested').count(),
        },
    }, 'Partners')


@api_view(['POST'])
def admin_review(request, partner_id):
    """Approve, reject or suspend, and say exactly which scopes are granted."""
    admin, err = _admin(request)
    if err:
        return err

    partner = Partner.objects.filter(pk=partner_id).first()
    if partner is None:
        return _err('No such partner.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    decision = (request.data.get('decision') or '').strip().lower()
    if decision not in ('approved', 'rejected', 'suspended', 'pending'):
        return _err('Decision must be approved, rejected, suspended or pending.', 'BAD_DECISION')

    with transaction.atomic():
        partner.status = decision
        partner.review_note = (request.data.get('note') or '')[:2000]
        partner.reviewed_by = admin
        partner.reviewed_at = timezone.now()

        if decision == 'approved':
            # An approval grants exactly what the admin ticks, defaulting to
            # what was asked for - never to everything.
            granted = request.data.get('scopes')
            partner.approved_scopes = valid_scopes(
                granted if granted is not None else partner.requested_scopes
            )
        elif decision in ('rejected', 'suspended'):
            partner.approved_scopes = []
            partner.api_keys.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())

        partner.save()

    try:
        emails.send_partner_decision(partner)
    except Exception:
        logger.warning('partner decision email failed', exc_info=True)

    return _ok(_partner_row(partner, include_private=True), f'Partner {decision}.')


@api_view(['POST'])
def admin_sso_review(request, partner_id):
    """SSO is approved on its own, and only for a partner that is already approved."""
    admin, err = _admin(request)
    if err:
        return err

    partner = Partner.objects.filter(pk=partner_id).first()
    if partner is None:
        return _err('No such partner.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    decision = (request.data.get('decision') or '').strip().lower()
    if decision not in ('approved', 'rejected'):
        return _err('Decision must be approved or rejected.', 'BAD_DECISION')

    if decision == 'approved':
        if partner.status != 'approved':
            return _err('Approve the partner before approving SSO.', 'PARTNER_NOT_APPROVED')
        missing = [
            field for field in ('legal_name', 'privacy_policy_url', 'data_protection_contact')
            if not getattr(partner, field)
        ]
        if missing:
            return _err(
                'SSO needs these first: ' + ', '.join(missing).replace('_', ' '),
                'MISSING_SSO_DETAILS',
            )
        if not partner.redirect_uris:
            return _err('SSO needs at least one redirect URI.', 'MISSING_REDIRECT')

        partner.sso_status = 'approved'
        partner.reviewed_by = admin
        partner.reviewed_at = timezone.now()
        secret = partner.issue_sso_credentials()
        partner.save()
        return _ok(
            {
                'partner': _partner_row(partner, include_private=True),
                'client_id': partner.sso_client_id,
                'client_secret': secret,
            },
            'SSO approved. The secret is shown once.',
        )

    partner.sso_status = 'rejected'
    partner.review_note = (request.data.get('note') or partner.review_note)[:2000]
    partner.reviewed_by = admin
    partner.reviewed_at = timezone.now()
    partner.save()
    return _ok(_partner_row(partner, include_private=True), 'SSO refused.')
