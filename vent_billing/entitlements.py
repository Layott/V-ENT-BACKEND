"""Where a membership benefit actually bites.

`benefits.py` answers "does this person hold this key right now". This file is
the other half: the endpoints that have to ASK, and the arithmetic they ask
with. It exists as its own module because the fault it is written against is
the one this codebase has produced most often - a capability built on one side
and never called from the other.

    grep -rn "has_benefit" --include=*.py . | grep -v vent_billing
    (nothing)

That was true when the billing app was first written. Every benefit in the
catalogue was a promise on a plan page, an organiser could sell it, a
subscriber could pay for it, and NOTHING anywhere honoured it. A discount that
is only ever rendered is not a discount, it is a lie with a price attached, and
it is worse than not offering one because somebody paid for it.

## What each catalogue key means to a request

| key                       | the endpoint that must ask                          |
|---------------------------|-----------------------------------------------------|
| `members_area`            | `views_plans.members_area` - built with the plan     |
| `member_ticket_discount`  | `vent_event.ledger.quote`, so the panel and the      |
|                           | charge read ONE number                               |
| `free_entry`              | tournament registration, before the wallet is        |
|                           | debited                                              |
| `priority_registration`   | the registration window, which is the only thing     |
|                           | early access can be early to                         |
| `early_announcements`     | the announcement feed                                |
| `member_badge`            | rendered, and rendering is all it claims             |

`member_badge` is the one key here that is honestly a display concern, and it
says so rather than being quietly absent from this table.

## Whose membership, though

A membership is with ONE seller. Somebody paying an organisation for free entry
to that organisation's tournaments has not bought free entry to everybody
else's, and a helper that forgets to narrow by seller would hand the whole
platform away to anybody holding the cheapest plan on it. So every function
here resolves the seller from the object being asked about, and `benefits.py`
is called with it. There is no path through this file that asks "do they hold
this from anybody".

## Rounding, and which way it goes

A discount is rounded in the MEMBER's favour, which is the opposite direction
from the platform fee in `charging.py` and for the same reason. The fee rounds
down because a fee rounded up takes a coin the platform did not earn; a
discount rounds the price down because a discount rounded against the member
takes back part of what the organiser said they could keep. Both rules are the
same rule: round against whoever set the number.
"""
from decimal import ROUND_FLOOR, Decimal

from . import benefits

#: The catalogue keys this module enforces, so a test can assert that every
#: key promising something concrete has somewhere that asks. `member_badge` is
#: deliberately absent: it promises a badge and a badge is drawn, not enforced.
ENFORCED = (
    'members_area',
    'member_ticket_discount',
    'free_entry',
    'priority_registration',
    'early_announcements',
)


def seller_of(obj):
    """The (org, owner) that runs an event or a tournament.

    Both models carry an optional organisation and a person who made it, under
    different column names, which is the only reason this function exists. The
    organisation is preferred when there is one: a plan sold by an organisation
    is the organisation's, and the person who happened to press Create is not
    who the member is paying.
    """
    if obj is None:
        return None, None
    org = (getattr(obj, 'organization', None)
           or getattr(obj, 'tournament_organization', None))
    owner = (getattr(obj, 'creator', None)
             or getattr(obj, 'tournament_creator', None))
    return org, owner


def _holds(user, key, obj, at=None):
    """Does this person hold `key` from whoever runs `obj`.

    Narrowed to the ORGANISATION when there is one and to the person otherwise,
    never to both at once: a plan carries exactly one of them, so asking for
    both would match nothing.
    """
    if user is None or not getattr(user, 'pk', None):
        return False
    org, owner = seller_of(obj)
    if org is not None:
        return benefits.has_benefit(user, key, org=org, at=at)
    if owner is not None:
        return benefits.has_benefit(user, key, owner=owner, at=at)
    return False


def _value(user, key, obj, at=None):
    if user is None or not getattr(user, 'pk', None):
        return 0
    org, owner = seller_of(obj)
    if org is not None:
        return benefits.benefit_value(user, key, org=org, at=at)
    if owner is not None:
        return benefits.benefit_value(user, key, owner=owner, at=at)
    return 0


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

def ticket_discount_pct(user, event, at=None):
    """The member discount this buyer gets on this event's tickets, 0 to 100.

    Read on the request that prices the basket, so somebody whose membership
    lapsed an hour ago is quoted the full price now rather than at the next
    deploy. That is gate C1 in one sentence.
    """
    pct = _value(user, 'member_ticket_discount', event, at=at)
    return max(0, min(int(pct or 0), 100))


def discounted(amount, pct):
    """`amount` less `pct` percent, rounded DOWN, never below zero.

    Decimal rather than float throughout. Money is never a float here, and a
    subscription performs the same arithmetic every month for years, which is
    exactly the shape of thing that accumulates a float's error into something
    somebody notices.
    """
    if not pct or amount is None:
        return amount
    value = Decimal(str(amount)) * (Decimal(100) - Decimal(str(pct))) / Decimal(100)
    if value < 0:
        value = Decimal(0)
    return value.quantize(Decimal('0.01'), rounding=ROUND_FLOOR)


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------

def waives_entry_fee(user, tournament, at=None):
    """Whether this person's membership pays this tournament's entry fee.

    Asked before the wallet is debited, never after. A waiver applied after the
    coins have moved is a refund somebody has to make by hand.
    """
    return _holds(user, 'free_entry', tournament, at=at)


def may_register_early(user, tournament, at=None):
    """Whether this person may register before registration opens.

    The benefit that had nothing to be early TO. `registration_opens_at` was on
    the model, was sent to every screen, and was enforced by nothing: an
    organiser who set a date found that anybody who opened the page could
    register anyway. So the window is enforced in `registration_window_error`
    below, and this is what steps over it.
    """
    return _holds(user, 'priority_registration', tournament, at=at)


def registration_window_error(user, tournament, at=None):
    """`(code, opens_at)` when registration has not opened for this person yet.

    Returns `(None, None)` when they may register, which is every case except
    an organiser who deliberately set a future opening date and a visitor who
    holds no early access.

    Only ever refuses where a date was SET. A tournament with no opening date
    has no window, which is almost all of them, so this cannot start refusing
    registrations that used to work.
    """
    from django.utils import timezone

    opens = getattr(tournament, 'registration_opens_at', None)
    if not opens:
        return None, None
    at = at or timezone.now()
    if at >= opens:
        return None, None
    if may_register_early(user, tournament, at=at):
        return None, opens
    return 'REGISTRATION_NOT_OPEN', opens


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

def sees_early_announcements(user, obj, at=None):
    """Whether this person reads this organiser's announcements before release."""
    return _holds(user, 'early_announcements', obj, at=at)
