"""The premium offer, and buying it.

Three endpoints, and between them they replace the sentence the CEO objected to
on 10 September: "Ask a V-ENT admin to turn premium on for this account".

    GET  /auth/premium/offer/      what it costs, what it gives, where you stand
    POST /auth/premium/buy/        charge my wallet and turn it on
    POST /auth/premium/interest/   it is not on sale; record that I want it

`offer` answers to ANYBODY, signed in or not, and that is deliberate: the page
that says what premium is has to be readable and indexable, and a price behind a
login is a price nobody finds. Signed in, the same payload also carries where
the caller stands, so the page has one request rather than two.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import premium, premium_sale
from .actors import actor_from_request
from .models import UserWallet
from .org_link import mine as my_orgs


def _ok(data, message='OK'):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=status.HTTP_200_OK)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'data': {}, 'message': message,
                     'code': code}, status=http_status)


def _standing(holder):
    """Where a holder stands, in the shape every screen reads."""
    return {
        'is_premium': bool(holder.is_premium),
        'premium_note': holder.premium_note or '',
        'premium_until': holder.premium_until,
        # An admin gave it, so there is nothing to renew and nothing to charge.
        'granted': bool(holder.is_premium and holder.premium_until is None),
    }


@api_view(['GET'])
def premium_offer(request):
    """What premium costs and what it unlocks. Open to everybody."""
    prices = premium_sale.offer()

    data = {
        **prices,
        # The real list, from the module that gates them, rather than marketing
        # copy that can drift away from what the code actually checks.
        'features': [{'key': key, 'name': name}
                     for key, name in premium.FEATURES.items()],
        'signed_in': False,
    }

    user, _ = actor_from_request(request)
    if user is not None:
        wallet = UserWallet.objects.filter(user=user).first()
        data.update({
            'signed_in': True,
            'balance_vc': wallet.wallet_balance if wallet else 0,
            'me': _standing(user),
            # The organisations this person may buy for, so the page can offer
            # the choice rather than only ever selling to the individual.
            'organisations': [
                {'slug': org.slug, 'name': org.org_name,
                 **_standing(org)}
                for org in my_orgs(user)
            ],
        })

    return _ok(data, 'The premium offer.')


@api_view(['POST'])
def premium_buy(request):
    """Charge the caller's wallet and turn premium on."""
    user, err = actor_from_request(request)
    if err:
        return err

    try:
        purchase = premium_sale.buy(
            user,
            months=request.data.get('months') or 1,
            org_slug=request.data.get('organisation') or None,
        )
    except premium_sale.PremiumSaleError as exc:
        # 409 for "you already have it", 402 for "you cannot afford it", 400
        # for the rest. A screen branches on the code, but the status is what
        # anything in between reads.
        http = status.HTTP_400_BAD_REQUEST
        if exc.code == 'ALREADY_PREMIUM':
            http = status.HTTP_409_CONFLICT
        elif exc.code == 'INSUFFICIENT_FUNDS':
            http = status.HTTP_402_PAYMENT_REQUIRED
        elif exc.code in ('ORG_NOT_YOURS',):
            http = status.HTTP_403_FORBIDDEN
        elif exc.code == 'ORG_NOT_FOUND':
            http = status.HTTP_404_NOT_FOUND
        return _err(exc.message, exc.code, http)

    holder = purchase.org if purchase.org_id else purchase.user
    wallet = UserWallet.objects.filter(user=user).first()
    return _ok({
        'coins': purchase.coins,
        'months': purchase.months,
        'period_start': purchase.period_start,
        'period_end': purchase.period_end,
        'balance_vc': wallet.wallet_balance if wallet else 0,
        'holder': _standing(holder),
    }, 'Premium is on.')


@api_view(['POST'])
def premium_interest(request):
    """Record that somebody wants premium while there is no price.

    The button on the offer page is never inert, and it never sends anybody to
    find a member of staff. It also tells the console WHICH refusal people are
    hitting, which is a question nothing could previously ask.
    """
    user, err = actor_from_request(request)
    if err:
        return err

    if premium_sale.offer()['on_sale']:
        # Nothing to record: they can buy it.
        return _err('Premium is on sale, so there is nothing to register.',
                    'PREMIUM_ON_SALE')

    row = premium_sale.register_interest(user, request.data.get('surface') or '')
    return _ok({'times': row.times}, 'Noted.')
