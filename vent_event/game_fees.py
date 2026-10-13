"""Paying to play a party game, and getting it back (inbox 449).

CEO, 12 October 2026, approving the party games mockup: "add that organizers can
add entry fees to games aso if they want to."

The rules, decided with the build (recorded in the handover of 13 October):

- The fee is whole VENT COINS on the game (`EventGame.fee_vc`), 0 for free.
  It is an event sale like a ticket: the same platform fee as a ticket, on
  whoever the organiser put the event's fee on, written to the event's ledger
  keyed to the entry (`ledger.record_game_fee`), paid out by the same
  settlement run.
- Paying takes coins with the wallet PIN, from whoever is signed in. A guest
  ticket has no account, so a guest signs in to pay; the coins come back to
  that account (`fee_payer`). A card covers a shortfall on the screen
  (PayShortfall), which tops the wallet up and presses again.
- You pay what the game cost when you joined. A place is paid for when it is
  taken: a full paid game refuses (GAME_FULL) rather than charging for a place
  on a waiting list that may never come.
- The coins come back, once, when the person leaves before the game starts
  (or before a start time is set), when the organiser takes the game off, and
  when the ticket itself is refunded or voided (`game_entries.drop_ticket`),
  which also covers a cancelled event. Leaving after the start keeps the fee:
  the place was used.
- Games ticked in checkout never charge: `game_entries.join_picks` skips a
  game with a fee, and the screen says those are joined on the Games tab.
"""
import secrets
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from vent_auth import coins, wallets
from vent_auth.models import Transaction, UserWallet

from . import ledger
from .models import EventGame, EventGameEntry

LIVE = (EventGameEntry.IN, EventGameEntry.WAITING)

#: The most a game may charge to play, in whole coins (a million naira).
MAX_ENTRY_VC = 1000


class GameFeeError(Exception):
    """A refusal with the code the screen translates and the numbers it needs."""

    def __init__(self, message, code, status=400, **params):
        super().__init__(message)
        self.code = code
        self.status = status
        self.params = params


def game_name(game):
    """The game as a wallet line names it: the organiser's name for their own,
    the catalogue key in words for one of the fifty (the history line is read
    back into the reader's language by vent_auth.statement_lines)."""
    if game.custom_name:
        return game.custom_name
    return game.catalogue_key.replace('-', ' ').title()


def has_started(game, now=None):
    return bool(game.starts_at and (now or timezone.now()) >= game.starts_at)


def join_paid(game, ticket, payer, pin):
    """Take the fee from `payer` and put `ticket` in `game`. Returns the entry.

    Already in or waiting: nothing is charged and the entry comes back as it
    is. The PIN is checked before the transaction, so a wrong PIN's count is
    kept even though nothing else is written (wallets.check_pin saves it).
    """
    wallet = UserWallet.objects.filter(user=payer).first()
    if wallet is None:
        raise GameFeeError('No wallet found for this account.', 'NO_WALLET')
    try:
        wallets.check_pin(wallet, pin)
    except wallets.WalletError as exc:
        raise GameFeeError(str(exc), exc.code, **exc.params) from None

    with transaction.atomic():
        locked = EventGame.objects.select_for_update().select_related('event').get(pk=game.pk)
        if locked.status != EventGame.OPEN:
            raise GameFeeError('Sign ups for this game are closed.', 'GAME_NOT_OPEN', 409)
        entry = EventGameEntry.objects.filter(game=locked, ticket=ticket).first()
        if entry is not None and entry.state in LIVE:
            return entry
        playing = EventGameEntry.objects.filter(game=locked, state=EventGameEntry.IN).count()
        if locked.places is not None and playing >= locked.places:
            raise GameFeeError('This game is full.', 'GAME_FULL', 409,
                               places=locked.places)

        priced = ledger.quote_game(locked)
        total = Decimal(priced['total_vc'])
        held = UserWallet.objects.select_for_update().get(pk=wallet.pk)
        if held.wallet_balance < total:
            # The numbers ride with the code so the screen can offer a card for
            # exactly the shortfall (vent_auth/pay.py).
            raise GameFeeError(
                'You need %s VC to play - your balance is %s VC.'
                % (coins.label(total), coins.label(held.wallet_balance)),
                'INSUFFICIENT_BALANCE', 400,
                needed_vc=int(total), balance_vc=coins.as_json(held.wallet_balance))
        held.wallet_balance -= total
        held.save(update_fields=['wallet_balance'])
        reference = 'game-%s' % secrets.token_hex(8)
        description = 'Game: %s - %s' % (game_name(locked), locked.event.name)
        if priced['buyer_fee_vc']:
            description += ' (incl. %s VC service fee)' % priced['buyer_fee_vc']
        Transaction.objects.create(wallet=held, type='deduction', amount=-total,
                                   description=description[:255], status='completed',
                                   reference=reference)

        if entry is None:
            entry = EventGameEntry(game=locked, ticket=ticket)
        entry.state = EventGameEntry.IN
        entry.left_at = None
        entry.joined_at = timezone.now()
        entry.fee_paid_vc = total
        entry.fee_payer = payer
        entry.fee_reference = reference
        entry.save()
        ledger.record_game_fee(entry, priced)
        return entry


def refund(entry, reason=''):
    """Give back what this entry paid, to whoever paid it, once.

    Returns the coins returned (0 when nothing was owed). Safe to call twice:
    the second call finds nothing paid. The ledger lines are reversed in the
    same transaction, so the organiser is never paid for a place returned.
    """
    with transaction.atomic():
        held = (EventGameEntry.objects.select_for_update()
                .select_related('game__event').get(pk=entry.pk))
        paid = Decimal(held.fee_paid_vc or 0)
        if paid <= 0 or held.fee_payer_id is None:
            return Decimal('0')
        wallet = UserWallet.objects.select_for_update().filter(user_id=held.fee_payer_id).first()
        if wallet is None:
            return Decimal('0')
        wallet.wallet_balance += paid
        wallet.save(update_fields=['wallet_balance'])
        # Its own reference: a Transaction reference is unique, and the
        # payment it returns already holds `fee_reference`.
        Transaction.objects.create(
            wallet=wallet, type='refund', amount=paid,
            description=('Refund - game: %s - %s'
                         % (game_name(held.game), held.game.event.name))[:255],
            status='completed', reference='%s-refund-%s' % (held.fee_reference or 'game',
                                                            secrets.token_hex(4)))
        ledger.reverse_game_fee(held, reason or 'Game fee refunded')
        held.fee_paid_vc = 0
        held.save(update_fields=['fee_paid_vc'])
    entry.fee_paid_vc = Decimal('0')
    return paid


def refund_all(game, reason=''):
    """Every fee paid for `game` back to its payers: the game came off."""
    total = Decimal('0')
    for entry in EventGameEntry.objects.filter(game=game, fee_paid_vc__gt=0):
        total += refund(entry, reason)
    return total
