"""The gallery, with two kinds of picture and a recorded release.

CEO, 31 August 2026: "under image gallery, should be able to upload images, and
there should be another type of upload for those who want to upload their
Esports pictures, let them know that the Esports images will be used publicly
and inside events or tournaments. that they grant use of it to organizers for
those events."

The rule that shapes everything here: **a licence that is not recorded is not a
licence.** An organiser asked six months later where a photograph came from
needs an answer that is a lookup, not a memory, so the moment of consent and the
exact wording version are written onto the row at upload time. An image whose
`released_at` is null was never released, whatever its `kind` column says, and
`is_released` checks both halves so no caller can check only one.

The wording itself lives here, in one place, in all three languages, and is
served to the screen rather than retyped into it. A consent notice that exists
in two places drifts, and then nobody can say what was agreed to.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import UserGallery, Users
from .views_helpers import session_timeout_minutes

# The most a person may hold, per kind. The old limit was five for everything;
# somebody building an esports portfolio should not have to delete a holiday
# photograph to add a tournament one.
MAX_PERSONAL = 8
MAX_ESPORTS = 12

# What somebody agrees to when they release a picture. Kept as data so the
# screen shows exactly the sentence the row records the version of.
RELEASE_TERMS = {
    'version': UserGallery.RELEASE_TERMS_VERSION,
    'en': (
        'Esports pictures are public. By uploading one you grant V-ENT and the '
        'organisers of events and tournaments you take part in the right to '
        'show it on those event and tournament pages, and in the promotion of '
        'them. You keep ownership of the picture, you can delete it at any '
        'time, and deleting it stops any further use.'
    ),
    'fr': (
        'Les photos esport sont publiques. En en téléversant une, vous accordez '
        'à V-ENT et aux organisateurs des événements et tournois auxquels vous '
        'participez le droit de l’afficher sur les pages de ces événements et '
        'tournois, et dans leur promotion. Vous restez propriétaire de la photo, '
        'vous pouvez la supprimer à tout moment, et la supprimer met fin à tout '
        'usage ultérieur.'
    ),
    'pt': (
        'As fotos de esports são públicas. Ao carregar uma, concede à V-ENT e aos '
        'organizadores dos eventos e torneios em que participa o direito de a '
        'mostrar nas páginas desses eventos e torneios, e na sua promoção. '
        'Mantém a propriedade da foto, pode apagá-la a qualquer momento, e '
        'apagá-la termina qualquer uso posterior.'
    ),
}


def _error(message, code, http_status):
    return Response({'status': 'error', 'data': {}, 'message': message, 'code': code},
                    status=http_status)


def _authenticate(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None, _error('Authorization header with a Bearer token is required.',
                            'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return None, _error('Invalid session token.', 'UNAUTHORIZED',
                            status.HTTP_401_UNAUTHORIZED)
    if user.login_session_created_at is None or \
            timezone.now() - user.login_session_created_at > timedelta(
                minutes=session_timeout_minutes()):
        return None, _error('Session token has expired.', 'SESSION_EXPIRED',
                            status.HTTP_401_UNAUTHORIZED)
    return user, None


def serialize_image(request, item):
    try:
        url = request.build_absolute_uri(item.image.url) if item.image else None
    except ValueError:
        url = None
    return {
        'image_id': item.id,
        'id': item.id,
        'image': url,
        'kind': item.kind,
        'caption': item.caption,
        'is_released': item.is_released,
        'released_at': item.released_at,
        'release_terms_version': item.release_terms_version or None,
        'date_added': item.date_added,
    }


@api_view(['GET'])
@permission_classes([AllowAny])
def release_terms(request):
    """The wording somebody is agreeing to, served rather than retyped."""
    return Response({'status': 'success', 'data': RELEASE_TERMS,
                     'message': 'Release terms retrieved.'}, status=status.HTTP_200_OK)


@api_view(['POST'])
def upload_gallery(request):
    """Add pictures, of one kind, in one go.

    An esports upload without `consent: true` is refused. It would be easy to
    default it to true and let the screen show a notice, and that is precisely
    the shape that produces a licence nobody can evidence.
    """
    user, err = _authenticate(request)
    if err:
        return err

    images = request.FILES.getlist('images') or request.FILES.getlist('image')
    if not images:
        return _error('No images provided.', 'NO_IMAGES_PROVIDED',
                      status.HTTP_400_BAD_REQUEST)

    kind = (request.data.get('kind') or UserGallery.KIND_PERSONAL).strip().lower()
    if kind not in dict(UserGallery.KIND_CHOICES):
        return _error('That is not a kind of picture.', 'VALIDATION_ERROR',
                      status.HTTP_400_BAD_REQUEST)

    consent_raw = request.data.get('consent')
    consented = consent_raw in (True, 'true', 'True', '1', 1, 'on', 'yes')
    if kind == UserGallery.KIND_ESPORTS and not consented:
        return _error(
            'An esports picture needs the release to be agreed to before it can '
            'be uploaded.', 'CONSENT_REQUIRED', status.HTTP_400_BAD_REQUEST)

    limit = MAX_ESPORTS if kind == UserGallery.KIND_ESPORTS else MAX_PERSONAL
    held = UserGallery.objects.filter(user=user, kind=kind).count()
    if held + len(images) > limit:
        left = max(0, limit - held)
        return _error(
            'You can hold %d %s picture(s), and you have room for %d more.'
            % (limit, kind, left),
            'LIMIT_EXCEEDED', status.HTTP_400_BAD_REQUEST)

    caption = (request.data.get('caption') or '').strip()[:140]
    now = timezone.now()
    made = []
    for image in images:
        made.append(UserGallery.objects.create(
            user=user, image=image, kind=kind, caption=caption,
            released_at=now if kind == UserGallery.KIND_ESPORTS else None,
            release_terms_version=(UserGallery.RELEASE_TERMS_VERSION
                                   if kind == UserGallery.KIND_ESPORTS else ''),
        ))

    return Response({
        'status': 'success',
        'data': {'images': [serialize_image(request, i) for i in made]},
        'message': 'Uploaded.',
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
def withdraw_release(request):
    """Take a picture out of organisers' hands without deleting it.

    Consent that cannot be taken back is not consent. The row keeps its history
    - it becomes a personal picture with no `released_at` - so the record still
    shows that it was once released and when.
    """
    user, err = _authenticate(request)
    if err:
        return err

    item = UserGallery.objects.filter(id=request.data.get('image_id'), user=user).first()
    if item is None:
        return _error('Image not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    item.kind = UserGallery.KIND_PERSONAL
    item.released_at = None
    item.save(update_fields=['kind', 'released_at'])
    return Response({'status': 'success',
                     'data': {'image': serialize_image(request, item)},
                     'message': 'This picture is no longer released.'},
                    status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def public_gallery(request, user_id):
    """Somebody's pictures, as a stranger sees them.

    Personal pictures follow the profile's privacy setting. Released esports
    pictures do not, because releasing one is the person saying it may be shown
    publicly - that is the whole content of the release.
    """
    from .views_profile import can_view_profile, _user_from_bearer

    who = str(user_id).strip()
    owner = Users.objects.filter(username__iexact=who, is_active=True).first()
    if owner is None and who.isdigit():
        owner = Users.objects.filter(user_id=int(who), is_active=True).first()
    if owner is None:
        return _error('No such profile.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    viewer, ignored = _user_from_bearer(request)
    viewer = None if ignored else viewer

    released = list(UserGallery.objects.filter(
        user=owner, kind=UserGallery.KIND_ESPORTS, released_at__isnull=False))

    # A personal picture is part of the profile, so it is as visible as the
    # profile is and no more. A released esports picture is above, deliberately
    # outside that check: releasing one IS the person saying it may be shown.
    personal = []
    if can_view_profile(viewer, owner):
        personal = list(UserGallery.objects.filter(
            user=owner, kind=UserGallery.KIND_PERSONAL))

    return Response({
        'status': 'success',
        'data': {
            'esports': [serialize_image(request, i) for i in released],
            'personal': [serialize_image(request, i) for i in personal],
        },
        'message': 'Gallery retrieved.',
    }, status=status.HTTP_200_OK)
