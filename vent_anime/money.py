"""Every coin the anime module moves, in one place.

Three sales: a chapter, early access to a chapter, and a month of a series. All
three do the same three things, which is why they are one function with an
argument rather than three that drift:

    take the coins from the reader -> credit the author -> write the row

## The author is PAID, not credited to a number on a screen

The reader's coins land in the author's wallet through the same
`vent_tournament.services.wallet` the whole platform uses, so the author sees
them beside their prize money and can withdraw them the same way. An earnings
figure that is not a wallet balance is a promise, and this platform has one
ledger.

## The platform's cut is a dashboard number, and today it is 0

The rate is `anime_fee_pct` in `AdminSetting.platform_fees`, beside the ticket
and stall fees, so an admin sets it without a deploy. It is 0 until somebody
decides otherwise: taking a percentage nobody chose is the fault
`holds.commission_rate` documents on the marketplace side. The fee is whole
coins, rounded down, off the author's credit; the reader pays the price on the
label. It is stamped on the chapter row at the sale, so a change on the
dashboard never rewrites what an author earned last month.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from vent_auth.models import UserWallet
from vent_tournament.services import wallet as wallet_service

from .models import ChapterPurchase, SeriesSubscription

#: A month of a subscription. 30 days, for the same reason premium uses 30: the
#: 31st of next month does not exist six times a year.
SUBSCRIPTION_DAYS = 30


class PaymentError(Exception):
    """Carries a code, because the screen showing it may be in French.

    And numbers beside it where there are any: a refusal for want of coins
    that does not say how many were needed cannot be turned into a card
    payment by the screen without asking the price a second time.
    """

    def __init__(self, code, message, **params):
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params


def _wallets(reader, author):
    reader_wallet = UserWallet.objects.select_for_update().filter(
        user=reader).first()
    if reader_wallet is None:
        raise PaymentError('NO_WALLET', 'That account has no wallet yet.')
    # The author's wallet is not locked for update when it is the same row, or
    # the second select deadlocks with the first on some backends.
    if author.user_id == reader.user_id:
        return reader_wallet, reader_wallet
    author_wallet = UserWallet.objects.select_for_update().filter(
        user=author).first()
    return reader_wallet, author_wallet


def platform_rate():
    """The platform's cut of a comic sale, as a percentage, from the dashboard."""
    from vent_auth.models import AdminSetting
    fees = AdminSetting.load().merged().get('platform_fees') or {}
    try:
        return max(0.0, float(fees.get('anime_fee_pct') or 0))
    except (TypeError, ValueError):
        return 0.0


def fee_on(coins, rate=None):
    """The platform's whole coins out of one sale, rounded DOWN.

    Down for the same reason every other fee here rounds down: a fee rounded
    up takes a coin the platform did not earn on every small sale.
    """
    rate = platform_rate() if rate is None else rate
    if not coins or rate <= 0:
        return 0
    return min(int(coins), int(Decimal(str(coins)) * Decimal(str(rate)) / Decimal('100')))


def _move(reader, author, coins, description):
    """Debit the reader, credit the author less the fee. Returns (tx, fee_vc)."""
    reader_wallet, author_wallet = _wallets(reader, author)
    if reader_wallet.wallet_balance < coins:
        raise PaymentError(
            'INSUFFICIENT_FUNDS',
            'You need %s VENT COINS and have %s.'
            % (coins, reader_wallet.wallet_balance),
            needed_vc=int(coins), balance_vc=reader_wallet.wallet_balance)

    fee = fee_on(coins)
    tx = wallet_service.debit(reader_wallet, coins, tx_type='deduction',
                              description=description)
    if author_wallet is not None and coins - fee > 0:
        wallet_service.credit(
            author_wallet, coins - fee, tx_type='prize',
            description=description + (' (after a %s VC V-ENT fee)' % fee if fee else ''))
    return tx, fee


def buy_chapter(reader, chapter, *, reason='chapter'):
    """Pay for one chapter. `reason` is 'chapter' or 'early'."""
    series = chapter.series
    if series.author_id == reader.user_id:
        raise PaymentError('YOUR_OWN', 'This is your own comic.')

    if reason == 'early':
        coins = chapter.early_access_vc
        if not chapter.is_early():
            raise PaymentError('ALREADY_OUT',
                               'That chapter is out already.')
    else:
        coins = series.chapter_price_vc
        if series.pricing != 'per_chapter':
            raise PaymentError('NOT_FOR_SALE',
                               'That chapter is not sold on its own.')

    if coins <= 0:
        raise PaymentError('NOTHING_TO_PAY', 'That has no price set.')

    with db_transaction.atomic():
        if ChapterPurchase.objects.filter(user=reader, chapter=chapter).exists():
            raise PaymentError('ALREADY_BOUGHT', 'You already have that one.')
        tx, fee = _move(reader, series.author, coins,
                        'Anime: %s #%s' % (series.title[:80], chapter.number))
        row = ChapterPurchase.objects.create(
            user=reader, chapter=chapter, coins=coins, fee_vc=fee, reason=reason)
    return row, tx


def subscribe(reader, series, months=1):
    """Pay for a month or more of a series. Extends rather than overwrites."""
    if series.author_id == reader.user_id:
        raise PaymentError('YOUR_OWN', 'This is your own comic.')
    if series.pricing != 'subscription':
        raise PaymentError('NOT_A_SUBSCRIPTION',
                           'That comic is not sold as a subscription.')
    months = max(1, min(int(months or 1), 12))
    coins = series.subscription_price_vc * months
    if coins <= 0:
        raise PaymentError('NOTHING_TO_PAY', 'That has no price set.')

    now = timezone.now()
    with db_transaction.atomic():
        row = SeriesSubscription.objects.select_for_update().filter(
            user=reader, series=series).first()
        # From the end of what is already paid for, or from today if it lapsed.
        start = row.until if (row and row.until > now) else now
        until = start + timedelta(days=SUBSCRIPTION_DAYS * months)

        tx, _fee = _move(reader, series.author, coins,
                         'Anime subscription: %s' % series.title[:80])

        if row is None:
            row = SeriesSubscription.objects.create(
                user=reader, series=series, until=until)
        else:
            row.until = until
            row.save(update_fields=['until'])
    return row, tx
