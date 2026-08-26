"""Community - feed posts, clubs, discussion threads, scrims and direct messages.

Root-mounted (`/post/`, `/club/`, `/thread/`, `/scrim/`, `/dm/`) because that is
what the community pages call. Everything here is real data: no seeded rows, no
fabricated counts. Empty means empty.
"""
from datetime import timedelta

from django.db.models import Count, F, Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .models import (
    Users, UserProfile, Games, Teams,
    Post, PostLike, PostComment,
    Club, ClubMember,
    Thread, ThreadReply, ThreadUpvote, ThreadReplyUpvote,
    Scrim,
    Conversation, DirectMessage,
)

SESSION_TIMEOUT_MINUTES = 120
PAGE_SIZE = 20


def _error(message, code, http_status, extra=None):
    return Response({'status': 'error', 'data': extra or {}, 'message': message, 'code': code},
                    status=http_status)


def _ok(data, message):
    return Response({'status': 'success', 'data': data, 'message': message}, status=status.HTTP_200_OK)


def _created(data, message):
    return Response({'status': 'success', 'data': data, 'message': message}, status=status.HTTP_201_CREATED)


def _authenticate(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None, _error('Authorization header with a Bearer token is required.',
                            'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    token = header.split(' ', 1)[1].strip()
    user = Users.objects.filter(login_session_token=token).first() if token else None
    if user is None:
        return None, _error('Invalid session token.', 'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)
    if user.login_session_created_at is None or \
            timezone.now() - user.login_session_created_at > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
        return None, _error('Session token has expired.', 'SESSION_EXPIRED', status.HTTP_401_UNAUTHORIZED)
    return user, None


def _optional_user(request):
    user, err = _authenticate(request)
    return None if err else user


def _abs(request, filefield):
    if not filefield:
        return None
    try:
        return request.build_absolute_uri(filefield.url)
    except ValueError:
        return None


def _avatar(request, user):
    profile = UserProfile.objects.filter(user=user).first()
    return _abs(request, profile.profile_picture) if profile else None


def _person(request, user):
    return {
        'id': user.user_id,
        'user_id': user.user_id,
        'username': user.username,
        'full_name': user.full_name,
        'avatar': _avatar(request, user),
        # The founder mark, wherever a name appears. It was only ever reported
        # by the profile endpoint, so the badge showed on a profile and nowhere
        # else - not on a post, a comment, a thread or a conversation. This is
        # the one builder every community author goes through.
        #
        # Only reported when the person is wearing it: switching it off in
        # settings has to switch it off everywhere, not just on the profile.
        'founder_badge': bool(getattr(user, 'is_founder', False) and user.show_founder_badge),
    }


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

def serialize_post(request, post, viewer=None, with_comments=False):
    data = {
        'id': post.id,
        'slug': post.slug,
        'body': post.body,
        'content': post.body,          # the feed renders post.content
        'image': _abs(request, post.image),
        'game': post.game.game_title if post.game else None,
        'club': ({'id': post.club_id, 'slug': post.club.slug, 'name': post.club.name}
                 if post.club_id else None),
        'author': _person(request, post.author),
        'created_at': post.created_at,
        'like_count': post.likes.count(),
        'likes_count': post.likes.count(),
        'comment_count': post.comments.count(),
        'comments_count': post.comments.count(),
        'liked': bool(viewer and post.likes.filter(user=viewer).exists()),
        'liked_by_me': bool(viewer and post.likes.filter(user=viewer).exists()),
    }
    if with_comments:
        data['comments'] = [
            {
                'id': c.id,
                'body': c.body,
                'author': _person(request, c.author),
                'created_at': c.created_at,
            }
            for c in post.comments.select_related('author')
        ]
    return data


@api_view(['GET'])
def post_list(request):
    viewer = _optional_user(request)
    qs = Post.objects.select_related('author', 'game', 'club').prefetch_related('likes', 'comments')

    game = (request.GET.get('game') or '').strip()
    if game and game.lower() != 'all':
        qs = qs.filter(game__game_title__iexact=game)
    club_id = request.GET.get('club')
    if club_id:
        qs = qs.filter(club_id=club_id)
    search = (request.GET.get('search') or request.GET.get('q') or '').strip()
    if search:
        qs = qs.filter(Q(body__icontains=search) | Q(author__username__icontains=search))
    if (request.GET.get('filter') or '') == 'following' and viewer:
        club_ids = ClubMember.objects.filter(user=viewer).values_list('club_id', flat=True)
        qs = qs.filter(club_id__in=list(club_ids))

    try:
        page = max(int(request.GET.get('page', 1)), 1)
    except (TypeError, ValueError):
        page = 1
    start = (page - 1) * PAGE_SIZE
    rows = [serialize_post(request, p, viewer) for p in qs[start:start + PAGE_SIZE]]

    return _ok({'posts': rows, 'count': qs.count(), 'page': page, 'per_page': PAGE_SIZE},
               'Posts retrieved.')


@api_view(['POST'])
def post_create(request):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    body = (request.data.get('body') or request.data.get('content') or '').strip()
    if not body:
        return _error('Write something first.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if len(body) > 5000:
        return _error('That post is too long (5,000 characters max).',
                      'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    game = None
    game_name = (request.data.get('game') or '').strip()
    if game_name and game_name.lower() != 'all':
        game = Games.objects.filter(game_title__iexact=game_name).first()

    club = None
    if request.data.get('club_id'):
        club = Club.objects.filter(id=request.data.get('club_id')).first()
        if club and club.is_private and not ClubMember.objects.filter(club=club, user=user).exists():
            return _error('You are not a member of that club.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    post = Post.objects.create(author=user, body=body, game=game, club=club)
    return _created({'post': serialize_post(request, post, user)}, 'Posted.')


@api_view(['GET'])
def post_detail(request, post_id):
    from vent_auth.slugs import lookup_kwargs

    post = (
        Post.objects.select_related('author', 'game', 'club')
        .filter(**lookup_kwargs(post_id, id_field='id'))
        .first()
    )
    if post is None:
        return _error('Post not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return _ok({'post': serialize_post(request, post, _optional_user(request), with_comments=True)},
               'Post retrieved.')


@api_view(['POST'])
def post_like(request, post_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    post = Post.objects.filter(id=post_id).first()
    if post is None:
        return _error('Post not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    like = PostLike.objects.filter(post=post, user=user).first()
    if like:
        like.delete()
        liked = False
    else:
        PostLike.objects.create(post=post, user=user)
        liked = True

    return _ok({'liked': liked, 'liked_by_me': liked, 'like_count': post.likes.count()},
               'Liked.' if liked else 'Like removed.')


@api_view(['POST'])
def post_comment(request, post_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    post = Post.objects.filter(id=post_id).first()
    if post is None:
        return _error('Post not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    body = (request.data.get('body') or request.data.get('comment') or '').strip()
    if not body:
        return _error('Write a comment first.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    comment = PostComment.objects.create(post=post, author=user, body=body[:1000])

    if post.author_id != user.user_id:
        try:
            from .views_notifications import create_notification
            create_notification(
                user=post.author, category='mention',
                title=f'@{user.username} commented on your post',
                body=body[:120], link='/community',
                metadata={'post_id': post.id},
            )
        except Exception:
            pass

    return _created(
        {'comment': {'id': comment.id, 'body': comment.body, 'author': _person(request, user),
                     'created_at': comment.created_at},
         'comment_count': post.comments.count()},
        'Comment added.',
    )


# ---------------------------------------------------------------------------
# Clubs
# ---------------------------------------------------------------------------

def serialize_club(request, club, viewer=None):
    return {
        'id': club.id,
        'slug': club.slug,
        'name': club.name,
        'description': club.description,
        'game': club.game.game_title if club.game else None,
        'logo': _abs(request, club.logo),
        'banner': _abs(request, club.banner),
        'is_private': club.is_private,
        'owner': _person(request, club.owner),
        'member_count': club.members.count(),
        'post_count': club.posts.count(),
        'joined': bool(viewer and club.members.filter(user=viewer).exists()),
        'is_joined': bool(viewer and club.members.filter(user=viewer).exists()),
        'is_owner': bool(viewer and club.owner_id == viewer.user_id),
        'created_at': club.created_at,
    }


@api_view(['GET'])
def club_list(request):
    viewer = _optional_user(request)
    qs = Club.objects.select_related('game', 'owner').prefetch_related('members', 'posts')

    game = (request.GET.get('game') or '').strip()
    if game and game.lower() != 'all':
        qs = qs.filter(game__game_title__iexact=game)
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))

    rows = [serialize_club(request, c, viewer) for c in qs]
    return _ok({'clubs': rows, 'count': len(rows)}, 'Clubs retrieved.')


@api_view(['POST'])
def club_create(request):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    name = (request.data.get('name') or '').strip()
    if not name:
        return _error('A club name is required.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if Club.objects.filter(name__iexact=name).exists():
        return _error('A club with that name already exists.', 'DUPLICATE', status.HTTP_409_CONFLICT)

    game = Games.objects.filter(game_title__iexact=(request.data.get('game') or '').strip()).first()
    club = Club.objects.create(
        name=name[:120],
        description=(request.data.get('description') or '').strip(),
        game=game,
        owner=user,
        is_private=bool(request.data.get('is_private')),
    )
    ClubMember.objects.create(club=club, user=user)
    return _created({'club': serialize_club(request, club, user)}, f'{club.name} created.')


@api_view(['GET'])
def club_detail(request, club_id):
    from vent_auth.slugs import resolve_or_redirect

    club, moved_to = resolve_or_redirect(
        club_id, entity_type='club', id_field='id', model=Club,
        queryset=Club.objects.select_related('game', 'owner'),
    )
    if moved_to:
        return Response({
            'status': 'moved', 'code': 'SLUG_CHANGED',
            'message': 'This club has been renamed.',
            'data': {'slug': moved_to, 'url': f'/community/club/{moved_to}'},
        }, status=status.HTTP_200_OK)
    if club is None:
        return _error('Club not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    viewer = _optional_user(request)
    members = [
        _person(request, m.user) for m in club.members.select_related('user')[:50]
    ]
    posts = [
        serialize_post(request, p, viewer)
        for p in club.posts.select_related('author', 'game')[:PAGE_SIZE]
    ]
    return _ok({'club': serialize_club(request, club, viewer), 'members': members, 'posts': posts},
               'Club retrieved.')


@api_view(['POST'])
def club_join(request, club_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    club = Club.objects.filter(id=club_id).first()
    if club is None:
        return _error('Club not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    membership = ClubMember.objects.filter(club=club, user=user).first()
    if membership:
        if club.owner_id == user.user_id:
            return _error('The owner cannot leave their own club.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)
        membership.delete()
        joined = False
    else:
        ClubMember.objects.create(club=club, user=user)
        joined = True

    return _ok({'joined': joined, 'member_count': club.members.count()},
               f"{'Joined' if joined else 'Left'} {club.name}.")


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

def serialize_thread(request, t, with_replies=False, viewer=None):
    data = {
        'id': t.id,
        'slug': t.slug,
        'title': t.title,
        'body': t.body,
        'category': t.category,
        'author': _person(request, t.author),
        'club': ({'id': t.club_id, 'slug': t.club.slug, 'name': t.club.name}
                 if t.club_id else None),
        'reply_count': t.replies.count(),
        'upvotes': t.upvotes.count(),
        'upvoted': bool(viewer and t.upvotes.filter(user=viewer).exists()),
        'view_count': t.view_count,
        'is_pinned': t.is_pinned,
        'is_locked': t.is_locked,
        'created_at': t.created_at,
        'last_activity_at': t.last_activity_at,
    }
    if with_replies:
        data['replies'] = [
            serialize_reply(request, r, viewer)
            for r in t.replies.select_related('author').prefetch_related('upvotes')
        ]
    return data


def serialize_reply(request, r, viewer=None):
    return {
        'id': r.id,
        'body': r.body,
        'author': _person(request, r.author),
        'created_at': r.created_at,
        'upvotes': r.upvotes.count(),
        'upvoted': bool(viewer and r.upvotes.filter(user=viewer).exists()),
    }


@api_view(['GET'])
def thread_list(request):
    viewer = _optional_user(request)
    qs = Thread.objects.select_related('author', 'club').prefetch_related('replies', 'upvotes')
    category = (request.GET.get('category') or '').strip()
    if category and category.lower() != 'all':
        qs = qs.filter(category__iexact=category)
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(body__icontains=search))

    rows = [serialize_thread(request, t, viewer=viewer) for t in qs[:PAGE_SIZE]]
    return _ok({'threads': rows, 'count': qs.count()}, 'Threads retrieved.')


@api_view(['POST'])
def thread_create(request):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    title = (request.data.get('title') or '').strip()
    body = (request.data.get('body') or '').strip()
    if not title or not body:
        return _error('A title and a body are required.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    category = (request.data.get('category') or 'general').strip().lower()
    if category not in dict(Thread.CATEGORY_CHOICES):
        category = 'general'

    thread = Thread.objects.create(
        title=title[:180], body=body, category=category, author=user,
        club=Club.objects.filter(id=request.data.get('club_id')).first() if request.data.get('club_id') else None,
    )
    return _created({'thread': serialize_thread(request, thread)}, 'Thread posted.')


@api_view(['GET'])
def thread_detail(request, thread_id):
    from vent_auth.slugs import resolve_or_redirect

    thread, moved_to = resolve_or_redirect(
        thread_id, entity_type='thread', id_field='id', model=Thread,
        queryset=Thread.objects.select_related('author', 'club'),
    )
    if moved_to:
        return Response({
            'status': 'moved', 'code': 'SLUG_CHANGED',
            'message': 'This thread has been renamed.',
            'data': {'slug': moved_to, 'url': f'/community/thread/{moved_to}'},
        }, status=status.HTTP_200_OK)
    if thread is None:
        return _error('Thread not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    viewer = _optional_user(request)
    # Count the read before serialising so the number the reader sees includes it.
    Thread.objects.filter(id=thread.id).update(view_count=F('view_count') + 1)
    thread.view_count += 1

    return _ok({'thread': serialize_thread(request, thread, with_replies=True, viewer=viewer)},
               'Thread retrieved.')


@api_view(['POST'])
def thread_reply(request, thread_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    thread = Thread.objects.filter(id=thread_id).first()
    if thread is None:
        return _error('Thread not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if thread.is_locked:
        return _error('This thread is locked.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    body = (request.data.get('body') or '').strip()
    if not body:
        return _error('Write a reply first.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    reply = ThreadReply.objects.create(thread=thread, author=user, body=body)
    thread.last_activity_at = timezone.now()
    thread.save(update_fields=['last_activity_at'])

    return _created(
        {'reply': serialize_reply(request, reply, user),
         'reply_count': thread.replies.count()},
        'Reply posted.',
    )


@api_view(['POST'])
def thread_upvote(request, thread_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    thread = Thread.objects.filter(id=thread_id).first()
    if thread is None:
        return _error('Thread not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    existing = ThreadUpvote.objects.filter(thread=thread, user=user).first()
    if existing:
        existing.delete()
        upvoted = False
    else:
        ThreadUpvote.objects.create(thread=thread, user=user)
        upvoted = True

    return _ok({'upvoted': upvoted, 'upvotes': thread.upvotes.count()},
               'Upvote added.' if upvoted else 'Upvote removed.')


@api_view(['POST'])
def thread_reply_upvote(request, reply_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    reply = ThreadReply.objects.filter(id=reply_id).first()
    if reply is None:
        return _error('Reply not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    existing = ThreadReplyUpvote.objects.filter(reply=reply, user=user).first()
    if existing:
        existing.delete()
        upvoted = False
    else:
        ThreadReplyUpvote.objects.create(reply=reply, user=user)
        upvoted = True

    return _ok({'upvoted': upvoted, 'upvotes': reply.upvotes.count()},
               'Upvote added.' if upvoted else 'Upvote removed.')


# ---------------------------------------------------------------------------
# Scrims
# ---------------------------------------------------------------------------

def serialize_scrim(request, s, viewer=None):
    team = {'id': s.team_id, 'name': s.team.team_name, 'tag': None,
            'logo': _abs(request, s.team.team_logo)} if s.team_id else None
    opponent = {'id': s.opponent_id, 'name': s.opponent.team_name, 'tag': None} if s.opponent_id else None
    return {
        'id': s.id,
        'slug': s.slug,
        'team': team,
        'opponent': opponent,
        # The scrims table renders team_a / team_b / scheduled_at / format.
        'team_a': team,
        'team_b': opponent,
        'opponent_open_or_team_b': {'open': opponent is None, 'opponent': opponent},
        'scheduled_at': s.scheduled_for,
        'format': s.match_format,
        'region': s.region,
        'game': s.game.game_title if s.game else None,
        'scheduled_for': s.scheduled_for,
        'notes': s.notes,
        'status': s.status,
        'created_by': _person(request, s.created_by),
        # A team cannot scrim itself, so the poster never sees an Accept button.
        'is_mine': bool(viewer and s.created_by_id == viewer.user_id),
        'challenged': {'id': s.challenged_id, 'name': s.challenged.team_name} if s.challenged_id else None,
        'created_at': s.created_at,
    }


@api_view(['GET'])
def scrim_list(request):
    viewer = _optional_user(request)
    qs = Scrim.objects.select_related('team', 'opponent', 'challenged', 'game', 'created_by')
    wanted = (request.GET.get('status') or '').strip()
    if wanted and wanted.lower() != 'all':
        qs = qs.filter(status__iexact=wanted)
    game = (request.GET.get('game') or '').strip()
    if game and game.lower() != 'all':
        qs = qs.filter(game__game_title__iexact=game)
    region = (request.GET.get('region') or '').strip()
    if region and region.lower() != 'all':
        qs = qs.filter(region__iexact=region)

    rows = [serialize_scrim(request, s, viewer) for s in qs[:PAGE_SIZE]]
    return _ok({'scrims': rows, 'count': qs.count()}, 'Scrims retrieved.')


@api_view(['POST'])
def scrim_create(request):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    team = Teams.objects.filter(team_id=request.data.get('team_id')).first()
    if team is None:
        return _error('Pick one of your teams.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    from .models import TeamMembers
    is_member = team.team_owner_id == user.user_id or TeamMembers.objects.filter(team=team, user=user).exists()
    if not is_member:
        return _error('You can only post scrims for a team you belong to.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    # The poster picks the game; fall back to whatever the team plays.
    game_title = (request.data.get('game') or '').strip()
    game = Games.objects.filter(game_title__iexact=game_title).first() if game_title else None

    # Naming an opponent turns the post into a direct challenge: only that team
    # can accept it. Leaving it blank keeps the slot open to anyone.
    opponent = None
    opponent_name = (request.data.get('opponent') or '').strip()
    if opponent_name:
        opponent = Teams.objects.filter(team_name__iexact=opponent_name).first()
        if opponent is None:
            return _error(f'No team called "{opponent_name}".', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        if opponent.team_id == team.team_id:
            return _error('A team cannot scrim itself.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    scrim = Scrim.objects.create(
        team=team,
        game=game or team.game,
        created_by=user,
        challenged=opponent,
        scheduled_for=request.data.get('scheduled_for') or request.data.get('scheduled_at') or None,
        match_format=(request.data.get('format') or '').strip()[:20],
        region=(request.data.get('region') or '').strip()[:40],
        notes=(request.data.get('notes') or '').strip()[:280],
    )

    if opponent is not None:
        try:
            from .views_notifications import create_notification
            create_notification(
                user=opponent.team_owner, category='team',
                title=f'{team.team_name} challenged you to a scrim',
                body=scrim.notes, link='/community?tab=scrims',
                metadata={'scrim_id': scrim.id},
            )
        except Exception:
            pass

    return _created({'scrim': serialize_scrim(request, scrim, user)}, 'Scrim posted.')


@api_view(['POST'])
def scrim_accept(request, scrim_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    scrim = Scrim.objects.select_related('team').filter(id=scrim_id).first()
    if scrim is None:
        return _error('Scrim not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if scrim.status != 'open':
        return _error(f'That scrim is already {scrim.status}.', 'STATE_CONFLICT', status.HTTP_409_CONFLICT)

    opponent = Teams.objects.filter(team_id=request.data.get('team_id')).first()
    if opponent is None:
        return _error('Pick the team you are bringing.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if opponent.team_id == scrim.team_id:
        return _error('A team cannot scrim itself.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
    if scrim.challenged_id and scrim.challenged_id != opponent.team_id:
        return _error(f'This scrim was aimed at {scrim.challenged.team_name}.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    from .models import TeamMembers
    is_member = opponent.team_owner_id == user.user_id or TeamMembers.objects.filter(team=opponent, user=user).exists()
    if not is_member:
        return _error('You can only accept with a team you belong to.',
                      'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    scrim.opponent = opponent
    scrim.status = 'accepted'
    scrim.save(update_fields=['opponent', 'status'])

    try:
        from .views_notifications import create_notification
        create_notification(
            user=scrim.created_by, category='team',
            title=f'{opponent.team_name} accepted your scrim',
            body=scrim.notes, link='/community',
            metadata={'scrim_id': scrim.id},
        )
    except Exception:
        pass

    return _ok({'scrim': serialize_scrim(request, scrim, user)}, f'{opponent.team_name} is in.')


# ---------------------------------------------------------------------------
# Direct messages
# ---------------------------------------------------------------------------

def _conversation_for(user_a, user_b):
    lo, hi = sorted([user_a, user_b], key=lambda u: u.user_id)
    convo, _ = Conversation.objects.get_or_create(user_a=lo, user_b=hi)
    return convo


def serialize_conversation(request, convo, viewer):
    other = convo.user_b if convo.user_a_id == viewer.user_id else convo.user_a
    last = convo.messages.last()
    return {
        'id': convo.id,
        'with': _person(request, other),
        'last_message': last.body[:140] if last else '',
        'last_message_at': convo.last_message_at,
        'unread_count': convo.messages.filter(read_at__isnull=True).exclude(sender=viewer).count(),
    }


@api_view(['GET'])
def dm_list(request):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    convos = (
        Conversation.objects
        .filter(Q(user_a=user) | Q(user_b=user))
        .select_related('user_a', 'user_b')
        .prefetch_related('messages')
    )
    rows = [serialize_conversation(request, c, user) for c in convos]
    return _ok({'conversations': rows, 'count': len(rows)}, 'Conversations retrieved.')


@api_view(['GET'])
def dm_detail(request, conversation_id):
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    convo = Conversation.objects.select_related('user_a', 'user_b').filter(id=conversation_id).first()
    if convo is None:
        return _error('Conversation not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if user.user_id not in (convo.user_a_id, convo.user_b_id):
        return _error('This conversation is not yours.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    convo.messages.filter(read_at__isnull=True).exclude(sender=user).update(read_at=timezone.now())

    messages = [
        {
            'id': m.id,
            'body': m.body,
            'sender': _person(request, m.sender),
            'mine': m.sender_id == user.user_id,
            'created_at': m.created_at,
            'read_at': m.read_at,
        }
        for m in convo.messages.select_related('sender')
    ]
    return _ok({'conversation': serialize_conversation(request, convo, user), 'messages': messages},
               'Conversation retrieved.')


@api_view(['POST'])
def dm_send(request, conversation_id):
    """Send into an existing conversation, or start one with `username`."""
    user, auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    body = (request.data.get('body') or request.data.get('message') or '').strip()
    if not body:
        return _error('Write a message first.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)

    if str(conversation_id) == 'new':
        other = Users.objects.filter(username=(request.data.get('username') or '').strip()).first()
        if other is None:
            return _error('No user with that username.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        if other.user_id == user.user_id:
            return _error('You cannot message yourself.', 'VALIDATION_ERROR', status.HTTP_400_BAD_REQUEST)
        # `allow_direct_messages` was written by the Privacy panel and read by
        # nothing, so somebody who had turned messages off still received them.
        # Checked here rather than only in the client, because a setting only
        # the client honours is not a setting.
        from .views_usersearch import may_message
        if not may_message(user, other):
            return _error(
                f'@{other.username} does not accept direct messages.',
                'DM_NOT_ALLOWED', status.HTTP_403_FORBIDDEN,
            )
        convo = _conversation_for(user, other)
    else:
        convo = Conversation.objects.filter(id=conversation_id).first()
        if convo is None:
            return _error('Conversation not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
        if user.user_id not in (convo.user_a_id, convo.user_b_id):
            return _error('This conversation is not yours.', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    message = DirectMessage.objects.create(conversation=convo, sender=user, body=body)
    convo.last_message_at = timezone.now()
    convo.save(update_fields=['last_message_at'])

    other = convo.user_b if convo.user_a_id == user.user_id else convo.user_a
    try:
        from .views_notifications import create_notification
        create_notification(
            user=other, category='mention',
            title=f'New message from @{user.username}',
            body=body[:120], link='/community/dm',
            metadata={'conversation_id': convo.id},
        )
    except Exception:
        pass

    return _created(
        {'conversation_id': convo.id,
         'message': {'id': message.id, 'body': message.body, 'mine': True,
                     'created_at': message.created_at}},
        'Message sent.',
    )
