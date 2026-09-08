"""What an organiser sees: their members, their money, and who is past due.

    GET  /billing/plan/<slug>/members/    who is on it, and in what state
    GET  /billing/plan/<slug>/earnings/   what it has earned and what is owed
    GET  /billing/plan/<slug>/invoices/   every charge attempted, failures too
    POST /billing/plan/<slug>/settle/     pay it out, once
    POST /billing/invoice/<token>/refund/ give one charge back
    POST /billing/subscription/<token>/end/  the organiser ending a membership

Recurring revenue is reported as a real number rather than a marketing one:
only subscriptions that will actually be charged again count towards it. A
member who cancelled last week still has access and is still on the list, and
counting their payment as recurring would overstate every figure on this
screen. The list says both.
"""
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view

from . import charging, clock, ledger, lifecycle, serializers, states
from .models import Invoice, Subscription
from .permissions import error, may_manage_plan, ok, require_viewer
from .views_plans import _plan_by_ref


def _resolve(request, ref):
    """(plan, viewer, None) or (None, None, error). Organiser only."""
    who, err = require_viewer(request)
    if err:
        return None, None, err
    plan, _moved = _plan_by_ref(ref)
    if plan is None:
        return None, None, error('That plan could not be found.', 'NOT_FOUND',
                                 status.HTTP_404_NOT_FOUND)
    if not may_manage_plan(who, plan):
        return None, None, error('Only the organiser can see this.', 'FORBIDDEN',
                                 status.HTTP_403_FORBIDDEN)
    return plan, who, None


def _monthly_value(plan):
    """One plan's price expressed per month, for a comparable figure.

    A yearly plan divided by twelve, rounded down. Rounded down for the same
    reason fees are: a number an organiser reads as revenue should never be
    larger than what actually arrives.
    """
    if plan.interval == clock.YEARLY:
        return plan.price_vc // 12
    return plan.price_vc


@api_view(['GET'])
def members(request, ref):
    """Who is on this plan, newest first, with the past-due ones named."""
    plan, _who, err = _resolve(request, ref)
    if err:
        return err

    rows = Subscription.objects.filter(plan=plan).select_related(
        'subscriber', 'plan', 'plan__org', 'plan__owner', 'card', 'pending_plan')

    state = (request.query_params.get('state') or '').strip()
    if state in dict(states.CHOICES):
        rows = rows.filter(state=state)

    listed = [serializers.subscription_row(s, request=request, for_organiser=True)
              for s in rows[:500]]

    counts = {row['state']: row['n'] for row in Subscription.objects.filter(
        plan=plan).values('state').annotate(n=Count('subscription_id'))}

    renewing = [s for s in Subscription.objects.filter(
        plan=plan, state__in=(states.TRIALING, states.ACTIVE, states.PAST_DUE),
        cancel_at_period_end=False)]
    # The same number the plan's public page shows, from the same function.
    live_count = plan.live_subscriptions().count()

    return ok({
        'plan': serializers.plan_row(plan, request=request, include_private=True),
        'members': listed,
        'counts': counts,
        # Only what will actually be charged again. A member who cancelled last
        # week still has access and is still on the list above, and counting
        # them here would overstate the figure the organiser plans against.
        'recurring_monthly_vc': _monthly_value(plan) * len(renewing),
        'members_live': live_count,
        'renewing': len(renewing),
        'past_due': counts.get(states.PAST_DUE, 0),
    })


@api_view(['GET'])
def earnings(request, ref):
    """What this plan has earned, who is owed it, and what has been paid."""
    plan, _who, err = _resolve(request, ref)
    if err:
        return err

    figures = ledger.balances(plan)
    collected = Invoice.objects.filter(
        plan=plan, state=Invoice.STATE_PAID).aggregate(
            n=Sum('collected_vc'))['n'] or 0
    refunded = Invoice.objects.filter(
        plan=plan, state=Invoice.STATE_REFUNDED).aggregate(
            n=Sum('collected_vc'))['n'] or 0

    runs = [{
        'id': run.id,
        'amount_vc': run.amount_vc,
        'lines_paid': run.lines_paid,
        'at': run.created_at.isoformat(),
        'note': run.note,
    } for run in plan.settlements.all()[:20]]

    return ok({
        'fee_pct': charging.platform_rate(),
        'collected_vc': collected,
        'refunded_vc': refunded,
        'settlements': runs,
        **figures,
    })


@api_view(['POST'])
def settle(request, ref):
    """Pay out everything owed on this plan, once.

    Idempotent by construction, not by a guard on top: each line is stamped
    with the run that paid it inside the transaction that moved the coins, so
    pressing it twice pays nothing the second time. Answering 200 with
    `lines_paid: 0` is the right answer to "already settled", because an error
    there reads as broken and invites another press.
    """
    plan, who, err = _resolve(request, ref)
    if err:
        return err
    run = ledger.settle(plan, run_by=who,
                        note=str(request.data.get('note') or ''))
    return ok({
        'settlement_id': run.id,
        'amount_vc': run.amount_vc,
        'lines_paid': run.lines_paid,
        'at': run.created_at.isoformat(),
        **ledger.balances(plan),
    })


@api_view(['GET'])
def plan_invoices(request, ref):
    """Every charge attempted against this plan, newest first.

    Failures included, and the reason as a code. An organiser asking "why did
    that member stop paying" is asking about a failed charge, and a list that
    only holds the successful ones cannot answer.

    This is also where a refund is pressed from, because a refund is against a
    CHARGE rather than against a person: refunding "the member" is ambiguous
    the moment somebody has paid twice.
    """
    plan, _who, err = _resolve(request, ref)
    if err:
        return err

    rows = Invoice.objects.filter(plan=plan).select_related(
        'subscription', 'subscription__subscriber')[:200]
    out = []
    for inv in rows:
        row = serializers.invoice_row(inv)
        row['subscriber'] = serializers.person_row(
            inv.subscription.subscriber, request)
        row['subscription_token'] = inv.subscription.token
        out.append(row)
    return ok({'invoices': out})


@api_view(['POST'])
def refund(request, token):
    """Give one charge back. `{reason, keep_access?}`.

    Writes reversal lines rather than adjusting anybody's balance quietly. If
    the organiser has already been settled, the negative line stays open and
    nets against their next settlement, which is the entire reason this is a
    ledger and not a running total.
    """
    who, err = require_viewer(request)
    if err:
        return err

    invoice = Invoice.objects.filter(token=str(token)).select_related(
        'plan', 'subscription').first()
    if invoice is None:
        return error('That charge could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)
    if not may_manage_plan(who, invoice.plan):
        return error('Only the organiser can refund this.', 'FORBIDDEN',
                     status.HTTP_403_FORBIDDEN)

    reason = str(request.data.get('reason') or '').strip()
    if not reason:
        # A refund with no reason is a line nobody can explain later, and this
        # is money leaving an organiser's balance.
        return error('Say why this is being refunded.', 'REASON_REQUIRED')

    if invoice.state != Invoice.STATE_PAID:
        return error('Only a charge that was collected can be refunded.',
                     'NOT_REFUNDABLE')

    keep = bool(request.data.get('keep_access'))
    charging.refund(invoice, reason=reason, actor=who, end_access=not keep)
    invoice.refresh_from_db()
    return ok({'invoice': serializers.invoice_row(invoice),
               'subscription': serializers.subscription_row(
                   invoice.subscription, request=request, for_organiser=True)},
              'Refunded.')


@api_view(['POST'])
def end_membership(request, token):
    """The organiser ending somebody's membership.

    Same rule as the subscriber cancelling: it stops renewing and the paid
    period runs out. An organiser cannot take back time somebody has paid for
    either, and if they want to, that is a refund and it says so.
    """
    who, err = require_viewer(request)
    if err:
        return err
    sub = Subscription.objects.filter(token=str(token)).select_related(
        'plan', 'plan__org', 'plan__owner', 'subscriber', 'card',
        'pending_plan').first()
    if sub is None:
        return error('That subscription could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)
    if not may_manage_plan(who, sub.plan):
        return error('Only the organiser can do that.', 'FORBIDDEN',
                     status.HTTP_403_FORBIDDEN)

    lifecycle.cancel(sub, actor=who, by_organiser=True,
                     reason=str(request.data.get('reason') or '')[:200])
    return ok(serializers.subscription_row(sub, request=request,
                                           for_organiser=True),
              'That membership will not renew.')


@api_view(['GET'])
def org_overview(request):
    """`?org=<slug>` - every plan an organisation runs, in one answer.

    The organisation console reads this, so a screen with four plans on it
    makes one request rather than four. It is the same numbers as `members`
    and `earnings`, from the same functions.
    """
    who, err = require_viewer(request)
    if err:
        return err

    from .views_plans import _org_by_ref
    org = _org_by_ref(request.query_params.get('org'))
    if org is None:
        return error('Organization not found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)

    from .permissions import may_manage_org
    if not may_manage_org(who, org):
        return error('Your role in this organization does not allow that.',
                     'FORBIDDEN', status.HTTP_403_FORBIDDEN)

    now = timezone.now()
    rows = []
    total_monthly = 0
    total_owed = 0
    total_past_due = 0
    for plan in org.plans.select_related('org', 'owner'):
        # `Plan.live_subscriptions`, the same function the plan's public page
        # counts with, so "members" means the same number on both screens. The
        # walk on 8 September had the public page saying "1 members" and this
        # console saying 0, for the same person, which is the two-surfaces
        # fault this codebase keeps producing.
        live = plan.live_subscriptions()
        renewing = live.filter(
            cancel_at_period_end=False,
            state__in=(states.TRIALING, states.ACTIVE, states.PAST_DUE)).count()
        past_due = live.filter(state=states.PAST_DUE).count()
        figures = ledger.balances(plan)
        monthly = _monthly_value(plan) * renewing
        total_monthly += monthly
        total_owed += figures['owed_vc']
        total_past_due += past_due
        rows.append({
            **serializers.plan_row(plan, request=request, include_private=True),
            'members': live.count(),
            'renewing': renewing,
            'past_due': past_due,
            'recurring_monthly_vc': monthly,
            'owed_vc': figures['owed_vc'],
            'paid_vc': figures['paid_vc'],
        })

    return ok({
        'org': {'slug': org.slug, 'name': org.org_name},
        'plans': rows,
        'recurring_monthly_vc': total_monthly,
        'owed_vc': total_owed,
        'past_due': total_past_due,
        'as_at': now.isoformat(),
    })
