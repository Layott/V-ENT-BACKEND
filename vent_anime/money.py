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

## The platform takes nothing, today, and it says so

There is no commission on a chapter sale. Not because it is free forever, but
because nobody has decided a rate, and taking a percentage nobody chose is the
fault `holds.commission_rate` documents on the marketplace side. When a rate is
decided it goes in `AdminSetting.platform_fees` beside the others and is stamped
on the row at the sale, so a change never rewrites what an author earned last
month.
"""
from datetime import timedelta

from django.db import transaction as db_transaction
from django.utils import timezone

from vent_auth.models import UserWallet
from vent_tournament.services import wallet as wallet_service

from .models import ChapterPurchase, SeriesSubscription

#: A month of a subscription. 30 days, for the same reason premium uses 30: the
#: 31st of next month does not exist six times a year.
SUBSCRIPTION_DAYS = 30


class PaymentError(Exception):
    """Carries a code, because the screen showing it may be in French."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


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


def _move(reader, author, coins, description):
    """Debit the reader, credit the author, return the reader's transaction."""
    reader_wallet, author_wallet = _wallets(reader, author)
    if reader_wallet.wallet_balance < coins:
        raise PaymentError(
            'INSUFFICIENT_FUNDS',
            'You need %s VENT COINS and have %s.'
            % (coins, reader_wallet.wallet_balance))

    tx = wallet_service.debit(reader_wallet, coins, tx_type='deduction',
                              description=description)
    if author_wallet is not None:
        wallet_service.credit(author_wallet, coins, tx_type='prize',
                              description=description)
    return tx


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
        tx = _move(reader, series.author, coins,
                   'Anime: %s #%s' % (series.title[:80], chapter.number))
        row = ChapterPurchase.objects.create(
            user=reader, chapter=chapter, coins=coins, reason=reason)
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

        tx = _move(reader, series.author, coins,
                   'Anime subscription: %s' % series.title[:80])

        if row is None:
            row = SeriesSubscription.objects.create(
                user=reader, series=series, until=until)
        else:
            row.until = until
            row.save(update_fields=['until'])
    return row, tx
