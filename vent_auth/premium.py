"""Whether an account may use a premium feature, answered in one place.

CEO, 9 September 2026, choosing between three ways to gate the fifteen features
the organiser spec marks PREMIUM: "A flag admins set, sold later."

## Why this exists at all

The spec marks a long list PREMIUM, and there was nothing to ask. `vent_billing`
sells an ORGANISER's plan to their own followers - somebody paying Vermillion
Encore for free entry to Vermillion's tournaments - which is a different thing
from a V-ENT platform subscription. So every premium line had no gate, and the
choice was to build a whole subscription product first, ship everything
ungated, or put the question behind one function.

One function. Today it reads a field the admin console sets, so V-ENT can GRANT
premium before it can charge for it. When the pricing is decided, `vent_billing`
writes the same field and **no feature code changes**, which is the entire point
of it being one function rather than a check copied into twelve views.

## The shape of the answer

A tournament belongs to a person, and sometimes to an organisation. The
organisation's standing wins when there is one: somebody running a tournament
for an org that pays should not be refused because their personal account does
not. That is the same precedence `entitlements.seller_of` already uses, and
disagreeing with it would be two answers to one question.

## What it deliberately does NOT do

It does not know what any feature costs, how long a subscription lasts, or
whether one was paid for. Those belong to whatever eventually sells it. This
answers one question: may this account use this, right now.
"""
from django.db import models

#: The features this module gates. Named rather than free text, so a typo is a
#: KeyError at import rather than a silent `False` that turns a paid feature
#: off for everybody. Same reason `ROLE_PERMISSIONS` names its permissions.
FEATURES = {
    'ticket_codes': 'Generated entry codes, and downloading them as a document',
    'automated_prizes': 'Prizes paid out automatically at a time you set',
    'entry_requirements_advanced': 'Penalty points and ranking as entry conditions',
    'export_documents': 'Reports as PDF and DOCX rather than a spreadsheet',
    'unlimited_size': 'More than 64 entrants, and more than one tournament at once',
    'advanced_streaming': 'Live data and graphics into a stream',
    'financial_analytics': 'Entry fees, sponsorship and payouts, broken down',
    'media_export': 'Logos and media files, in a bundle',
}


class PremiumMixin(models.Model):
    """The two columns, so a user and an organisation carry the same ones.

    Abstract, so each concrete model gets its own columns rather than a shared
    table nobody can index usefully.
    """

    #: Whether premium is on. A field rather than a computed property, because
    #: today an admin sets it and tomorrow a subscription does, and the readers
    #: should not be able to tell the difference.
    is_premium = models.BooleanField(default=False)

    #: Why, in the admin's words. "Granted for the Rivalry season" is the
    #: difference between a support question that takes a minute and one that
    #: takes an afternoon.
    premium_note = models.CharField(max_length=200, blank=True, default='')

    #: When it runs out, or NULL for premium that does not.
    #:
    #: An admin grant has no end date, because "granted for the Rivalry season"
    #: is a sentence somebody wrote and not a date anything can enforce. A
    #: PURCHASE has one, and `premium_sale.expire_due()` is what turns the flag
    #: off when it passes.
    #:
    #: Deliberately not a second boolean: "is it on" and "until when" are one
    #: fact, and two columns that can disagree is how an account ends up
    #: premium with an expiry in the past.
    premium_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def premium_has_lapsed(self, now=None):
        """Whether this holder's premium is on but past its end date.

        Read by `expire_due` and by anything that wants to be right between
        cron runs. `has_premium` deliberately does NOT call it: a nightly sweep
        that has not run yet must not make a paid account's features flicker
        off mid-session, and being a few hours generous to somebody who paid is
        the right direction to be wrong in.
        """
        from django.utils import timezone
        if not self.is_premium or self.premium_until is None:
            return False
        return self.premium_until <= (now or timezone.now())


def _owner_and_org(obj):
    """The person and the organisation behind a tournament, event or org.

    Both models name these differently, which is the only reason this exists.
    """
    if obj is None:
        return None, None
    # An organisation asked about directly is its own answer.
    if hasattr(obj, 'org_owner_id'):
        return getattr(obj, 'org_owner', None), obj
    owner = (getattr(obj, 'tournament_creator', None)
             or getattr(obj, 'creator', None)
             or (obj if hasattr(obj, 'user_id') else None))
    org = (getattr(obj, 'tournament_organization', None)
           or getattr(obj, 'organization', None))
    return owner, org


def has_premium(who=None, *, org=None):
    """May this account use a premium feature.

    `who` may be a user, a tournament, an event or an organisation: the callers
    hold different things and making each of them work out the owner is how the
    answer starts differing between screens.

    The ORGANISATION wins when there is one. Somebody running a tournament for
    an organisation that pays is not refused because their personal account
    does not.
    """
    owner, found_org = _owner_and_org(who)
    org = org or found_org
    if org is not None and getattr(org, 'is_premium', False):
        return True
    if owner is not None and getattr(owner, 'is_premium', False):
        return True
    # A bare user passed as `who` and not recognised above.
    return bool(getattr(who, 'is_premium', False))


def refuse(feature):
    """The refusal body, or None if `feature` is not gated.

    A code, never a sentence: the screen that shows this may be in French, and
    the frontend has one dictionary keyed by code. The English here is the
    fallback for anything without a key yet.
    """
    if feature not in FEATURES:
        raise KeyError('unknown premium feature: %r' % (feature,))
    return {
        'status': 'error',
        'data': {'feature': feature},
        'message': FEATURES[feature] + ' is a premium feature.',
        'code': 'PREMIUM_REQUIRED',
    }
