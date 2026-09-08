"""One description of a plan, a subscription and an invoice.

Hand-built dicts in each view is the fault this codebase has produced more than
once: an organiser rendered as a dict lost its avatar and its founder badge,
and a person described two ways ends up described two ways on two screens. So
each shape is built here and every view that sends it calls the same function.

A row's field names do not change between the list and the detail. A detail may
carry MORE, never different: `slug`, `token` and `state` mean the same thing
everywhere, which is the rule the whole platform is built to.
"""
from datetime import timedelta

from . import benefits, dunning, states
from .models import Invoice, Subscription


def person_row(user, request=None):
    """A person, through the ONE builder the platform already has.

    Not a dict written here. A hand-built person loses the avatar and the
    founder badge, which is a fault this codebase has shipped: the badge was
    reported by the profile endpoint and by nothing else, so it showed on a
    profile and on no post, comment or thread. `views_community._person` is the
    single description, and every author on the platform goes through it.
    """
    if user is None:
        return None
    from vent_auth.views_community import _person
    return _person(request, user)


def plan_row(plan, *, viewer=None, request=None, include_private=False):
    """A plan, as every screen reads it.

    `member_content` is never in this shape. It is what somebody is paying for,
    so it comes from its own endpoint that checks the subscription first; a
    field that is sometimes present and sometimes absent is a field somebody
    eventually forgets to guard.
    """
    row = {
        'slug': plan.slug,
        'name': plan.name,
        'tagline': plan.tagline,
        'description': plan.description,
        'price_vc': plan.price_vc,
        'price_ngn': plan.price_ngn,
        'interval': plan.interval,
        'trial_days': plan.trial_days,
        'is_free': plan.is_free,
        'status': plan.status,
        'benefits': [
            {'key': row_.get('key') if isinstance(row_, dict) else row_,
             'value': (row_.get('value') if isinstance(row_, dict) else 0) or 0}
            for row_ in (plan.benefits or [])
        ],
        'implied_benefits': list(benefits.IMPLIED),
        'seller': {
            'kind': 'org' if plan.org_id else 'person',
            'name': plan.seller_name,
            'org_slug': plan.org.slug if plan.org_id else None,
            'username': plan.owner.username if plan.owner_id else None,
        },
        'member_count': plan.live_subscriptions().count(),
        'created_at': plan.created_at.isoformat(),
    }
    if include_private:
        row['member_content'] = plan.member_content
        row['has_member_content'] = bool(plan.member_content)
    else:
        row['has_member_content'] = bool(plan.member_content)
    return row


def subscription_row(sub, *, request=None, for_organiser=False):
    """A subscription, as the subscriber and the organiser both read it.

    One shape for both, with `subscriber` filled in only for the organiser's
    list. Permission decides what somebody may DO, never what shape the data
    has - the rule that stopped a second Tournament model existing.
    """
    next_at = sub.next_charge_at
    row = {
        'token': sub.token,
        'state': sub.state,
        'plan': plan_row(sub.plan, request=request),
        'period_start': sub.period_start.isoformat(),
        'period_end': sub.period_end.isoformat(),
        'access_until': sub.period_end.isoformat(),
        'has_access': sub.has_access(),
        'renews': sub.renews,
        'cancel_at_period_end': sub.cancel_at_period_end,
        'next_charge_at': next_at.isoformat() if next_at else None,
        'source': sub.source,
        'card': ({
            'brand': sub.card.brand,
            'last4': sub.card.last4,
        } if sub.card_id else None),
        'pending_plan': (plan_row(sub.pending_plan, request=request)
                         if sub.pending_plan_id else None),
        'dunning_attempt': sub.dunning_attempt,
        'dunning_attempts_left': (dunning.attempts_left(sub.dunning_attempt)
                                  if sub.state == states.PAST_DUE else 0),
        'grace_until': (
            sub.period_end.isoformat() if sub.state != states.PAST_DUE
            else (sub.period_end
                  + timedelta(days=Subscription.GRACE_DAYS)).isoformat()),
        'created_at': sub.created_at.isoformat(),
    }
    if for_organiser:
        row['subscriber'] = person_row(sub.subscriber, request)
    return row


def invoice_row(inv):
    return {
        'token': inv.token,
        'plan_name': inv.plan_name,
        'period_start': inv.period_start.isoformat(),
        'period_end': inv.period_end.isoformat(),
        'attempt': inv.attempt,
        'amount_vc': inv.amount_vc,
        'amount_ngn': inv.amount_ngn,
        'collected_vc': inv.collected_vc,
        'state': inv.state,
        'source': inv.source,
        'reference': inv.provider_reference,
        # A CODE, never the gateway's own English. `failure_detail` stays on
        # the row for whoever is investigating and is never sent to a reader.
        'failure_code': inv.failure_code,
        'created_at': inv.created_at.isoformat(),
        'paid_at': inv.paid_at.isoformat() if inv.paid_at else None,
        'refunded_at': inv.refunded_at.isoformat() if inv.refunded_at else None,
        'refundable': inv.state == Invoice.STATE_PAID and inv.collected_vc > 0,
    }


def event_row(evt):
    return {
        'from': evt.from_state,
        'to': evt.to_state,
        'reason': evt.reason,
        'at': evt.at.isoformat(),
        'by': evt.actor.username if evt.actor_id else None,
        'note': evt.note,
    }
