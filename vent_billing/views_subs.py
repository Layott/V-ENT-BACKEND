"""What a subscriber does: joining, leaving, and reading what they paid.

    GET  /billing/subscriptions/                   mine
    GET  /billing/subscription/<token>/            one of mine, with its history
    POST /billing/plan/<slug>/subscribe/           join
    POST /billing/subscription/<token>/cancel/     leave, keeping the period
    POST /billing/subscription/<token>/resume/     changed my mind
    POST /billing/subscription/<token>/change/     move plan at the next period
    GET  /billing/entitlements/                    what I currently hold

`cancel` is the one to read first. It is one press, it works from every live
state, and it does not take the paid period away. That order - cancellation
before charging - is the reason this feature was refused on 8 September and the
reason it is here now.
"""
from rest_framework import status
from rest_framework.decorators import api_view

from . import benefits, charging, lifecycle, serializers
from .models import Invoice, Subscription
from .permissions import error, ok, require_viewer
from .views_plans import _plan_by_ref


def _mine(who, token):
    return Subscription.objects.filter(
        token=str(token), subscriber=who).select_related(
            'plan', 'plan__org', 'plan__owner', 'pending_plan', 'card').first()


@api_view(['GET'])
def my_subscriptions(request):
    who, err = require_viewer(request)
    if err:
        return err
    rows = Subscription.objects.filter(subscriber=who).select_related(
        'plan', 'plan__org', 'plan__owner', 'pending_plan', 'card')

    wallet, card = charging.payment_source(who)
    return ok({
        'subscriptions': [serializers.subscription_row(s, request=request)
                          for s in rows],
        # What the next charge would come out of, so the screen can say it
        # rather than guess. A subscriber asking "what is this taking from"
        # is asking about a real account, not about a preference.
        'wallet_balance_vc': wallet.wallet_balance if wallet else 0,
        'default_card': ({'brand': card.brand, 'last4': card.last4}
                         if card else None),
    })


@api_view(['GET'])
def subscription_detail(request, token):
    who, err = require_viewer(request)
    if err:
        return err
    sub = _mine(who, token)
    if sub is None:
        return error('That subscription could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)

    row = serializers.subscription_row(sub, request=request)
    row['invoices'] = [serializers.invoice_row(i) for i in sub.invoices.all()[:50]]
    row['history'] = [serializers.event_row(e) for e in sub.events.all()[:50]]
    return ok(row)


@api_view(['POST'])
def subscribe(request, ref):
    """Join a plan. The first charge happens here, server side.

    Never activated on the browser's word: the wallet is debited by this
    process, and a card is charged by a request this server made and answered
    by Paystack. There is no path through this endpoint that trusts a redirect.
    """
    who, err = require_viewer(request)
    if err:
        return err

    plan, _moved = _plan_by_ref(ref)
    if plan is None:
        return error('That plan could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)

    try:
        sub, invoice = lifecycle.subscribe(who, plan)
    except lifecycle.SubscribeError as exc:
        messages = {
            'PLAN_NOT_AVAILABLE': 'This plan is not open for new members.',
            'ALREADY_SUBSCRIBED': 'You are already a member of this plan.',
            charging.INSUFFICIENT_FUNDS:
                'There are not enough VENT COINS in your wallet for this.',
            charging.NO_PAYMENT_METHOD:
                'Add money to your wallet or save a card, then try again.',
            charging.CARD_DECLINED: 'That card was declined. Nothing was charged.',
            charging.GATEWAY_ERROR:
                'The payment could not be completed. Nothing was charged.',
            charging.PAYMENTS_UNAVAILABLE:
                'Card payment is not set up on this platform yet.',
        }
        http = (status.HTTP_409_CONFLICT if exc.code == 'ALREADY_SUBSCRIBED'
                else status.HTTP_400_BAD_REQUEST)
        return error(messages.get(exc.code, 'That could not be done.'),
                     exc.code, http,
                     price_vc=plan.price_vc, price_ngn=plan.price_ngn)

    row = serializers.subscription_row(sub, request=request)
    row['invoice'] = serializers.invoice_row(invoice) if invoice else None
    return ok(row, 'You are a member.')


@api_view(['POST'])
def cancel(request, token):
    """One press, from any live state, keeping what was paid for.

    Answers 200 with the date access runs to, so the screen says the date
    rather than "cancelled" and leaves somebody wondering whether they just
    lost three weeks.
    """
    who, err = require_viewer(request)
    if err:
        return err
    sub = _mine(who, token)
    if sub is None:
        return error('That subscription could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)

    lifecycle.cancel(sub, actor=who,
                     reason=str(request.data.get('reason') or '')[:200])
    return ok(serializers.subscription_row(sub, request=request),
              'Cancelled. You keep access until the end of the period.')


@api_view(['POST'])
def resume(request, token):
    who, err = require_viewer(request)
    if err:
        return err
    sub = _mine(who, token)
    if sub is None:
        return error('That subscription could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)
    try:
        lifecycle.resume(sub, actor=who)
    except lifecycle.SubscribeError as exc:
        messages = {
            'NOT_CANCELLED': 'That subscription is not cancelled.',
            'PERIOD_OVER': 'That period has already ended. Join again to start '
                           'a new one.',
        }
        return error(messages.get(exc.code, 'That could not be done.'), exc.code)
    return ok(serializers.subscription_row(sub, request=request),
              'Your membership will renew as before.')


@api_view(['POST'])
def change_plan(request, token):
    """`{plan: <slug>}`. Takes effect at the START of the next period.

    No proration, and the screen says the date before anybody presses. See the
    note at the top of `charging.py`: a part-period of a plan priced in whole
    VENT COINS is not expressible, and rounding it takes from one side every
    single time.
    """
    who, err = require_viewer(request)
    if err:
        return err
    sub = _mine(who, token)
    if sub is None:
        return error('That subscription could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)

    plan, _moved = _plan_by_ref(request.data.get('plan'))
    if plan is None:
        return error('That plan could not be found.', 'NOT_FOUND',
                     status.HTTP_404_NOT_FOUND)

    try:
        lifecycle.change_plan(sub, plan, actor=who)
    except lifecycle.SubscribeError as exc:
        messages = {
            'PLAN_NOT_AVAILABLE': 'That plan is not open for new members.',
            'DIFFERENT_SELLER': 'That plan belongs to somebody else. Cancel this '
                                'one and join theirs.',
        }
        return error(messages.get(exc.code, 'That could not be done.'), exc.code)

    return ok(serializers.subscription_row(sub, request=request),
              'The change starts at the beginning of your next period.')


@api_view(['GET'])
def entitlements(request):
    """Everything the caller currently holds.

    Answers 200 to a signed-out caller with an empty list and no benefits, on
    purpose and following the `capabilities` endpoint: the interface then has
    one code path for members and strangers alike rather than a 401 branch that
    only one of them ever exercises.
    """
    from .permissions import viewer
    who = viewer(request)
    if who is None:
        return ok({'signed_in': False, 'memberships': []})
    return ok({'signed_in': True, 'memberships': benefits.entitlements(who)})


@api_view(['GET'])
def my_invoices(request):
    """Every charge attempt against the caller, newest first.

    Failures included. A statement that only lists successes cannot answer
    "why did my membership stop", which is the question it exists for.
    """
    who, err = require_viewer(request)
    if err:
        return err
    rows = Invoice.objects.filter(
        subscription__subscriber=who).select_related('subscription')[:100]
    return ok({'invoices': [serializers.invoice_row(i) for i in rows]})
