"""Prize distribution.

Credits VENT COINS to winners based on final placement. Atomic and idempotent:
the (tournament, position) unique constraint on PrizePayout guarantees a position
can never be paid twice, so re-running only fills gaps.
"""
from django.db import transaction

from . import wallet as wallet_service


class PrizeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# Cap enforced by spec (§6/§7): synchronous payout kept bounded.
MAX_POSITIONS = 16


def distribute(tournament, *, triggered_by=None, auto=False, force_recompute=False):
    """Distribute prizes for a completed tournament. Wraps its own atomic block.

    Returns a list of distribution dicts. Raises PrizeError on precondition fail.
    """
    from ..models import TournamentPrizeDistribution, TournamentRegistration, PrizePayout

    if tournament.status != 'completed' or tournament.completed_at is None:
        raise PrizeError('tournament_not_completed', 'Tournament is not completed yet.')
    if tournament.prize_type == 'no_prize':
        raise PrizeError('no_prize_configured', 'This tournament awards no prizes.')

    prize_rows = list(
        TournamentPrizeDistribution.objects.filter(tournament=tournament).order_by('position')[:MAX_POSITIONS]
    )
    if not prize_rows:
        raise PrizeError('prize_distribution_missing', 'No prize distribution configured.')

    existing = {p.position: p for p in PrizePayout.objects.filter(tournament=tournament)}
    if existing and not force_recompute and len(existing) >= len(prize_rows):
        raise PrizeError('already_distributed', 'Prizes have already been distributed.')

    results = []
    with transaction.atomic():
        # Lock the tournament row so two admins can't race the same payout.
        from ..models import Tournament
        Tournament.objects.select_for_update().get(pk=tournament.pk)

        # Pass 1 - resolve every position that still needs paying.
        targets = []  # (position, reg, amount)
        for row in prize_rows:
            position = row.position
            if position in existing:
                results.append(_result(existing[position]))
                continue
            reg = (
                TournamentRegistration.objects
                .filter(tournament=tournament, final_position=position)
                .select_related('user', 'team')
                .first()
            )
            if reg is None:
                # No competitor finished at this rank (fewer entrants than prize
                # positions) - skip, per spec.
                continue
            amount = int(row.prize)
            if amount <= 0:
                continue
            targets.append((position, reg, amount))

        # Lock all recipient wallets up front in PK order (deadlock avoidance).
        locked_wallets = wallet_service.lock_wallets_for_registrations([t[1] for t in targets])

        # Pass 2 - credit each winner using the already-locked wallet.
        for position, reg, amount in targets:
            wallet = wallet_service.wallet_for_registration(reg, locked_wallets)
            if wallet is None:
                raise PrizeError(
                    'winner_wallet_missing',
                    f'No wallet to credit for position {position} (registration {reg.id}).',
                )
            label = _participant_label(reg)
            tx = wallet_service.credit(
                wallet, amount,
                tx_type='prize',
                description=f'Prize payout - position {position} - {tournament.tournament_title}',
                tournament=tournament,
            )
            payout = PrizePayout.objects.create(
                tournament=tournament,
                winner_registration=reg,
                position=position,
                amount=amount,
                transaction=tx,
                paid_by=triggered_by,
                auto_distributed=auto,
            )
            results.append(_result(payout, label=label))

    return results


def _participant_label(reg):
    # One accessor for all three kinds. A squad used to come back as the empty
    # string, so a prize for Nigeria was recorded against nobody.
    return getattr(reg, 'entrant_name', '') or ''


def _result(payout, label=None):
    return {
        'position': payout.position,
        'winner_registration_id': payout.winner_registration_id,
        'amount': payout.amount,
        'transaction_id': payout.transaction_id,
        'payout_id': payout.id,
        'name': label,
    }
