"""Bidding, buying, reviewing, reporting and wishlists.

Every money move goes through `holds.py`, which is the only writer of a
purchase's status. Everything here is a caller.

Two things are deliberately NOT built here, because they exist:

* messaging a seller is `Conversation` and `DirectMessage` in `vent_auth`. This
  module only counts the inquiry, so the seller's analytics know it happened.
* reporting a listing is `UserReport`, with a marketplace `context`, so it
  lands in the admin queue that is already read rather than a second queue
  nobody opens.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth.actors import actor_from_request, may_override
from vent_auth.models import UserReport, Users
from vent_auth.views_notifications import create_notification
from vent_auth.slugs import lookup_kwargs

from . import catalogue, holds
from .models import Bid, Listing, Purchase, Review, Wishlist
from .views_listings import _person, _row


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _listing(reference):
    if not reference:
        return None
    return Listing.objects.filter(
        **lookup_kwargs(reference, id_field='listing_id')).first()


def _purchase(token):
    return Purchase.objects.filter(slug=token).select_related(
        'listing', 'buyer', 'seller').first()


def _bid_row(bid):
    return {
        'id': bid.id,
        'amount': bid.amount,
        'message': bid.message,
        'status': bid.status,
        'at': bid.created_at,
        'bidder': _person(bid.bidder),
    }


def _purchase_row(purchase, *, who=None):
    row = {
        'token': purchase.slug,
        'listing': _row(purchase.listing),
        'amount': purchase.amount,
        'quantity': purchase.quantity,
        'status': purchase.status,
        'note': purchase.note,
        'created_at': purchase.created_at,
        'settled_at': purchase.settled_at,
        'buyer': _person(purchase.buyer),
        'seller': _person(purchase.seller),
        'has_review': hasattr(purchase, 'review'),
    }
    # The seller sees the commission and what they actually receive. The buyer
    # sees what they paid, which is the number they agreed to; showing them a
    # breakdown of somebody else's earnings is not their business.
    if who is not None and who.user_id == purchase.seller_id:
        row['commission'] = purchase.commission
        row['seller_amount'] = purchase.seller_amount
    return row


# ---------------------------------------------------------------------------
# Bidding
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
def bids(request, reference):
    """GET or POST /marketplace/listings/<ref>/bids/."""
    user, err = actor_from_request(request)
    if err:
        return err
    listing = _listing(reference)
    if listing is None:
        return _err('No such listing.', 'LISTING_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        # A bidder sees their own bid; the seller sees them all. An open list
        # of everybody's offers turns a bid into an auction the seller did not
        # advertise.
        rows = listing.bids.select_related('bidder')
        if listing.seller_id != user.user_id:
            rows = rows.filter(bidder=user)
        return _ok({'bids': [_bid_row(b) for b in rows]}, 'Bids')

    if not listing.bidding:
        return _err('This listing does not take bids.', 'NO_BIDDING')
    if not listing.is_live():
        return _err('That listing is not available.', 'NOT_AVAILABLE')
    if listing.seller_id == user.user_id:
        return _err('You cannot bid on your own listing.', 'OWN_LISTING')
    if listing.bids_close_at and listing.bids_close_at <= timezone.now():
        return _err('Bidding on this closed on %s.'
                    % listing.bids_close_at.date().isoformat(), 'BIDS_CLOSED',
                    status.HTTP_409_CONFLICT)

    try:
        amount = int(request.data.get('amount'))
    except (TypeError, ValueError):
        return _err('How much are you offering?', 'VALIDATION_FAILED',
                    field='amount')
    if amount <= 0:
        return _err('An offer of more than nothing.', 'VALIDATION_FAILED',
                    field='amount')

    # One standing bid per person. A second is a replacement, not a queue of
    # offers from the same account.
    listing.bids.filter(bidder=user, status='open').update(
        status='withdrawn', settled_at=timezone.now())
    bid = Bid.objects.create(
        listing=listing, bidder=user, amount=amount,
        message=str(request.data.get('message') or '')[:300])

    Listing.objects.filter(pk=listing.pk).update(
        inquiries=(listing.inquiries or 0) + 1)
    create_notification(
        listing.seller_id, 'marketplace',
        'New offer on %s' % listing.title,
        body='%s offered %s VENT COINS.' % (user.username, amount),
        link='/marketplace/listing/%s' % (listing.slug or listing.pk),
        metadata={'listing': listing.pk, 'bid': bid.id})

    return _ok({'bid': _bid_row(bid)}, 'Offer sent.', status.HTTP_201_CREATED)


@api_view(['POST'])
def settle_bid(request, bid_id):
    """POST /marketplace/bids/<id>/ - the seller accepts or declines.

    Accepting does NOT move money. It tells the bidder they may buy at that
    price, and the buyer still has to commit, because taking coins out of
    somebody's wallet on somebody else's press is not a thing to build.
    """
    user, err = actor_from_request(request)
    if err:
        return err
    bid = Bid.objects.filter(pk=bid_id).select_related('listing', 'bidder').first()
    if bid is None:
        return _err('No such offer.', 'BID_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if bid.listing.seller_id != user.user_id:
        return _err('This is not your listing.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)
    if bid.status != 'open':
        return _err('That offer is already %s.' % bid.status, 'ALREADY_SETTLED',
                    status.HTTP_409_CONFLICT)

    decision = str(request.data.get('decision') or '').strip()
    if decision not in ('accepted', 'declined'):
        return _err('Accept it or decline it.', 'VALIDATION_FAILED',
                    field='decision')

    bid.status = decision
    bid.settled_at = timezone.now()
    bid.save(update_fields=['status', 'settled_at'])

    create_notification(
        bid.bidder_id, 'marketplace',
        'Your offer was %s' % decision,
        body='%s on "%s".' % (bid.amount, bid.listing.title),
        link='/marketplace/listing/%s' % (bid.listing.slug or bid.listing.pk),
        metadata={'listing': bid.listing_id, 'bid': bid.id})

    return _ok({'bid': _bid_row(bid)}, 'Offer %s.' % decision)


# ---------------------------------------------------------------------------
# Buying
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
def buy(request, reference):
    """GET the quote, POST to commit.

    The GET is why this is two methods rather than one: the spec's commission
    has to be visible BEFORE somebody pays, and a fee discovered on the receipt
    is a fee nobody agreed to.
    """
    user, err = actor_from_request(request)
    if err:
        return err
    listing = _listing(reference)
    if listing is None:
        return _err('No such listing.', 'LISTING_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    try:
        quantity = max(1, int(request.data.get('quantity')
                              or request.GET.get('quantity') or 1))
    except (TypeError, ValueError):
        quantity = 1

    # An accepted offer is what this buyer pays, rather than the asking price.
    accepted = listing.bids.filter(bidder=user, status='accepted').first()
    total = accepted.amount if accepted else listing.price * quantity
    commission, seller_amount = holds.quote(total)

    if request.method == 'GET':
        return _ok({
            'total': total,
            'commission': commission,
            'seller_receives': seller_amount,
            'from_accepted_offer': bool(accepted),
        }, 'Quote')

    if not request.data.get('confirm'):
        # The same shape as deleting a tournament and paying prizes: the facts
        # first, then a deliberate second press.
        return _err('Check what you are about to pay, then confirm.',
                    'CONFIRM_REQUIRED', status.HTTP_409_CONFLICT)

    try:
        purchase = holds.hold(listing, user, quantity=quantity,
                              amount=accepted.amount if accepted else None)
    except holds.PurchaseError as exc:
        http = (status.HTTP_409_CONFLICT if exc.code in ('NOT_AVAILABLE', 'NOT_ENOUGH')
                else status.HTTP_400_BAD_REQUEST)
        return _err(exc.message, exc.code, http)

    create_notification(
        listing.seller_id, 'marketplace',
        'Somebody bought %s' % listing.title,
        body='%s VENT COINS are held until you deliver.' % purchase.amount,
        link='/marketplace/dashboard',
        metadata={'listing': listing.pk, 'purchase': purchase.slug})

    return _ok({'purchase': _purchase_row(purchase, who=user)},
               'Your coins are held until it is delivered.',
               status.HTTP_201_CREATED)


@api_view(['GET'])
def my_purchases(request):
    """GET /marketplace/purchases/ - what I bought and what I sold."""
    user, err = actor_from_request(request)
    if err:
        return err
    bought = Purchase.objects.filter(buyer=user).select_related('listing', 'seller')
    sold = Purchase.objects.filter(seller=user).select_related('listing', 'buyer')
    return _ok({
        'bought': [_purchase_row(p, who=user) for p in bought],
        'sold': [_purchase_row(p, who=user) for p in sold],
    }, 'Your purchases')


@api_view(['POST'])
def settle_purchase(request, token):
    """POST /marketplace/purchases/<token>/ - release, refund, cancel or dispute.

    Who may do what, and it is not symmetrical:

      release   the BUYER confirms delivery. Or an admin, settling a dispute.
      refund    the SELLER gives up, or an admin decides.
      cancel    the BUYER, before the seller has done anything.
      dispute   either side.
    """
    user, err = actor_from_request(request)
    if err:
        return err
    purchase = _purchase(token)
    if purchase is None:
        return _err('No such purchase.', 'PURCHASE_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    is_buyer = purchase.buyer_id == user.user_id
    is_seller = purchase.seller_id == user.user_id
    # `approve_payouts`, not a tournament permission: settling somebody else's
    # purchase is moving money on their behalf, which is the same act the
    # payout queue asks for. A permission name that does not exist silently
    # answers False, and `vent_auth/tests_permission_names.py` holds this.
    is_admin = may_override(user, 'approve_payouts')
    if not (is_buyer or is_seller or is_admin):
        return _err('This is not your purchase.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)

    action = str(request.data.get('action') or '').strip()
    note = str(request.data.get('note') or '')[:300]

    try:
        if action == 'release':
            if not (is_buyer or is_admin):
                return _err('Only the buyer confirms delivery.', 'NOT_YOURS',
                            status.HTTP_403_FORBIDDEN)
            holds.release(purchase, by=user, note=note)
            create_notification(
                purchase.seller_id, 'marketplace',
                'You have been paid for %s' % purchase.listing.title,
                body='%s VENT COINS, after the platform fee.' % purchase.seller_amount,
                link='/wallets',
                metadata={'purchase': purchase.slug})
        elif action in ('refund', 'cancel'):
            if action == 'cancel' and not (is_buyer or is_admin):
                return _err('Only the buyer can call it off.', 'NOT_YOURS',
                            status.HTTP_403_FORBIDDEN)
            if action == 'refund' and not (is_seller or is_admin):
                return _err('Only the seller or an admin can refund this.',
                            'NOT_YOURS', status.HTTP_403_FORBIDDEN)
            holds.refund(purchase, by=user, note=note, cancelled=(action == 'cancel'))
            create_notification(
                purchase.buyer_id, 'marketplace',
                'Your coins are back',
                body='%s VENT COINS returned for "%s".'
                     % (purchase.amount, purchase.listing.title),
                link='/wallets', metadata={'purchase': purchase.slug})
        elif action == 'dispute':
            holds.dispute(purchase, by=user, note=note)
            # Raised as a report as well, so it is in the queue an admin
            # already reads rather than a second one nobody opens.
            other = purchase.seller if is_buyer else purchase.buyer
            UserReport.objects.create(
                reporter=user, reported=other, reason='scam',
                detail=note or 'Marketplace purchase disputed.',
                context='marketplace:%s' % purchase.slug)
        else:
            return _err('Release it, refund it, cancel it or dispute it.',
                        'VALIDATION_FAILED', field='action')
    except holds.PurchaseError as exc:
        return _err(exc.message, exc.code, status.HTTP_409_CONFLICT)

    return _ok({'purchase': _purchase_row(purchase, who=user)}, 'Done.')


# ---------------------------------------------------------------------------
# Trust
# ---------------------------------------------------------------------------

@api_view(['POST'])
def review(request, token):
    """POST /marketplace/purchases/<token>/review/ - only after a real purchase."""
    user, err = actor_from_request(request)
    if err:
        return err
    purchase = _purchase(token)
    if purchase is None:
        return _err('No such purchase.', 'PURCHASE_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if purchase.buyer_id != user.user_id:
        return _err('Only the buyer reviews a purchase.', 'NOT_YOURS',
                    status.HTTP_403_FORBIDDEN)
    if purchase.status != 'released':
        return _err('Review it once it is delivered and settled.',
                    'NOT_SETTLED', status.HTTP_409_CONFLICT)
    if hasattr(purchase, 'review'):
        return _err('You have already reviewed this.', 'ALREADY_REVIEWED',
                    status.HTTP_409_CONFLICT)

    try:
        rating = int(request.data.get('rating'))
    except (TypeError, ValueError):
        return _err('One to five.', 'VALIDATION_FAILED', field='rating')
    if not 1 <= rating <= 5:
        return _err('One to five.', 'VALIDATION_FAILED', field='rating')

    row = Review.objects.create(
        purchase=purchase, listing=purchase.listing, reviewer=user,
        seller=purchase.seller, rating=rating,
        body=str(request.data.get('body') or '')[:2000])
    create_notification(
        purchase.seller_id, 'marketplace', 'A new review',
        body='%s left %s stars on "%s".'
             % (user.username, rating, purchase.listing.title),
        link='/marketplace/seller/%s' % purchase.seller.username,
        metadata={'review': row.id})
    return _ok({'rating': row.rating, 'body': row.body}, 'Thank you.',
               status.HTTP_201_CREATED)


@api_view(['POST'])
def report_listing(request, reference):
    """POST /marketplace/listings/<ref>/report/ - into the queue that exists."""
    user, err = actor_from_request(request)
    if err:
        return err
    listing = _listing(reference)
    if listing is None:
        return _err('No such listing.', 'LISTING_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if listing.seller_id == user.user_id:
        return _err('That is your own listing.', 'OWN_LISTING')

    reason = str(request.data.get('reason') or 'scam').strip()
    if reason not in dict(UserReport.REASONS):
        reason = 'other'

    UserReport.objects.create(
        reporter=user, reported=listing.seller, reason=reason,
        detail=str(request.data.get('detail') or '')[:2000],
        context='marketplace:listing:%s' % (listing.slug or listing.pk))
    return _ok({}, 'Reported. Somebody will look at it.',
               status.HTTP_201_CREATED)


@api_view(['POST'])
def inquire(request, reference):
    """POST /marketplace/listings/<ref>/inquire/ - open the conversation.

    The message itself lives in `Conversation` and `DirectMessage`, which is
    the platform's one inbox. This returns the conversation to open and counts
    the inquiry for the seller's analytics.
    """
    from vent_auth.models import Conversation, DirectMessage

    user, err = actor_from_request(request)
    if err:
        return err
    listing = _listing(reference)
    if listing is None:
        return _err('No such listing.', 'LISTING_NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    if listing.seller_id == user.user_id:
        return _err('That is your own listing.', 'OWN_LISTING')

    # The pair is stored in a fixed order, so two people cannot end up with two
    # conversations about each other.
    a, b = sorted([user, listing.seller], key=lambda u: u.user_id)
    conversation, _made = Conversation.objects.get_or_create(user_a=a, user_b=b)

    body = str(request.data.get('message') or '').strip()
    if body:
        DirectMessage.objects.create(
            conversation=conversation, sender=user,
            body='About "%s": %s' % (listing.title, body[:1500]))
        conversation.last_message_at = timezone.now()
        conversation.save(update_fields=['last_message_at'])
        create_notification(
            listing.seller_id, 'marketplace',
            'A question about %s' % listing.title,
            body=body[:180], link='/messages/%s' % conversation.slug,
            metadata={'listing': listing.pk})

    Listing.objects.filter(pk=listing.pk).update(
        inquiries=(listing.inquiries or 0) + 1)
    return _ok({'conversation': conversation.slug}, 'Message sent.')


# ---------------------------------------------------------------------------
# Wishlists
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
def wishlist(request):
    """GET or POST /marketplace/wishlist/ - what somebody is looking for."""
    user, err = actor_from_request(request)
    if err:
        return err

    if request.method == 'GET':
        rows = Wishlist.objects.filter(user=user)
        return _ok({'wishlist': [
            {'id': w.id, 'text': w.text, 'category': w.category,
             'kind': w.kind, 'max_price': w.max_price}
            for w in rows]}, 'Wishlist')

    text = str(request.data.get('text') or '').strip()
    category = str(request.data.get('category') or '').strip()
    if not text and not category:
        return _err('Say what you are looking for.', 'VALIDATION_FAILED',
                    field='text')
    if category and category not in catalogue.CATEGORIES:
        return _err('Choose a category.', 'VALIDATION_FAILED', field='category')

    row = Wishlist.objects.create(
        user=user, text=text[:140], category=category,
        kind=str(request.data.get('kind') or '').strip(),
        max_price=request.data.get('max_price') or None)
    return _ok({'id': row.id}, 'Saved. You will be told when one appears.',
               status.HTTP_201_CREATED)


@api_view(['DELETE'])
def wishlist_delete(request, row_id):
    user, err = actor_from_request(request)
    if err:
        return err
    deleted, _ = Wishlist.objects.filter(pk=row_id, user=user).delete()
    if not deleted:
        return _err('Not on your list.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return _ok({}, 'Removed.')


@api_view(['GET'])
def admin_holds(request):
    """GET /marketplace/admin/holds/ - every purchase with money sitting in it.

    Money held by a platform that cannot list what it is holding is money
    nobody is watching.
    """
    user, err = actor_from_request(request)
    if err:
        return err
    if not may_override(user, 'approve_payouts'):
        return _err('Admins only.', 'ADMIN_ONLY', status.HTTP_403_FORBIDDEN)

    rows = holds.open_holds().select_related('listing', 'buyer', 'seller')
    return _ok({
        'holds': [_purchase_row(p) for p in rows],
        'total_held': sum(p.amount for p in rows),
        'commission_rate': holds.commission_rate(),
    }, 'Open holds')
