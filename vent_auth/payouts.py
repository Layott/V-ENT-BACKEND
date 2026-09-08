"""Where a payout is allowed to go, and how much of one is allowed at once.

From the VENT WALLET spec: "Request payouts in USDT to my crypto wallet, to
withdraw earnings or balance."

## What is here and what is deliberately not

Nothing in this module touches a chain. The custody decision in
`tasks/specs/crypto-and-custody.md` has not been made, and until it is, no
line of code can know whose key signs a send. What IS the same under all three
answers is everything around the send, and that is what this builds: the
destination, the proof that the destination belongs to the person asking, the
shape check that stops money going to the wrong network, and the limits.

The crypto document says it in those words: "the wallet leaf builds every part
of the payout pipeline that is rail independent: the request, the approval,
the audit trail, the notification, the ledger lines and the limits. That work
is not wasted under any of the three answers."

## Two checks that exist because the failures are permanent

1. **The address is proved, not typed.** A code goes to the account's email
   and has to come back. Somebody who gets into a session cannot add a
   destination without also holding the mailbox, and an address already proved
   cannot be edited: it is removed and a new one is proved. A chain
   transaction does not reverse, so the moment to be careful is before.

2. **The address is checked against its network.** A TRON address is 34
   characters starting with T; an Ethereum address is 0x and 40 hex digits.
   They are not interchangeable and the chains do not warn: USDT sent on TRON
   to an Ethereum address is gone, quietly, with a successful transaction
   receipt. Checking the shape catches the whole class before the money moves.
"""
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import PayoutAddress, WithdrawalRequest


class PayoutError(Exception):
    """A refusal, carrying the code the frontend translates.

    `params` are the numbers the sentence is built around. A code alone cannot
    say "the smallest payout is 5", and a sentence built in Python cannot be
    translated, so both travel: the code names the meaning and the params fill
    the translation's placeholders.
    """

    def __init__(self, message, code, **params):
        super().__init__(message)
        self.code = code
        self.params = params


# ---------------------------------------------------------------------------
# Does this address belong on this network
# ---------------------------------------------------------------------------

# Base58 as Bitcoin and TRON define it: no 0, no O, no I, no l, because those
# are the characters people mistake for each other when copying by hand.
_TRON = re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$')
_ETH = re.compile(r'^0x[0-9a-fA-F]{40}$')

_SHAPES = {
    PayoutAddress.NETWORK_TRC20: _TRON,
    PayoutAddress.NETWORK_ERC20: _ETH,
}


def clean_address(network, address):
    """The address, tidied, or a refusal naming what is wrong with it.

    An Ethereum address typed into the TRON field is the single most expensive
    typing mistake available on this platform, so it is refused by shape here
    rather than discovered by a user whose money did not arrive.
    """
    network = str(network or '').strip().lower()
    address = str(address or '').strip()
    if network not in _SHAPES:
        raise PayoutError('Choose the network the address is on.',
                          'VALIDATION_ERROR')
    if not address:
        raise PayoutError('Enter the address.', 'VALIDATION_ERROR')

    if not _SHAPES[network].match(address):
        if network == PayoutAddress.NETWORK_TRC20 and _ETH.match(address):
            raise PayoutError(
                'That is an Ethereum address, filed under TRON. Sending to it '
                'on TRON would lose the money.', 'WRONG_NETWORK')
        if network == PayoutAddress.NETWORK_ERC20 and _TRON.match(address):
            raise PayoutError(
                'That is a TRON address, filed under Ethereum. Sending to it '
                'on Ethereum would lose the money.', 'WRONG_NETWORK')
        raise PayoutError('That does not look like an address on that '
                          'network.', 'BAD_ADDRESS')
    return network, address


# ---------------------------------------------------------------------------
# Proving it belongs to the person asking
# ---------------------------------------------------------------------------

#: How long a confirmation code is worth typing. Long enough to find the
#: email, short enough that one read out of an old inbox is no use.
CODE_MINUTES = 30


def new_code():
    return ''.join(secrets.choice('0123456789') for _ in range(6))


def add_address(user, network, address, label=''):
    """File an address and send the code that proves it. Returns the row.

    Adding the same address twice is not an error: it re-sends the code for
    the one that is already there. Somebody who lost the email presses the
    button again, and a second identical row would be a second thing to
    choose between on the payout screen.
    """
    network, address = clean_address(network, address)

    row = PayoutAddress.objects.filter(
        user=user, network=network, address=address).first()
    if row is not None and row.confirmed:
        raise PayoutError('That address is already on your account.',
                          'ALREADY_ADDED')
    if row is None:
        row = PayoutAddress(user=user, network=network, address=address)

    row.label = str(label or '')[:60]
    row.confirm_code = new_code()
    row.confirm_sent_at = timezone.now()
    row.save()

    try:
        from . import emails
        emails.send_payout_address_code(user, row)
    except Exception:                                            # noqa: BLE001
        # The row is written either way. A code that exists and an email that
        # did not arrive is fixed by asking again; a missing row is not.
        pass
    return row


def confirm_address(user, ref, code):
    """Prove an address with the code that was emailed. Returns the row."""
    row = PayoutAddress.objects.filter(user=user, ref=str(ref or '')).first()
    if row is None:
        raise PayoutError('No such address on your account.', 'NOT_FOUND')
    if row.confirmed:
        return row
    if not row.confirm_code or not row.confirm_sent_at:
        raise PayoutError('Ask for a new code.', 'CODE_EXPIRED')
    age = timezone.now() - row.confirm_sent_at
    if age.total_seconds() > CODE_MINUTES * 60:
        raise PayoutError('That code has expired. Ask for a new one.',
                          'CODE_EXPIRED')
    if str(code or '').strip() != row.confirm_code:
        raise PayoutError('That code is not right.', 'INVALID_CODE')

    row.confirmed_at = timezone.now()
    # Spent, so a code read out of an old email cannot be used again.
    row.confirm_code = ''
    row.save(update_fields=['confirmed_at', 'confirm_code'])
    return row


def remove_address(user, ref):
    """Take an address off the account.

    A row that has paid something out is kept and only unconfirmed, because
    `WithdrawalRequest.payout_address` is PROTECT: where money went is not a
    thing a person can delete from their own history.
    """
    row = PayoutAddress.objects.filter(user=user, ref=str(ref or '')).first()
    if row is None:
        raise PayoutError('No such address on your account.', 'NOT_FOUND')
    if row.withdrawals.exists():
        row.confirmed_at = None
        row.save(update_fields=['confirmed_at'])
        return row
    row.delete()
    return None


def address_payload(row):
    return {
        'ref': row.ref,
        'network': row.network,
        'network_label': dict(PayoutAddress.NETWORK_CHOICES).get(
            row.network, row.network),
        'address': row.address,
        'short': row.short,
        'label': row.label,
        'confirmed': row.confirmed,
        'added_at': row.created_at.isoformat() if row.created_at else None,
    }


def confirmed_address(user, ref):
    """The address a payout may go to, or a refusal. Never an unproved one."""
    row = PayoutAddress.objects.filter(user=user, ref=str(ref or '')).first()
    if row is None:
        raise PayoutError('Choose an address to be paid to.', 'NOT_FOUND')
    if not row.confirmed:
        raise PayoutError('Confirm that address before it can be paid to.',
                          'ADDRESS_NOT_CONFIRMED')
    return row


# ---------------------------------------------------------------------------
# How much may go at once
# ---------------------------------------------------------------------------

def _limit(name, fallback):
    try:
        return int(getattr(settings, name, fallback))
    except (TypeError, ValueError):
        return fallback


def usdt_enabled():
    """Whether a USDT payout may be ASKED for yet.

    Off until the custody question is answered and a float exists behind it.
    Everything up to the send is built and tested; offering the option before
    somebody can actually send would hold a balance for a payout nobody can
    complete, which is worse than not offering it.
    """
    return bool(getattr(settings, 'USDT_PAYOUTS_ENABLED', False))


def limits():
    """The payout ceilings, in VENT COINS. Settings, so they can be moved.

    They are rail independent, which is why they are built now: a daily
    ceiling is the same number whether the money leaves as naira or as USDT,
    and it is the control that limits what a compromised account can take
    before anybody notices. `0` on the daily ceiling means no ceiling.
    """
    return {
        'minimum': _limit('PAYOUT_MINIMUM_VC', 5),
        'daily_max': _limit('PAYOUT_DAILY_MAX_VC', 500),
    }


def check_limits(wallet, amount):
    """Raise unless this payout is within the minimum and the daily ceiling.

    The day is counted from requests, not from approvals: somebody asking for
    twenty payouts an admin has not looked at yet is exactly the case a
    ceiling exists for, and counting approvals would let all twenty be made.
    """
    amount = int(amount)
    caps = limits()
    if amount < caps['minimum']:
        raise PayoutError(
            'The smallest payout is %d VENT COINS.' % caps['minimum'],
            'BELOW_MINIMUM', minimum=caps['minimum'])

    if caps['daily_max'] <= 0:
        return
    since = timezone.now() - timedelta(days=1)
    already = sum(WithdrawalRequest.objects.filter(
        wallet=wallet, requested_at__gte=since,
    ).exclude(status='rejected').values_list('amount', flat=True))
    if already + amount > caps['daily_max']:
        raise PayoutError(
            'That is over the daily payout limit of %d VENT COINS. You have '
            'asked for %d in the last day.' % (caps['daily_max'], already),
            'OVER_DAILY_LIMIT', daily_max=caps['daily_max'], already=already)


# ---------------------------------------------------------------------------
# What a payout is going to, in one sentence
# ---------------------------------------------------------------------------

def describe_destination(request_row):
    """One line naming where the money is going, for a statement or a console.

    One function, so the statement line, the admin queue, the email and the
    notification cannot end up describing the same payout three ways.
    """
    if request_row.method == WithdrawalRequest.METHOD_USDT:
        addr = request_row.payout_address
        label = dict(PayoutAddress.NETWORK_CHOICES).get(
            addr.network, addr.network) if addr else 'USDT'
        return '%s to %s' % (label, addr.short if addr else 'an address')
    tail = (request_row.account_number or '')[-4:]
    return 'Withdrawal to %s %s' % (request_row.bank_name, tail)
