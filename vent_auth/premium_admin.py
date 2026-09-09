"""Granting premium, in one place, for whatever kind of holder it is.

`is_premium` and `premium_note` are identical columns on `Users` and on
`Organization`, through one abstract mixin. Two endpoints write them and this is
what both call, so a grant means the same thing whichever screen it came from.

Three things it insists on.

**The note travels with the press.** `premium_note` exists so that six weeks
later somebody can read "granted for the Rivalry season" instead of asking in
Slack. A note added afterwards is a note nobody adds, so it is taken here or it
is empty on purpose.

**Every change is an `AdminAction`.** Premium is going to be sold. A free one
that nobody can explain is a support question with no answer, and the row
carries who, when, what it was and what it became.

**Revoking clears the note.** Leaving last year's reason on an account that no
longer has premium is worse than leaving it blank: it reads as though it still
applies.
"""
from vent_auth.models import AdminAction


def apply_premium(holder, *, on, note='', admin=None, kind='User'):
    """Set premium on a user or an organisation. Returns what changed.

    `holder` is anything carrying `PremiumMixin`. `kind` is what the audit row
    calls it, and it is passed rather than guessed so an unfamiliar model
    cannot quietly log itself as a User.
    """
    was = bool(holder.is_premium)
    was_note = holder.premium_note or ''

    holder.is_premium = bool(on)
    # A reason belongs to the grant it was written for. Taking premium away and
    # leaving the note behind leaves a sentence that reads as though it still
    # applies.
    holder.premium_note = (str(note or '')[:200] if on else '')
    holder.save(update_fields=['is_premium', 'premium_note'])

    if admin is not None:
        AdminAction.objects.create(
            admin=admin,
            action_type='grant_premium' if on else 'revoke_premium',
            target_model=kind,
            target_id=str(holder.pk),
            reason=str(note or '')[:500],
            metadata={
                'was': was,
                'now': holder.is_premium,
                'was_note': was_note,
                'note': holder.premium_note,
                'name': getattr(holder, 'username', None)
                        or getattr(holder, 'org_name', None),
            },
        )

    return {
        'is_premium': holder.is_premium,
        'premium_note': holder.premium_note,
        'changed': was != holder.is_premium,
    }
