"""The only place a marketplace purchase moves money.

The spec asks the platform to "facilitate secure and reliable transactions" and
to "charge a percentage fee or commission on a successful transaction". Between
a stranger paying and a stranger delivering there has to be somewhere for the
coins to sit, and that is what this is: the buyer's balance is debited when they
commit, and the seller is credited only when the thing arrives.

## Four moves, and nothing else may write `Purchase.status`

    hold()      buyer -> platform. The coins leave the buyer's balance.
    release()   platform -> seller, less the commission. Buyer confirms, or an
                admin decides.
    refund()    platform -> buyer. Seller could not deliver, or an admin says.
    dispute()   nothing moves. An admin now owns the decision.

A second writer is a second answer to who has the money, which is the fault the
event ledger was written to avoid and the reason it says "a ledger, not a
balance".

## Where the coins actually sit

There is no platform wallet row on this platform. A hold is therefore a DEBIT
with no matching credit until it settles, and the `Purchase` row IS the record
of what is owed - which is the same shape the event ledger uses, and it is why
the row stores all three numbers rather than computing two of them.

## The commission

Read from `AdminSetting.platform_fees`, beside the ticketing fee, so it is one
place an admin can see and change rather than a deploy. It is decided AT THE
PURCHASE and stamped on the row: a rate change next month must never rewrite
what a sale earned last month. Rounded down, through the same `fee_on` the
ticketing side uses, so V-ENT never takes a coin it did not earn.
"""
from django.db import transaction as db_transaction
from django.utils import timezone

from vent_auth.models import UserWallet
from vent_event.ledger import fee_on          # one rounding rule, not two
from vent_tournament.services import wallet as wallet_service

from .models import Purchase


class PurchaseError(Exception):
    """Carries a code, because the screen showing it may be in French."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def commission_rate():
    """The marketplace's cut, as a percentage. 0 unless an admin has set one.

    Defaults to nothing for the same reason the ticketing fee does: charging an
    amount nobody chose is worse than charging nothing.
    """
    try:
        from vent_auth.models import AdminSetting
        fees = AdminSetting.load().merged().get('platform_fees') or {}
        # `listing_fee_pct`, which has been in DEFAULT_ADMIN_SETTINGS since
        # the settings row was written and has never had a reader. A new key
        # beside it would be a second name for the same number.
        return max(0.0, float(fees.get('listing_fee_pct') or 0))
    except Exception:
        return 0.0


def quote(amount):
    """(commission, seller_amount) for an amount, at today's rate.

    Public so a screen can show both numbers BEFORE somebody commits. A
    commission somebody discovers on the receipt is a commission they did not
    agree to.
    """
    commission = fee_on(amount, commission_rate())
    return commission, max(amount - commission, 0)


def _wallet(user):
    wallet = UserWallet.objects.select_for_update().filter(user=user).first()
    if wallet is None:
        raise PurchaseError('NO_WALLET', 'That account has no wallet yet.')
    return wallet


def hold(listing, buyer, quantity=1, amount=None):
    """Take the buyer's coins out of their balance and hold them.

    `amount` is passed only when a bid was accepted at a different price than
    the listing asks; otherwise it is the listing's price times the quantity.
    """
    if buyer.user_id == listing.seller_id:
        raise PurchaseError('OWN_LISTING', 'You cannot buy your own listing.')
    if not listing.is_live():
        raise PurchaseError('NOT_AVAILABLE', 'That listing is not available.')

    quantity = max(1, int(quantity or 1))
    if listing.kind == 'sale' and quantity > listing.quantity:
        raise PurchaseError('NOT_ENOUGH',
                            'There are only %s left.' % listing.quantity)

    total = int(amount if amount is not None else listing.price * quantity)
    if total <= 0:
        raise PurchaseError('NOTHING_TO_PAY', 'That listing has no price set.')

    commission, seller_amount = quote(total)

    with db_transaction.atomic():
        wallet = _wallet(buyer)
        if wallet.wallet_balance < total:
            raise PurchaseError(
                'INSUFFICIENT_FUNDS',
                'You need %s VENT COINS and have %s.'
                % (total, wallet.wallet_balance))

        tx = wallet_service.debit(
            wallet, total,
            tx_type='deduction',
            description='Marketplace: %s' % listing.title[:180])

        purchase = Purchase.objects.create(
            listing=listing, buyer=buyer, seller=listing.seller,
            amount=total, commission=commission, seller_amount=seller_amount,
            quantity=quantity, status='held', hold_transaction=tx)

        # A sale's stock goes down when the money is held, not when it is
        # released. Two people cannot both buy the last one while the first is
        # waiting for delivery.
        if listing.kind == 'sale':
            listing.quantity = max(listing.quantity - quantity, 0)
            if listing.quantity == 0:
                listing.status = 'sold'
            listing.save(update_fields=['quantity', 'status'])

    return purchase


def _settle(purchase, status, *, by=None, note=''):
    purchase.status = status
    purchase.settled_at = timezone.now()
    purchase.settled_by = by
    if note:
        purchase.note = note[:300]
    purchase.save(update_fields=['status', 'settled_at', 'settled_by', 'note'])
    return purchase


def release(purchase, *, by=None, note=''):
    """Pay the seller, less the commission."""
    if purchase.status != 'held':
        raise PurchaseError('NOT_HELD',
                            'That purchase is %s.' % purchase.get_status_display())

    with db_transaction.atomic():
        wallet = _wallet(purchase.seller)
        tx = wallet_service.credit(
            wallet, purchase.seller_amount,
            tx_type='receive',
            description='Marketplace sale: %s' % purchase.listing.title[:170])
        purchase.release_transaction = tx
        purchase.save(update_fields=['release_transaction'])
        _settle(purchase, 'released', by=by, note=note)

        listing = purchase.listing
        listing.completed = (listing.completed or 0) + 1
        listing.save(update_fields=['completed'])
    return purchase


def refund(purchase, *, by=None, note='', cancelled=False):
    """Give the buyer their coins back, and put the stock back on the shelf."""
    if purchase.status not in ('held', 'disputed'):
        raise PurchaseError('NOT_HELD',
                            'That purchase is %s.' % purchase.get_status_display())

    with db_transaction.atomic():
        wallet = _wallet(purchase.buyer)
        wallet_service.credit(
            wallet, purchase.amount,
            tx_type='refund',
            description='Marketplace refund: %s' % purchase.listing.title[:170])
        _settle(purchase, 'cancelled' if cancelled else 'refunded',
                by=by, note=note)

        listing = purchase.listing
        if listing.kind == 'sale':
            listing.quantity = (listing.quantity or 0) + purchase.quantity
            if listing.status == 'sold':
                listing.status = 'active'
            listing.save(update_fields=['quantity', 'status'])
    return purchase


def dispute(purchase, *, by=None, note=''):
    """Nothing moves. An admin decides what happens next.

    Deliberately not a money move: the point of a dispute is that neither side
    gets the coins until somebody has read both accounts.
    """
    if purchase.status != 'held':
        raise PurchaseError('NOT_HELD',
                            'That purchase is %s.' % purchase.get_status_display())
    purchase.status = 'disputed'
    purchase.note = (note or '')[:300]
    purchase.save(update_fields=['status', 'note'])
    return purchase


def open_holds():
    """Every purchase with money sitting in it.

    Read by the admin console, and the reason it exists: money held by a
    platform that cannot list what it is holding is money nobody is watching.
    """
    return Purchase.objects.filter(status__in=('held', 'disputed'))
