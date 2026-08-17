"""Authenticated reads of KYC identity documents.

The file itself sits under PRIVATE_MEDIA_ROOT, which nginx never serves openly.
Two ways out of here, both behind the same permission check:

* production - hand nginx an `X-Accel-Redirect` pointing at the `internal`
  location that fronts PRIVATE_MEDIA_ROOT. nginx streams the bytes, Django frees
  the worker immediately, and the URL is unguessable because it only works when
  Django emits that header.
* local dev (DEBUG) - stream it through Django, because runserver has no nginx.

Who may read a document: the user who uploaded it, or an admin holding the
`review_kyc` permission. Nobody else, including other admins.
"""
import mimetypes
import os
from datetime import timedelta

from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Users, KYCDocument

SESSION_TIMEOUT_MINUTES = 120


def kyc_document_url(request, document):
    """Absolute URL of the authenticated read endpoint for this document.

    Callers used to emit `document.document_image.url`, which pointed into the
    public media tree. That attribute now raises by design, so serializers must
    use this instead. The URL still requires a Bearer token to fetch.
    """
    if not document or not document.document_image:
        return None
    return request.build_absolute_uri(f'/auth/kyc/document/{document.id}/')


def _error(message, code, http_status):
    return Response({'status': 'error', 'data': {}, 'message': message, 'code': code},
                    status=http_status)


def _authenticate(request):
    header = request.headers.get('Authorization')
    if not header or not header.startswith('Bearer '):
        return None, _error('Authorization header with a Bearer token is required.',
                            'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return None, _error('Invalid session token.', 'UNAUTHORIZED',
                            status.HTTP_401_UNAUTHORIZED)
    if user.login_session_created_at is None or \
            timezone.now() - user.login_session_created_at > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
        return None, _error('Session token has expired.', 'SESSION_EXPIRED',
                            status.HTTP_401_UNAUTHORIZED)
    return user, None


def _may_read(user, document):
    """The uploader, or an admin whose role may work the KYC queue."""
    if document.user_id == user.user_id:
        return True
    if not getattr(user, 'is_staff', False):
        return False
    from .decorators import effective_admin_role, permissions_for
    return permissions_for(effective_admin_role(user)).get('list_kyc', False)


@api_view(['GET'])
def kyc_document(request, document_id):
    user, err = _authenticate(request)
    if err:
        return err

    document = KYCDocument.objects.filter(id=document_id).select_related('user').first()
    if document is None or not document.document_image:
        return _error('Document not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if not _may_read(user, document):
        return _error('You are not allowed to view this document.', 'FORBIDDEN',
                      status.HTTP_403_FORBIDDEN)

    name = document.document_image.name          # e.g. "kyc/passport_7.jpg"
    content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'

    if settings.DEBUG:
        path = os.path.join(settings.PRIVATE_MEDIA_ROOT, name)
        if not os.path.exists(path):
            return _error('Document file missing on disk.', 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
        return FileResponse(open(path, 'rb'), content_type=content_type)

    response = HttpResponse(status=200)
    response['Content-Type'] = content_type
    response['X-Accel-Redirect'] = f"{settings.PRIVATE_MEDIA_URL}{name}"
    # Never let a proxy or the browser keep an identity document around.
    response['Cache-Control'] = 'no-store, private'
    return response
