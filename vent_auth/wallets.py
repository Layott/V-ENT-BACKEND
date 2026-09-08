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


def transfer(source, target, amount, *, note='', kind='transfer', pin=None,
             debit_kind=None, credit_kind=None,
             debit_note='', credit_note=''):
    """Move coins from one wallet to another. Returns (debit, credit).

    `source` and `target` are any of UserWallet, TeamWallet or OrgWallet, in
    any combination, which is what makes the nine directions one function.

    `debit_kind` and `credit_kind` exist because one move can be two different
    words on the two statements. A person sending to another person has always
    read as `send` on one side and `receive` on the other, and renaming both to
    `transfer` would rewrite the meaning of every row already written. Money
    between a person and a team is a `transfer` on both sides, because neither
    of the other two words is true of it.
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
            type=debit_kind or kind, amount=-amount, status='completed',
            description=debit_note or note or ('To %s' % describe(dst)))
        credit = Transaction.objects.create(
            **{column_for(dst): dst},
            type=credit_kind or kind, amount=amount, status='completed',
            description=credit_note or note or ('From %s' % describe(src)))
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


def wallet_for_user(user, create=False):
    if not create:
        return UserWallet.objects.filter(user=user).first()
    from .views_helpers import get_or_create_user_wallet
    return get_or_create_user_wallet(user)


# ---------------------------------------------------------------------------
# Who the money is going to
# ---------------------------------------------------------------------------

def resolve_target(kind, ref):
    """The wallet a `{to_kind, to}` pair names. Raises WalletError if it cannot.

    Named explicitly rather than guessed from the string, because "vermillion"
    could be a username, a team or an organisation, and guessing wrong sends
    somebody's money to a stranger with the same name.

    One resolver, used by the person's own wallet and by the team and
    organisation wallets alike. Two copies would eventually disagree about what
    "vermillion" means, which is the same fault seen from the inside.
    """
    kind = str(kind or '').strip().lower()
    ref = str(ref or '').strip()
    if not kind or not ref:
        raise WalletError('Say who it is going to.', 'VALIDATION_ERROR')

    if kind == 'user':
        from .invites import invitee_for
        user, _email, problem = invitee_for(ref)
        if problem or user is None:
            raise WalletError('No account called %s.' % ref, 'NOT_FOUND')
        wallet = UserWallet.objects.filter(user=user).first()
        if wallet is None:
            # Created rather than refused: a wallet is made at signup, and an
            # account old enough to predate that should still be payable.
            wallet = wallet_for_user(user, create=True)
        return wallet

    if kind == 'team':
        from .models import Teams
        team = (Teams.objects.filter(slug=ref).first()
                or Teams.objects.filter(team_name__iexact=ref).first())
        if team is None:
            raise WalletError('No team called %s.' % ref, 'NOT_FOUND')
        return wallet_for_team(team)

    if kind == 'org':
        from .models import Organization
        org = (Organization.objects.filter(slug=ref).first()
               or Organization.objects.filter(org_name__iexact=ref).first())
        if org is None:
            raise WalletError('No organisation called %s.' % ref, 'NOT_FOUND')
        return wallet_for_org(org)

    raise WalletError('Send to a user, a team or an organisation.',
                      'VALIDATION_ERROR')


# ---------------------------------------------------------------------------
# The second factor
# ---------------------------------------------------------------------------

def second_factor_required(user):
    """Whether this person's wallet spends have to produce an authenticator code.

    The spec asks for "two-factor authentication and a PIN for wallet
    transactions". The two are not the same thing and are not interchangeable:
    a PIN is something typed into this site and lives in its database; the code
    comes off a device the site has never seen. Somebody who has set up an
    authenticator has said they want the second one, so their money is held to
    it, on every debit, without a separate switch to forget to turn on.

    It reuses the factor already enrolled at sign-in rather than adding a
    second mechanism, so there is one secret per person and one place a code
    can be spent.
    """
    from .login_2fa import factor_for
    factor = factor_for(user)
    return bool(factor is not None and factor.confirmed)


def check_second_factor(user, code):
    """Raise unless this debit carries a valid code, when one is required.

    Silent when the account has no confirmed factor, so nothing changes for
    somebody who has not enrolled.
    """
    if user is None or not second_factor_required(user):
        return
    if not code:
        raise WalletError(
            'Enter the code from your authenticator app.',
            'TWO_FACTOR_REQUIRED')
    from .login_2fa import spend_code
    ok, _problem = spend_code(user, code)
    if not ok:
        raise WalletError('That code is not right, or it has been used.',
                          'INVALID_CODE')


# ---------------------------------------------------------------------------
# A payout, which is money held before it is money gone
# ---------------------------------------------------------------------------

def hold_for_payout(wallet, amount, description):
    """Take the amount out of the balance and mark it pending. Returns the row.

    The money leaves the spendable balance the moment it is requested, not when
    an admin gets to it. Before this, a payout request touched nothing: the
    balance was debited at approval, so anybody could request their whole
    balance, spend it, and leave the approval to fail on them days later with
    "Insufficient wallet balance". Holding is what makes the number on the
    screen a number somebody can rely on.

    The caller has already checked the PIN, the second factor and KYC. This
    function does the money and nothing else.
    """
    amount = int(amount)
    with db_transaction.atomic():
        locked = UserWallet.objects.select_for_update().get(pk=wallet.pk)
        if locked.wallet_balance < amount:
            raise WalletError(
                'There is not enough in your wallet: %d VC available.'
                % locked.wallet_balance, 'INSUFFICIENT_BALANCE')
        locked.wallet_balance -= amount
        locked.save(update_fields=['wallet_balance'])
        return Transaction.objects.create(
            wallet=locked, type='withdrawal', amount=-amount,
            status='pending', description=description)


def settle_payout(request_row):
    """The payout was approved and the money is gone. (ok, reason).

    A request made before holds existed carries no hold, and its balance was
    never debited, so it is debited here instead. That fallback is the whole
    reason `WithdrawalRequest.hold` is nullable: without it, approving one of
    those old requests would pay somebody without taking anything.
    """
    with db_transaction.atomic():
        wallet = UserWallet.objects.select_for_update().get(
            pk=request_row.wallet_id)
        held = request_row.hold

        if held is not None and held.status == 'pending':
            held.status = 'completed'
            held.save(update_fields=['status'])
            return True, None

        # No hold: the old shape. Take it now, and refuse rather than pay out
        # of a balance that is no longer there.
        if wallet.wallet_balance < request_row.amount:
            return False, 'Insufficient wallet balance'
        wallet.wallet_balance -= request_row.amount
        wallet.save(update_fields=['wallet_balance'])
        Transaction.objects.create(
            wallet=wallet, type='withdrawal', amount=-request_row.amount,
            status='completed',
            description='Withdrawal to %s %s' % (
                request_row.bank_name, request_row.account_number[-4:]))
        return True, None


def return_payout(request_row, reason=''):
    """The payout was denied. Put the held amount back. (ok, reason).

    The held row is cancelled rather than answered with a second, positive row.
    A refund line beside a debit line that is also still on the statement would
    make the statement sum to more than the balance, and a statement that does
    not add up to the balance is the one thing this module exists to prevent.
    """
    with db_transaction.atomic():
        wallet = UserWallet.objects.select_for_update().get(
            pk=request_row.wallet_id)
        held = request_row.hold
        if held is None or held.status != 'pending':
            # Nothing was held, so there is nothing to give back. An older
            # request, or one already settled.
            return True, None
        wallet.wallet_balance += request_row.amount
        wallet.save(update_fields=['wallet_balance'])
        held.status = 'cancelled'
        held.description = '%s - returned%s' % (
            held.description, (': %s' % reason) if reason else '')
        held.save(update_fields=['status', 'description'])
        return True, None
