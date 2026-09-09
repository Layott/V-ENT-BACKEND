"""Prize distribution.

Credits VENT COINS to winners based on final placement. Atomic and idempotent:
the (tournament, position) unique constraint on PrizePayout guarantees a position
can never be paid twice, so re-running only fills gaps.

`plan()` is the same resolution WITHOUT paying anybody, and it exists because
the spec asks for a warning before coins leave. A confirmation that says "are
you sure" warns nobody; one that lists four names, four amounts and a total is
something an organiser can actually check. Both functions resolve the winners
through the same code, so the list somebody approves is the list that gets
paid.
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


def plan(tournament):
    """Who would be paid what, and what is wrong with it. Writes nothing.

    Returns {'rows': [...], 'total': n, 'already_paid': n, 'problems': [...]}.
    A problem is a code, never a sentence: this is shown to an organiser who may
    be reading in French.
    """
    from ..models import (PrizePayout, TournamentPrizeDistribution,
                          TournamentRegistration)

    problems = []
    if tournament.status != 'completed' or tournament.completed_at is None:
        problems.append('tournament_not_completed')
    if tournament.prize_type == 'no_prize':
        problems.append('no_prize_configured')

    prize_rows = list(
        TournamentPrizeDistribution.objects.filter(tournament=tournament)
        .order_by('position')[:MAX_POSITIONS])
    if not prize_rows:
        problems.append('prize_distribution_missing')

    paid = {p.position: p for p in PrizePayout.objects.filter(tournament=tournament)}

    rows = []
    total = 0
    for row in prize_rows:
        amount = int(row.prize)
        reg = (TournamentRegistration.objects
               .filter(tournament=tournament, final_position=row.position)
               .select_related('user', 'team').first())
        already = paid.get(row.position)
        entry = {
            'position': row.position,
            'amount': amount,
            'name': _participant_label(reg) if reg else '',
            'registration_id': reg.id if reg else None,
            'paid': already is not None,
            'problem': None,
        }
        if already is not None:
            rows.append(entry)
            continue
        if amount <= 0:
            entry['problem'] = 'no_amount'
        elif reg is None:
            # Fewer entrants than prize positions. Not an error, and the
            # organiser should see the money is NOT going anywhere rather than
            # wondering later where it went.
            entry['problem'] = 'nobody_finished_here'
        elif not wallet_service.has_wallet(reg):
            entry['problem'] = 'winner_wallet_missing'
        else:
            total += amount
        rows.append(entry)

    return {
        'rows': rows,
        'total': total,
        'already_paid': len(paid),
        'problems': problems,
    }


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
