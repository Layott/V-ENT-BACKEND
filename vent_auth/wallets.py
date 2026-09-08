"""Money moving between wallets, in one place.

CEO, 7 September 2026: "Teams should have their own wallets and organizations
should also have their own wallets", and from the VENT WALLET spec: send to
users, teams and organisations; a team leader sends to members or to the
organisation; an organisation admin sends to teams or members.

That is nine directions if you write them out, and nine copies of "take from
one, give to the other, write both lines" is nine chances for one of them to
forget a line. So there is ONE function, and the nine directions are just which
two wallets are handed to it.

## The rules it is built to

1. **A balance is never edited without a transaction beside it.** Both are
   written inside the same `transaction.atomic()`, so a statement that does not
   add up to the balance is not a state this can produce. The event ledger is
   built to the same rule and for the same reason.

2. **The row is locked before it is read.** `select_for_update` on both wallets,
   ordered by a stable key so two simultaneous transfers between the same pair
   cannot deadlock. Reading a balance and then writing it back is how money is
   created out of nothing under load.

3. **A refusal happens BEFORE anything moves.** Every check - the amount, the
   funds, the PIN - runs before the first write. The vendor checkout had this
   the other way round in early September and refused with "Nothing has been
   taken from your wallet" after taking it.

4. **Nobody spends what they do not control.** This module does not decide who
   may spend a team's money; `may_spend` in `permissions.py` does, and every
   caller asks it first. Keeping the two apart is what stops a new endpoint
   quietly getting a different answer.
"""
from django.contrib.auth.hashers import check_password
from django.db import transaction as db_transaction

from .models import OrgWallet, TeamWallet, Transaction, UserWallet


class WalletError(Exception):
    """A refusal, carrying the code the frontend translates."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


#: How each kind of wallet is addressed on a Transaction row.
_COLUMN = {
    UserWallet: 'wallet',
    TeamWallet: 'team_wallet',
    OrgWallet: 'org_wallet',
}


def column_for(wallet):
    """Which Transaction column this wallet fills."""
    for model, name in _COLUMN.items():
        if isinstance(wallet, model):
            return name
    raise WalletError('That is not a wallet.', 'NOT_A_WALLET')


def _key(wallet):
    """A stable ordering key, so two transfers cannot deadlock on each other.

    Two people sending in opposite directions between the same pair will lock
    the same two rows; locking them in a consistent order means one waits
    rather than both waiting for each other.
    """
    return (column_for(wallet), str(wallet.pk))


def _lock(wallet):
    return type(wallet).objects.select_for_update().get(pk=wallet.pk)


def describe(wallet):
    """What to call this wallet on somebody else's statement."""
    if isinstance(wallet, UserWallet):
        return '@%s' % wallet.user.username
    if isinstance(wallet, TeamWallet):
        return wallet.team.team_name
    if isinstance(wallet, OrgWallet):
        return wallet.org.org_name
    return 'a wallet'


def check_pin(wallet, pin):
    """Raise unless the PIN is right. Every spend goes through here.

    A wallet with no PIN set cannot spend at all. That is deliberate: the
    alternative is a wallet anybody who reaches the endpoint can empty, and a
    team wallet is reachable by everybody in the team.
    """
    if not wallet.pin_hash:
        raise WalletError('Set a wallet PIN before sending anything.',
                          'PIN_REQUIRED')
    if not pin or not check_password(str(pin), wallet.pin_hash):
        raise WalletError('Incorrect wallet PIN.', 'INVALID_PIN')


def transfer(source, target, amount, *, note='', kind='transfer', pin=None):
    """Move coins from one wallet to another. Returns (debit, credit).

    `source` and `target` are any of UserWallet, TeamWallet or OrgWallet, in
    any combination, which is what makes the nine directions one function.
    """
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise WalletError('Say how much to send.', 'VALIDATION_ERROR')
    if amount <= 0:
        raise WalletError('The amount has to be more than nothing.',
                          'VALIDATION_ERROR')
    if _key(source) == _key(target):
        raise WalletError('That is the same wallet.', 'SAME_WALLET')

    if pin is not None:
        check_pin(source, pin)

    with db_transaction.atomic():
        # Locked in a stable order. See `_key`.
        first, second = sorted((source, target), key=_key)
        locked = {_key(first): _lock(first), _key(second): _lock(second)}
        src = locked[_key(source)]
        dst = locked[_key(target)]

        # Read AFTER the lock, checked BEFORE the write. A balance read before
        # the lock is a balance that may already have been spent.
        if src.wallet_balance < amount:
            raise WalletError(
                'There is not enough in that wallet: %d VC available.'
                % src.wallet_balance, 'INSUFFICIENT_BALANCE')

        src.wallet_balance -= amount
        dst.wallet_balance += amount
        src.save(update_fields=['wallet_balance'])
        dst.save(update_fields=['wallet_balance'])

        # Both lines, in the same transaction as the balances. A statement
        # that does not add up to the balance is not a state this can reach.
        debit = Transaction.objects.create(
            **{column_for(src): src},
            type=kind, amount=-amount, status='completed',
            description=note or ('To %s' % describe(dst)))
        credit = Transaction.objects.create(
            **{column_for(dst): dst},
            type=kind, amount=amount, status='completed',
            description=note or ('From %s' % describe(src)))
        return debit, credit


def statement(wallet, limit=100):
    """This wallet's lines, newest first. One shape for all three kinds."""
    rows = Transaction.objects.filter(
        **{column_for(wallet): wallet}).order_by('-created_at')[:limit]
    return [{
        'id': row.id,
        'type': row.type,
        'amount': row.amount,
        'description': row.description,
        'status': row.status,
        'at': row.created_at.isoformat(),
    } for row in rows]


def wallet_for_team(team, create=True):
    if not create:
        return TeamWallet.objects.filter(team=team).first()
    wallet, _ = TeamWallet.objects.get_or_create(
        team=team, defaults={'team_wallet_id': ('t%s' % team.pk)[:10]})
    return wallet


def wallet_for_org(org, create=True):
    if not create:
        return OrgWallet.objects.filter(org=org).first()
    wallet, _ = OrgWallet.objects.get_or_create(
        org=org, defaults={'org_wallet_id': ('o%s' % org.pk)[:10]})
    return wallet
