"""Inviting somebody by email, whether or not they have an account yet.

CEO, 7 September 2026: "For anything about invites on the platform, you should
be able to type in peoples emails and it shows users or just even people who
dont have accounts and they receive invites to the website and to the org."

That overrides what `OrgInvite` was built on. Its docstring said "an invite to
an email address nobody has claimed is a signup funnel rather than a
membership", and the CEO's answer is that the signup funnel is the point: an
organiser knows the caterer's email address, not their V-ENT username, and
telling them to go and find out first is how the invite never gets sent.

## One mechanism, three tables

There are already three invite models - `OrgInvite`, `TeamInvite`,
`VendorInvite` - and a fourth would be a fourth accept path to keep in step.
So this is not a model. It is the two operations all of them need:

    invitee_for(text)      an email or a username, resolved to a person or,
                           failing that, to a clean email address
    claim_pending(user)    the moment somebody signs up or verifies, every
                           invitation already addressed to their email becomes
                           theirs

The second is the half that is easy to forget and impossible to notice: an
invitation sent to an address, accepted by nobody, sitting in a table while the
person it was for signs up and sees nothing.
"""
import re

from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from .models import Users

# Deliberately permissive. Django's validator is the authority; this is only to
# decide whether somebody TYPED an email or a username, and "@" is the signal.
LOOKS_LIKE_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def normalise_email(raw):
    """Lower case, trimmed, or '' if it is not an email address."""
    text = str(raw or '').strip().lower()
    if not LOOKS_LIKE_EMAIL.match(text):
        return ''
    try:
        validate_email(text)
    except ValidationError:
        return ''
    return text


def invitee_for(raw):
    """(user, email, error).

    Three answers, and the caller treats all three as success except the last:

        (user, email, None)   they are on V-ENT already
        (None, email, None)   a real address, nobody here yet - invite them
        (None, '', 'reason')  not usable as either

    A username is accepted too, because the invite forms have always taken one
    and the CEO asked for email IN ADDITION rather than instead.
    """
    text = str(raw or '').strip()
    if not text:
        return None, '', 'Enter an email address or a username.'

    email = normalise_email(text)
    if email:
        # An account is found by email whatever case it was stored in.
        user = Users.objects.filter(email__iexact=email).first()
        return user, email, None

    if text.startswith('@'):
        text = text[1:]
    user = Users.objects.filter(username__iexact=text).first()
    if user is not None:
        return user, (user.email or '').lower(), None

    return None, '', ('No account called @%s, and that is not an email address '
                      'either.' % text)


def claim_pending(user):
    """Attach every invitation addressed to this person's email. Returns a count.

    Called when an account is created and again when its email is verified,
    because either is the first moment the address is known to belong to them.

    Matching on email alone is safe HERE and nowhere else: an invitation is an
    offer, not access. Accepting is still a separate act the person has to
    take, and every accept path already checks that the invitation belongs to
    whoever is answering it.
    """
    email = (getattr(user, 'email', '') or '').strip().lower()
    if not email:
        return 0

    claimed = 0

    from .models import OrgInvite, RoleInvite, TeamInvite
    # Told, not only attached (inbox 455): an invitation that becomes theirs
    # in a table they never look at is the same as one that never arrived.
    for model in (OrgInvite, RoleInvite):
        for invite in model.objects.filter(email__iexact=email, user__isnull=True,
                                           status='pending'):
            invite.user = user
            invite.save(update_fields=['user'])
            claimed += 1
            _tell(user, invite)
    claimed += TeamInvite.objects.filter(email__iexact=email, user__isnull=True,
                                         status='pending').update(user=user)

    # A vendor invitation becomes a real stall, because a stall is what the
    # invitation was offering. The same object a bought pitch produces, so
    # everything downstream cannot tell which way somebody came in.
    from vent_event.models import Vendor, VendorInvite
    for invite in VendorInvite.objects.filter(email__iexact=email):
        exists = Vendor.objects.filter(event=invite.event, owner=user).exists()
        if exists:
            continue
        Vendor.objects.create(
            event=invite.event, owner=user,
            name=invite.name or ('%s at %s' % (user.username, invite.event.name))[:120],
            booth=invite.booth or '',
            # Invited by the organiser, so there is nobody left to approve it.
            status='approved',
        )
        claimed += 1

    # An affiliate link addressed to this person before they had an account.
    # Claimed HERE with every other invitation rather than in its own hook,
    # because "somebody has arrived and their email is now known to be theirs"
    # is one moment and should have one place that handles it.
    #
    # The commission that accrued while nobody was attached is paid by the
    # next settlement, because the ledger lines find their user through the
    # link. Nothing has to be back-filled.
    from vent_event.models import EventLedgerEntry, EventReferral
    links = list(EventReferral.objects.filter(payee_email__iexact=email,
                                              payee__isnull=True))
    for link in links:
        link.payee = user
        link.save(update_fields=['payee'])
        # The lines already written name no user, so they would be skipped by
        # a settlement for ever. Attaching the link is not enough on its own.
        EventLedgerEntry.objects.filter(
            referral=link, user__isnull=True, settled_at__isnull=True
        ).update(user=user)
        claimed += 1

    return claimed


# ---------------------------------------------------------------------------
# A role on one event or one tournament, for somebody who may have no account
# (inbox 455). OrgInvite is the organisation's own; RoleInvite is the rest.
# ---------------------------------------------------------------------------

#: How a role reads in a sentence ('as door staff', never 'as door').
ROLE_WORDS = {'door': 'door staff', 'scorekeeper': 'scorekeeper', 'manager': 'manager',
              'admin': 'admin', 'member': 'member'}

#: The English the notification and the email carry. The screen translates the
#: title by its English (`tx`), so it is one fixed sentence.
INVITED_TITLE = 'You have an invitation'


def invitation_url(token):
    from vent.settings import FRONTEND_URL
    return '%s/invitations/%s' % (FRONTEND_URL, token)


def email_hint(email):
    """a***@gmail.com: enough for the person to recognise their address, not
    enough for whoever holds a forwarded link to learn it."""
    name, _, domain = (email or '').partition('@')
    if not domain:
        return ''
    return '%s***@%s' % (name[:1], domain)


def _what(invite):
    """(kind, name, path) of what an invitation is for."""
    from .models import OrgInvite
    if isinstance(invite, OrgInvite):
        org = invite.org
        return 'organization', org.org_name, '/organizations/%s' % (org.slug or org.org_id)
    target = invite.target
    if invite.kind == invite.KIND_EVENT:
        # Door staff are refused by the event console and work the scanner,
        # so "Open" takes them there. Sending them to /manage put a refusal
        # in front of them on the morning of the show (inbox 455 walk).
        if invite.role == 'door':
            return 'event', target.name, '/events/scan?event=%s' % target.slug
        return 'event', target.name, '/events/%s/manage' % target.slug
    return 'tournament', target.tournament_title, '/tournaments/%s/manage' % target.slug


def _tell(user, invite):
    from .views_notifications import create_notification
    _kind, name, _path = _what(invite)
    who = invite.invited_by.username if invite.invited_by_id else 'V-ENT'
    create_notification(
        user, 'system', INVITED_TITLE,
        '@%s invited you to %s as %s.' % (who, name, ROLE_WORDS.get(invite.role, invite.role)),
        link='/invitations/%s' % invite.token,
        metadata={'invite': invite.token, 'role': invite.role},
        # The email they signed up from was the invitation itself.
        email=False,
    )


def offer_role(kind, target, email, role, inviter):
    """Offer a role on an event or a tournament to an address with no account,
    and email them the link. A second offer to the same address for the same
    thing corrects the first rather than sending two."""
    from .models import RoleInvite
    field = 'event' if kind == RoleInvite.KIND_EVENT else 'tournament'
    invite = RoleInvite.objects.filter(**{field: target}, email__iexact=email,
                                       status=RoleInvite.STATUS_PENDING).first()
    if invite is None:
        invite = RoleInvite.objects.create(kind=kind, **{field: target}, email=email,
                                           role=role, invited_by=inviter)
    else:
        invite.role, invite.invited_by = role, inviter
        invite.save(update_fields=['role', 'invited_by'])
    send_invitation_email(invite)
    return invite


def send_invitation_email(invite):
    """Never raises: the organiser has done their part, and a mail server
    having a bad minute must not read as the invitation having failed."""
    from . import emails
    _kind, name, _path = _what(invite)
    try:
        emails.send_invitation(
            invite.email,
            what=name,
            who=invite.invited_by.username if invite.invited_by_id else 'V-ENT',
            role=ROLE_WORDS.get(invite.role, invite.role),
            # A link carrying the invitation's token, never the address: an
            # email in a URL ends up in every log between here and the reader.
            join_url=invitation_url(invite.token),
            message=getattr(invite, 'message', '') or '',
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception('could not email invitation %s', invite.token)


def find(token):
    """The invitation with this token, of whichever kind, or None."""
    from .models import OrgInvite, RoleInvite
    token = str(token or '')[:32]
    if token.startswith('ri_'):
        return RoleInvite.objects.filter(token=token).select_related(
            'event', 'tournament', 'invited_by', 'user').first()
    return OrgInvite.objects.filter(token=token).select_related(
        'org', 'invited_by', 'user').first()


def describe(invite, viewer=None):
    """What an invitation is, for its own page. Public to whoever holds the
    link, so it names the thing and the inviter and only hints at the address."""
    kind, name, path = _what(invite)
    mine = viewer is not None and (
        invite.user_id == viewer.user_id
        or (invite.user_id is None
            and (viewer.email or '').strip().lower() == (invite.email or '').lower()))
    return {
        'token': invite.token,
        'kind': kind,
        'name': name,
        'path': path,
        'role': invite.role,
        'status': invite.status,
        'invited_by': invite.invited_by.username if invite.invited_by_id else None,
        'email_hint': email_hint(invite.email or (invite.user.email if invite.user_id else '')),
        'awaiting_signup': invite.user_id is None,
        'for_you': mine,
        'created_at': invite.created_at,
    }


class AnswerError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def answer(invite, user, accept):
    """Accept or decline, as the person it was sent to. Accepting writes the
    real membership: an organisation member, an event manager or door steward,
    a tournament scorekeeper."""
    from .models import OrgInvite, OrgMember
    from django.utils import timezone

    if invite.user_id is None:
        # Signed up with this address but the claim never ran (a social sign
        # in, an account made before the invitation): claim it now, for a
        # verified account with exactly this email.
        if (user.is_active and (user.email or '').strip().lower()
                == (invite.email or '').lower()):
            invite.user = user
            invite.save(update_fields=['user'])
        else:
            raise AnswerError('INVITE_FOR_ANOTHER_ACCOUNT')
    if invite.user_id != user.user_id:
        raise AnswerError('INVITE_FOR_ANOTHER_ACCOUNT')
    if invite.status != 'pending':
        raise AnswerError('ALREADY_ANSWERED')

    invite.status = 'accepted' if accept else 'declined'
    invite.responded_at = timezone.now()
    invite.save(update_fields=['status', 'responded_at'])
    if not accept:
        return invite

    if isinstance(invite, OrgInvite):
        OrgMember.objects.get_or_create(
            org=invite.org, user=user,
            defaults={'role': invite.role, 'scopes': invite.scopes or []})
    elif invite.kind == invite.KIND_EVENT:
        from vent_event.models import EventManager
        row, made = EventManager.objects.get_or_create(
            event=invite.event, user=user,
            defaults={'role': invite.role, 'added_by': invite.invited_by})
        if not made and row.role != invite.role:
            row.role = invite.role
            row.save(update_fields=['role'])
    else:
        from vent_tournament.models import TournamentStaff
        TournamentStaff.objects.get_or_create(
            tournament=invite.tournament, user=user,
            defaults={'role': 'scorekeeper', 'added_by': invite.invited_by})

    if invite.invited_by_id:
        from .views_notifications import create_notification
        _kind, name, path = _what(invite)
        create_notification(
            invite.invited_by, 'system', 'Your invitation was accepted',
            '@%s accepted your invitation to %s as %s.' % (user.username, name,
                                                           ROLE_WORDS.get(invite.role, invite.role)),
            link=path)
    return invite
