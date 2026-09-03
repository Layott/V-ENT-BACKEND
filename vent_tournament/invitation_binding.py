"""Invitations addressed to an email, claimed when that person arrives.

CEO, 3 September 2026: "lets be able to invite through email also".

An organiser asks fifteen people by email. Twelve of them have no account. Each
of those rows names an address and nobody else. The moment one of them signs up
with that address, or signs in with it, the invitation stops being a line in a
mailbox and becomes something they can see and answer in the app.

Without this the email invitation is a dead end: the person joins, opens their
invitations, and finds nothing, while the organiser's list still says pending
for somebody who is demonstrably here. That is worse than not sending it, because
both sides believe something different.

Called from signup and from login. Login as well as signup deliberately: an
address may already have belonged to a dormant account, or the invitation may
have been sent after they joined.
"""

from django.db import transaction


def bind_invitations_for(user):
    """Attach any email invitations for this user's address to their account.

    Returns how many were bound. Safe to call on every login: it is a no-op
    once there is nothing left addressed to that email.
    """
    if user is None or not getattr(user, 'email', ''):
        return 0

    from .models import TournamentInvitation

    address = user.email.strip().lower()
    if not address:
        return 0

    pending = list(TournamentInvitation.objects
                   .filter(email__iexact=address, user__isnull=True)
                   .select_related('tournament'))
    if not pending:
        return 0

    bound = 0
    for invitation in pending:
        # They may already have been invited by name to the same tournament,
        # in which case binding would break `one_invitation_per_player`. The
        # named row is the better one: it was addressed to the person rather
        # than to an address, so the email row is retired instead.
        clash = (TournamentInvitation.objects
                 .filter(tournament_id=invitation.tournament_id, user=user)
                 .exclude(id=invitation.id)
                 .exists())
        try:
            with transaction.atomic():
                if clash:
                    invitation.delete()
                    continue
                invitation.user = user
                invitation.email = ''
                invitation.save(update_fields=['user', 'email'])
                bound += 1
        except Exception:                                   # noqa: BLE001
            # Never let this stop somebody signing in. An unbound invitation is
            # a missing row in a list; a failed login is a person locked out.
            continue

    return bound
