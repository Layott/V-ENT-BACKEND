"""Wallet mutation helpers for the tournament lifecycle.

CONSOLIDATION FLAG: vent_auth owns the wallet models but exposes no reusable
credit/debit service - `join_tournament` and the admin refund path each inline
their own logic. These helpers centralize that pattern *inside vent_tournament*
so bracket/prize/refund flows stay atomic and race-safe (select_for_update).
When vent_auth grows a canonical wallet service (and a `Transaction.team_wallet`
FK for true team payouts), migrate callers there and delete this module.

All functions here MUST be called inside a `transaction.atomic()` block so the
`select_for_update()` row lock is held.
"""
from vent_auth.models import UserWallet, Transaction


def _owner_user_id(registration):
    """Resolve the user whose wallet receives a registration's credit/refund.

    Solo registration -> the registrant. Team registration -> the team owner.
    (M1 fallback: `Transaction` only links to `UserWallet`; crediting a
    `TeamWallet` with an auditable Transaction needs a vent_auth schema change -
    flagged for consolidation.)
    """
    # Through `acting_user`, which knows all three kinds of side. This branch
    # knew two, so a squad's entry fee could never be refunded: it answered
    # None and the refund silently went nowhere.
    person = registration.acting_user
    return person.user_id if person is not None else None


def lock_wallet_for_registration(registration):
    """Return a single row-locked UserWallet for a registration, or None.

    For multi-wallet flows use `lock_wallets_for_registrations` so locks are
    acquired in PK order (deadlock avoidance).
    """
    user_id = _owner_user_id(registration)
    if user_id is None:
        return None
    return UserWallet.objects.select_for_update().filter(user_id=user_id).first()


def lock_wallets_for_registrations(registrations):
    """Row-lock every recipient wallet for these registrations, acquiring locks
    in ascending wallet-PK order to match the platform-wide convention and avoid
    deadlocks against concurrent wallet mutations.

    Must run inside a `transaction.atomic()` block. Returns {user_id: UserWallet}.
    """
    user_ids = {uid for uid in (_owner_user_id(r) for r in registrations) if uid}
    if not user_ids:
        return {}
    # Resolve the wallet PKs, then lock them one-by-one in sorted PK order so the
    # lock-acquisition order is identical for every caller.
    pks = sorted(
        UserWallet.objects.filter(user_id__in=user_ids).values_list('pk', flat=True)
    )
    locked = {}
    for pk in pks:
        wallet = UserWallet.objects.select_for_update().get(pk=pk)
        locked[wallet.user_id] = wallet
    return locked


def wallet_for_registration(registration, locked_map):
    """Look up an already-locked wallet (from `lock_wallets_for_registrations`)."""
    user_id = _owner_user_id(registration)
    if user_id is None:
        return None
    return locked_map.get(user_id)


def credit(wallet, amount, *, tx_type, description, tournament=None, reference=None):
    """Credit `amount` (positive VC) to an already-locked wallet + write a Transaction."""
    if amount <= 0:
        raise ValueError('credit amount must be positive')
    wallet.wallet_balance += amount
    wallet.save(update_fields=['wallet_balance'])
    # Transaction.reference is unique - leave it NULL (not '') when there is none
    # so repeated internal transactions don't collide on the empty string.
    return Transaction.objects.create(
        wallet=wallet,
        type=tx_type,
        amount=amount,
        description=description,
        status='completed',
        tournament=tournament,
        reference=reference or None,
    )


def debit(wallet, amount, *, tx_type, description, tournament=None, reference=None):
    """Debit `amount` (positive VC) from an already-locked wallet + write a Transaction.

    Records the Transaction with a negative amount (matching existing convention).
    Caller is responsible for the balance sufficiency check.
    """
    if amount <= 0:
        raise ValueError('debit amount must be positive')
    wallet.wallet_balance -= amount
    wallet.save(update_fields=['wallet_balance'])
    return Transaction.objects.create(
        wallet=wallet,
        type=tx_type,
        amount=-amount,
        description=description,
        status='completed',
        tournament=tournament,
        reference=reference or None,
    )
