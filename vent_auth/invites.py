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

    from .models import OrgInvite, TeamInvite
    for model in (OrgInvite, TeamInvite):
        try:
            rows = model.objects.filter(email__iexact=email, user__isnull=True,
                                        status='pending')
        except Exception:
            # A model without an `email` column yet. Nothing to claim rather
            # than an exception on somebody's signup.
            continue
        claimed += rows.update(user=user)

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
