"""Reports somebody filed, and the content an admin can act on.

CEO, 7 September 2026, from the admin dashboard spec:

    Content Management: review user-generated content, approve or reject
    submissions, moderate content, enforce guidelines and take action on
    reported content.
    Manage Communities: moderate discussions, enforce community guidelines,
    highlight community content.

## What was actually there before this file

`UserReport` had a docstring saying "the point of a report is the queue" and
"a report that goes nowhere is the same fake as the toast this replaces". It
had a status column, an `admin_note`, a `reviewed_by` and a `reviewed_at`.

Nothing anywhere in the codebase read a single one of them. Every report a
member filed went into a table with no reader, and `admin_get_user` returned
`'reports': []` as a literal. So the first thing here is the queue that model
was written for.

## Highlighting was a column nobody could write

`Thread.is_pinned` and `Thread.is_locked` are serialised to every reader of
`/community/threads/`, and no endpoint on the platform ever set either one.
The ordering `['-is_pinned', '-last_activity_at']` has been sorting by a field
that could only ever be False. Highlighting community content, which the spec
asks for by name, was one write away and had no way in.

## What is deliberately NOT here

The spec's content management names "manga, AMVs, other media". Anime is Phase
5 and there is no manga and no AMV on this platform to review. Inventing an
approval queue for a content type nobody can submit is a screen of controls
that do nothing, so the console says that in a sentence instead. What DOES
exist is posts, threads, replies, club messages and profile galleries, and
those are what this moderates.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .decorators import ROLE_PERMISSIONS, admin_role_required
from .models import (AdminAction, Club, ClubMessage, Post, PostComment, Thread,
                     ThreadReply, UserGallery, UserReport)

MODERATE_ROLES = ROLE_PERMISSIONS['moderate_content']
BAN_ROLES = ROLE_PERMISSIONS['ban_users']

# What an admin may do to a report, and the status each one lands on. Named
# once so the screen and the endpoint cannot drift apart about what "actioned"
# means.
REPORT_ACTIONS = {
    'review': 'reviewing',
    'action': 'actioned',
    'dismiss': 'dismissed',
    'reopen': 'open',
}


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _person(user):
    """One description of a person, the same one every other screen draws."""
    if user is None:
        return None
    from .views_community import _person as build

    row = build(None, user)
    row['email'] = user.email
    return row


def _record(admin, action, model, target_id, reason='', **metadata):
    AdminAction.objects.create(
        admin=admin, action_type=action, target_model=model,
        target_id=str(target_id), reason=reason or '', metadata=metadata or {})


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

def _report_row(row):
    return {
        'id': row.id,
        'reason': row.reason,
        'reason_label': dict(UserReport.REASONS).get(row.reason, row.reason),
        'detail': row.detail,
        'context': row.context,
        'status': row.status,
        'admin_note': row.admin_note,
        'reporter': _person(row.reporter),
        'reported': _person(row.reported),
        'reviewed_by': row.reviewed_by.username if row.reviewed_by_id else '',
        'reviewed_at': row.reviewed_at.isoformat() if row.reviewed_at else None,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


@api_view(['GET'])
@admin_role_required(MODERATE_ROLES)
def admin_reports(request):
    """The report queue, open ones first.

    The counts by status ride along with the list rather than needing a second
    request: "how many are waiting" is the question somebody opens this screen
    to answer, and a number that arrives after the table is a number they have
    already scrolled past.
    """
    rows = UserReport.objects.select_related(
        'reporter', 'reported', 'reviewed_by')

    state = (request.GET.get('status') or 'open').strip().lower()
    if state and state != 'all':
        if state not in dict(UserReport.STATUS):
            return _err('That is not a report status.', 'VALIDATION_ERROR')
        rows = rows.filter(status=state)

    term = (request.GET.get('q') or '').strip()
    if term:
        rows = rows.filter(
            Q(reported__username__icontains=term)
            | Q(reporter__username__icontains=term)
            | Q(detail__icontains=term))

    counts = {value: 0 for value, _label in UserReport.STATUS}
    for group in UserReport.objects.values('status').annotate(n=Count('id')):
        counts[group['status']] = group['n']

    return _ok({
        'results': [_report_row(r) for r in rows[:200]],
        'counts': counts,
        'statuses': [{'value': v, 'label': label}
                     for v, label in UserReport.STATUS],
        'reasons': [{'value': v, 'label': label}
                    for v, label in UserReport.REASONS],
    })


@api_view(['POST'])
@admin_role_required(MODERATE_ROLES)
def admin_report_action(request, report_id):
    """Move a report through the queue, and say what was done about it.

    `also_ban` is here rather than as a separate trip to the users screen
    because the moment somebody decides a report is founded is the moment they
    want to act on it, and a moderator who has to go and find the account again
    is a moderator who sometimes does not. It is still the ban permission that
    decides, not this one: a role that may moderate content and may not ban
    people gets the decision and not the ban.
    """
    admin = request.admin_user
    row = UserReport.objects.filter(pk=report_id).select_related(
        'reported').first()
    if row is None:
        return _err('No report with that number.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    action = str(request.data.get('action') or '').strip().lower()
    if action not in REPORT_ACTIONS:
        return _err('Say what to do with it.', 'VALIDATION_ERROR')

    note = str(request.data.get('note') or '').strip()[:2000]
    if action in ('action', 'dismiss') and not note:
        # A decision with no reason on it is a decision nobody can review, and
        # the person it was made about is the one who cannot see why.
        return _err('Say what you decided and why.', 'REASON_REQUIRED')

    row.status = REPORT_ACTIONS[action]
    row.admin_note = note or row.admin_note
    row.reviewed_by = admin
    row.reviewed_at = timezone.now()
    row.save(update_fields=['status', 'admin_note', 'reviewed_by',
                            'reviewed_at'])

    banned = False
    if request.data.get('also_ban'):
        from .decorators import effective_admin_role
        if effective_admin_role(admin) not in BAN_ROLES:
            return _err('Your role does not ban accounts.', 'NOT_ALLOWED',
                        status.HTTP_403_FORBIDDEN)
        target = row.reported
        if target.user_id == admin.user_id:
            return _err('That report is about you.', 'VALIDATION_ERROR')
        target.is_active = False
        target.save(update_fields=['is_active'])
        _record(admin, 'ban_user', 'User', target.user_id,
                reason='report %s: %s' % (row.id, note),
                username=target.username, from_report=row.id)
        banned = True

    _record(admin, 'report_%s' % action, 'UserReport', row.id, reason=note,
            reported=row.reported.username, banned=banned)

    return _ok({'report': _report_row(row), 'banned': banned},
               'Report updated.')


# ---------------------------------------------------------------------------
# The content itself
# ---------------------------------------------------------------------------

def _post_row(post):
    return {
        'kind': 'post',
        'ref': post.slug,
        'body': post.body[:400],
        'author': _person(post.author),
        'club': post.club.name if post.club_id else '',
        'likes': post.likes.count(),
        'comments': post.comments.count(),
        'created_at': post.created_at.isoformat() if post.created_at else None,
        'url': '/community/post/%s' % post.slug,
    }


def _thread_row(thread):
    return {
        'kind': 'thread',
        'ref': thread.slug,
        'title': thread.title,
        'body': thread.body[:400],
        'author': _person(thread.author),
        'club': thread.club.name if thread.club_id else '',
        'category': thread.category,
        'replies': thread.replies.count(),
        'upvotes': thread.upvotes.count(),
        'views': thread.view_count,
        'is_pinned': thread.is_pinned,
        'is_locked': thread.is_locked,
        'created_at': thread.created_at.isoformat() if thread.created_at else None,
        'url': '/community/thread/%s' % thread.slug,
    }


def _gallery_row(image):
    return {
        'kind': 'gallery',
        'ref': str(image.pk),
        'caption': image.caption,
        'author': _person(image.user),
        'image': image.image.url if image.image else None,
        # An esports picture is one somebody licensed to organisers. Whether
        # that licence was really granted is the only question worth asking
        # about it, so the answer is on the row rather than implied by `kind`.
        'released': image.is_released,
        'kind_of_image': image.kind,
        'created_at': image.date_added.isoformat() if image.date_added else None,
    }


@api_view(['GET'])
@admin_role_required(MODERATE_ROLES)
def admin_content(request):
    """Recent user content of one kind, newest first, with its engagement.

    `?kind=` is posts, threads or gallery. One kind at a time rather than a
    merged river: the actions differ per kind, and a table whose buttons change
    row by row is a table people misread.
    """
    kind = (request.GET.get('kind') or 'threads').strip().lower()
    term = (request.GET.get('q') or '').strip()

    if kind == 'posts':
        rows = Post.objects.select_related('author', 'club')
        if term:
            rows = rows.filter(Q(body__icontains=term)
                               | Q(author__username__icontains=term))
        results = [_post_row(p) for p in rows.order_by('-created_at')[:100]]
    elif kind == 'gallery':
        rows = UserGallery.objects.select_related('user')
        if term:
            rows = rows.filter(Q(caption__icontains=term)
                               | Q(user__username__icontains=term))
        results = [_gallery_row(g) for g in rows.order_by('-date_added')[:100]]
    elif kind == 'threads':
        rows = Thread.objects.select_related('author', 'club')
        if term:
            rows = rows.filter(Q(title__icontains=term)
                               | Q(body__icontains=term)
                               | Q(author__username__icontains=term))
        results = [_thread_row(t) for t in
                   rows.order_by('-is_pinned', '-created_at')[:100]]
    else:
        return _err('There is no content of that kind.', 'VALIDATION_ERROR')

    week = timezone.now() - timedelta(days=7)
    return _ok({
        'kind': kind,
        'results': results,
        'count': len(results),
        'engagement': {
            'posts_7d': Post.objects.filter(created_at__gte=week).count(),
            'threads_7d': Thread.objects.filter(created_at__gte=week).count(),
            'replies_7d': ThreadReply.objects.filter(
                created_at__gte=week).count(),
            'messages_7d': ClubMessage.objects.filter(
                created_at__gte=week, deleted_at__isnull=True).count(),
            'gallery_7d': UserGallery.objects.filter(
                date_added__gte=week).count(),
            'reports_open': UserReport.objects.filter(status='open').count(),
        },
        # Said by the API rather than hardcoded into the screen, so the day the
        # anime module ships this sentence goes away in one place.
        'not_built': [
            {'what': 'manga', 'phase': 5},
            {'what': 'amv', 'phase': 5},
            {'what': 'marketplace_listings', 'phase': 4},
            {'what': 'shop_products', 'phase': 3},
        ],
    })


@api_view(['POST'])
@admin_role_required(MODERATE_ROLES)
def admin_content_action(request, kind, ref):
    """Delete, highlight or lock one piece of content.

    Every one of these writes an AdminAction naming what was removed and why.
    A deletion nobody can reconstruct is how a platform loses an argument with
    the person whose post went missing.
    """
    admin = request.admin_user
    action = str(request.data.get('action') or '').strip().lower()
    reason = str(request.data.get('reason') or '').strip()[:500]

    if action == 'delete' and not reason:
        return _err('Say why this is being removed.', 'REASON_REQUIRED')

    kind = (kind or '').strip().lower()

    if kind == 'thread':
        row = Thread.objects.filter(slug=str(ref)).first()
        if row is None:
            return _err('No thread at that address.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        if action == 'delete':
            _record(admin, 'delete_thread', 'Thread', row.pk, reason=reason,
                    title=row.title, author=row.author.username)
            row.delete()
            return _ok({}, 'Thread removed.')
        if action in ('pin', 'unpin'):
            row.is_pinned = (action == 'pin')
            row.save(update_fields=['is_pinned'])
            _record(admin, 'highlight_thread', 'Thread', row.pk, reason=reason,
                    title=row.title, pinned=row.is_pinned)
            return _ok({'thread': _thread_row(row)},
                       'Highlighted.' if row.is_pinned else 'No longer highlighted.')
        if action in ('lock', 'unlock'):
            row.is_locked = (action == 'lock')
            row.save(update_fields=['is_locked'])
            _record(admin, 'lock_thread', 'Thread', row.pk, reason=reason,
                    title=row.title, locked=row.is_locked)
            return _ok({'thread': _thread_row(row)},
                       'Locked.' if row.is_locked else 'Unlocked.')
        return _err('Say what to do with it.', 'VALIDATION_ERROR')

    if kind == 'post':
        row = Post.objects.filter(slug=str(ref)).first()
        if row is None:
            return _err('No post at that address.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        if action != 'delete':
            return _err('A post can be removed and nothing else.',
                        'VALIDATION_ERROR')
        _record(admin, 'delete_post', 'Post', row.pk, reason=reason,
                author=row.author.username, body=row.body[:200])
        row.delete()
        return _ok({}, 'Post removed.')

    if kind == 'gallery':
        if not str(ref).isdigit():
            return _err('No image with that number.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        row = UserGallery.objects.filter(pk=int(ref)).first()
        if row is None:
            return _err('No image with that number.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        if action == 'delete':
            _record(admin, 'delete_gallery_image', 'UserGallery', row.pk,
                    reason=reason, owner=row.user.username)
            row.delete()
            return _ok({}, 'Image removed.')
        if action == 'revoke_release':
            # The licence is withdrawn, not the picture. Somebody who asked us
            # to stop using their face in event artwork is asking for that and
            # not to have their profile emptied.
            row.kind = UserGallery.KIND_PERSONAL
            row.released_at = None
            row.save(update_fields=['kind', 'released_at'])
            _record(admin, 'revoke_image_release', 'UserGallery', row.pk,
                    reason=reason, owner=row.user.username)
            return _ok({'image': _gallery_row(row)},
                       'Organisers can no longer use it.')
        return _err('Say what to do with it.', 'VALIDATION_ERROR')

    if kind == 'message':
        if not str(ref).isdigit():
            return _err('No message with that number.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        row = ClubMessage.objects.filter(pk=int(ref)).first()
        if row is None:
            return _err('No message with that number.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        if action != 'delete':
            return _err('A message can be removed and nothing else.',
                        'VALIDATION_ERROR')
        # Soft, like every other deletion of a club message, so a thread does
        # not lose the replies that answered it.
        row.deleted_at = timezone.now()
        row.deleted_by = admin
        row.save(update_fields=['deleted_at', 'deleted_by'])
        _record(admin, 'delete_club_message', 'ClubMessage', row.pk,
                reason=reason,
                author=row.author.username if row.author_id else '')
        return _ok({}, 'Message removed.')

    if kind == 'comment':
        if not str(ref).isdigit():
            return _err('No comment with that number.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        row = PostComment.objects.filter(pk=int(ref)).first()
        if row is None:
            return _err('No comment with that number.', 'NOT_FOUND',
                        status.HTTP_404_NOT_FOUND)
        if action != 'delete':
            return _err('A comment can be removed and nothing else.',
                        'VALIDATION_ERROR')
        _record(admin, 'delete_comment', 'PostComment', row.pk, reason=reason,
                author=row.author.username)
        row.delete()
        return _ok({}, 'Comment removed.')

    return _err('There is no content of that kind.', 'VALIDATION_ERROR')


# ---------------------------------------------------------------------------
# Communities, with what happens inside them
# ---------------------------------------------------------------------------

@api_view(['GET'])
@admin_role_required(ROLE_PERMISSIONS['manage_communities'])
def admin_community_detail(request, slug):
    """One club: who is in it, what is being said, and what can be acted on.

    The listing already said how busy a club is. This is the screen somebody
    opens when the answer to that was surprising.
    """
    club = Club.objects.filter(slug=str(slug)).first()
    if club is None:
        return _err('No community at that address.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    from .models import ClubMember, ClubTopic

    members = ClubMember.objects.filter(club=club).select_related('user')
    threads = Thread.objects.filter(club=club).select_related('author')
    messages = (ClubMessage.objects
                .filter(topic__club=club, deleted_at__isnull=True)
                .select_related('author', 'topic').order_by('-created_at')[:50])

    return _ok({
        'club': {
            'slug': club.slug,
            'name': club.name,
            'description': club.description,
            'members': members.count(),
            'topics': ClubTopic.objects.filter(club=club).count(),
            'threads': threads.count(),
            'messages': ClubMessage.objects.filter(
                topic__club=club, deleted_at__isnull=True).count(),
        },
        'members': [{'user': _person(m.user), 'role': m.role}
                    for m in members[:100]],
        'threads': [_thread_row(t) for t in
                    threads.order_by('-is_pinned', '-created_at')[:50]],
        'messages': [{
            'id': m.pk,
            'topic': m.topic.name if m.topic_id else '',
            'author': _person(m.author),
            'body': (m.body or '')[:300],
            'created_at': m.created_at.isoformat() if m.created_at else None,
        } for m in messages],
    })


# ---------------------------------------------------------------------------
# Telling one person something
# ---------------------------------------------------------------------------

@api_view(['POST'])
@admin_role_required(ROLE_PERMISSIONS['send_notifications'])
def admin_notify_user(request, user_id):
    """Send one member a notification, and record that it was sent.

    The console has had a "Send Notification" button on the user page since it
    was built. It showed a success toast and sent NOTHING: no endpoint, no
    request, no row. Somebody warning a member believed they had, and the
    member never heard anything.

    It goes through `create_notification` like every other site that tells
    somebody something, so it lands in the same inbox and rings the same bell.
    """
    from .models import Users
    from .views_notifications import create_notification

    admin = request.admin_user
    user = Users.objects.filter(user_id=user_id).first() if str(user_id).isdigit() \
        else Users.objects.filter(username__iexact=str(user_id)).first()
    if user is None:
        return _err('No account with that address.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    body = str(request.data.get('message') or '').strip()
    if not body:
        return _err('The message is empty.', 'VALIDATION_ERROR')
    if len(body) > 500:
        return _err('Keep it under 500 characters.', 'VALIDATION_ERROR')

    subject = str(request.data.get('subject') or '').strip() or 'A message from V-ENT'

    row = create_notification(user, 'system', subject[:160], body=body,
                              link='/notifications',
                              metadata={'from_admin': admin.username})
    if row is None:
        return _err('The message could not be sent.', 'NOT_SENT',
                    status.HTTP_502_BAD_GATEWAY)

    _record(admin, 'notify_user', 'User', user.user_id, reason=subject,
            username=user.username, message=body[:200])
    return _ok({'username': user.username}, 'Sent. It is in their inbox.')
