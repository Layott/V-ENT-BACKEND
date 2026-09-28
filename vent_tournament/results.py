"""Whether a result is a result, and who won it. One answer for every door.

Three doors record a match: the player who confirms the opponent's score, the
organiser or scorekeeper, and an admin overriding. Each had its own idea:

  * confirming refused ANY level score, so a group-stage football draw, which
    is the most ordinary result in the game, could not be confirmed at all
  * the organiser's door and the admin's both demanded a winner, so a draw
    could not be recorded there either
  * the admin's door took any registration id as the winner, including one
    from a different match

So the decision lives here and the doors call it. It reads how the match is
played (fixed on the match when it was drawn, see `stage_settings`) and says:
the winner, or a draw, or a refusal carrying a code and the field it is about.

A level score in a knockout needs a decider. In football that is a penalty
shoot-out, recorded as its own score beside the goals, because "2-2, won 4-3 on
penalties" is two facts and writing 3-2 would be a lie about the goals.
"""
from dataclasses import dataclass


class ResultError(ValueError):
    def __init__(self, code, field=None):
        super().__init__(code)
        self.code = code
        self.field = field


@dataclass
class Decision:
    winner: object            # a TournamentRegistration, or None for a draw
    score_p1: int
    score_p2: int
    penalties_p1: object      # int or None
    penalties_p2: object
    games: list


def _count(value, field):
    if value in (None, ''):
        raise ResultError('SCORE_REQUIRED', field)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ResultError('NOT_A_NUMBER', field)
    if number < 0:
        raise ResultError('NEGATIVE_SCORE', field)
    if number > 999:
        raise ResultError('OUT_OF_RANGE', field)
    return number


def draw_allowed(match):
    """Whether this match may end level.

    Fixed on the match when it was drawn. A match drawn before that column
    existed carries False, so a table format is read from the tournament: a
    round robin has always allowed a draw in principle, and only the door
    refused it.
    """
    if match.draw_allowed:
        return True
    if match.stage_id is None:
        from .services.bracket import decided_by_table, normalize_bracket_type
        return decided_by_table(normalize_bracket_type(match.tournament.bracket_type))
    return False


def _clean_games(raw, best_of, legs):
    """Per-game or per-leg scores, or [] when only the headline was sent."""
    if raw in (None, '', []):
        return []
    if not isinstance(raw, list):
        raise ResultError('NOT_A_LIST', 'games')
    ceiling = max(best_of or 1, legs or 1)
    if len(raw) > ceiling:
        raise ResultError('TOO_MANY_GAMES', 'games')
    out = []
    for index, game in enumerate(raw):
        if not isinstance(game, dict):
            raise ResultError('NOT_A_GAME', 'games')
        out.append({
            'p1': _count(game.get('p1'), 'games.%s.p1' % index),
            'p2': _count(game.get('p2'), 'games.%s.p2' % index),
        })
    return out


def decide(match, score_p1, score_p2, *, penalties_p1=None, penalties_p2=None,
           games=None, winner_id=None):
    """Check a proposed result for `match` and return the Decision.

    `winner_id` is only for the one case nothing on the scoreboard can settle:
    a knockout the organiser has set to 'winner_named' (a coin toss, a golden
    goal the game does not record). Anywhere else it must agree with the
    score, and it must be one of the two sides.
    """
    from . import stage_settings

    if match.participant_1_id is None or match.participant_2_id is None:
        raise ResultError('PARTICIPANTS_NOT_SET')

    s1 = _count(score_p1, 'score_p1')
    s2 = _count(score_p2, 'score_p2')
    legs = getattr(match, 'legs', 1) or 1
    best_of = getattr(match, 'best_of', 1) or 1
    game_rows = _clean_games(games, best_of, legs)

    # When the games were sent, the headline must be what they add up to:
    # games won in a best-of series, goals on aggregate over two legs.
    if game_rows:
        if legs > 1 or best_of == 1:
            sum1 = sum(g['p1'] for g in game_rows)
            sum2 = sum(g['p2'] for g in game_rows)
        else:
            sum1 = sum(1 for g in game_rows if g['p1'] > g['p2'])
            sum2 = sum(1 for g in game_rows if g['p2'] > g['p1'])
        if (sum1, sum2) != (s1, s2):
            raise ResultError('GAMES_DO_NOT_ADD_UP', 'games')

    # A two-game match holds two games: 2-0, 1-1, 1-0 with a draw in the
    # other. Three games won in it is a typing mistake, and it was accepted
    # (found on the walk, 27 September 2026).
    if best_of == 2 and legs == 1 and s1 + s2 > 2:
        raise ResultError('SERIES_OVER_ITS_LENGTH', 'score_p1')

    # A best-of series (not a two-game group match, which may end 1-1) cannot
    # go past the number of wins that ends it.
    if best_of > 2 and legs == 1:
        needed = best_of // 2 + 1
        if s1 > needed or s2 > needed or (s1 == needed and s2 == needed):
            raise ResultError('SERIES_OVER_ITS_LENGTH', 'score_p1')
        if max(s1, s2) < needed and not draw_allowed(match):
            raise ResultError('SERIES_NOT_FINISHED', 'score_p1')

    has_pens = penalties_p1 not in (None, '') or penalties_p2 not in (None, '')
    p1 = p2 = None
    if has_pens:
        p1 = _count(penalties_p1, 'penalties_p1')
        p2 = _count(penalties_p2, 'penalties_p2')

    winner = None
    if s1 != s2:
        if has_pens:
            # Penalties only settle a level match. A shoot-out beside a score
            # that already had a winner is a typing mistake, not a result.
            raise ResultError('PENALTIES_ONLY_WHEN_LEVEL', 'penalties_p1')
        winner = match.participant_1 if s1 > s2 else match.participant_2
    else:
        rule = stage_settings.of_match(match).get('draws')
        if draw_allowed(match) and not has_pens:
            winner = None
        elif has_pens:
            if p1 == p2:
                raise ResultError('PENALTIES_LEVEL', 'penalties_p1')
            winner = match.participant_1 if p1 > p2 else match.participant_2
        elif rule == 'winner_named' and winner_id:
            winner = None   # decided below from winner_id
        else:
            raise ResultError('LEVEL_NEEDS_A_DECIDER', 'penalties_p1')

    if winner_id not in (None, ''):
        try:
            wanted = int(winner_id)
        except (TypeError, ValueError):
            raise ResultError('NOT_A_NUMBER', 'winner_registration_id')
        if wanted not in (match.participant_1_id, match.participant_2_id):
            raise ResultError('WINNER_NOT_IN_MATCH', 'winner_registration_id')
        named = match.participant_1 if wanted == match.participant_1_id else match.participant_2
        if winner is None and s1 == s2 and not has_pens:
            if draw_allowed(match):
                # A draw with a winner named is the organiser contradicting
                # the score. Say so rather than pick one.
                raise ResultError('WINNER_ON_A_DRAW', 'winner_registration_id')
            winner = named
        elif winner is not None and winner.id != wanted:
            raise ResultError('WINNER_DISAGREES_WITH_SCORE', 'winner_registration_id')

    _still_free_to_change(match, winner)

    return Decision(winner=winner, score_p1=s1, score_p2=s2,
                    penalties_p1=p1, penalties_p2=p2, games=game_rows)


PLAYED_STATUSES = ('completed', 'walkover_p1', 'walkover_p2', 'in_progress',
                   'pending_opponent_confirm', 'disputed')


def _still_free_to_change(match, winner):
    """Refuse changing a finished result that something later was built on.

    Found on the second bracket walk, 28 September 2026: a round-one result
    changed after the final was played put the new winner into a semi-final
    the old winner had already won, and left the old winner recorded as that
    semi's winner. A Swiss result changed after the next round was paired
    from it did the same to the pairing. The organiser corrects the later
    match first, then this one; nothing is rewritten underneath a result
    somebody has already played.

    A new result for an unfinished match, or a corrected score that keeps
    the same outcome, is always free.
    """
    if match.status not in ('completed', 'walkover_p1', 'walkover_p2'):
        return
    from . import stage_settings
    from .models import BracketMatch
    stage = match.stage if match.stage_id else None
    closed = stage is not None and stage.status == 'complete'
    new_winner_id = winner.id if winner is not None else None
    if (match.winner_id or None) == new_winner_id:
        # A corrected score, same outcome. Harmless in a knockout; in a table
        # the goals ARE the standings, and a closed stage's table has been
        # read and its qualifiers or final places set from it.
        if closed and (stage_settings.is_table(stage.format)
                       or stage.format in ('swiss', 'gsl')):
            raise ResultError('STAGE_CLOSED')
        return
    later = [pk for pk in (match.winner_to_match_id, match.loser_to_match_id) if pk]
    if later and BracketMatch.objects.filter(pk__in=later, status__in=PLAYED_STATUSES).exists():
        raise ResultError('NEXT_MATCH_ALREADY_PLAYED')
    if (stage is not None and stage.format == 'swiss'
            and stage.matches.filter(round_number__gt=match.round_number).exists()):
        raise ResultError('NEXT_ROUND_ALREADY_DRAWN')
    if closed:
        raise ResultError('STAGE_CLOSED')


def apply(match, decision, *, recorded_by=None, forfeit_reason=''):
    """Write a Decision onto a (locked) match and save it.

    Saving to a terminal status fires the advance cascade.
    """
    from django.utils import timezone

    now = timezone.now()
    match.score_p1 = decision.score_p1
    match.score_p2 = decision.score_p2
    match.penalties_p1 = decision.penalties_p1
    match.penalties_p2 = decision.penalties_p2
    match.games = decision.games
    match.winner = decision.winner
    match.status = 'completed'
    match.completed_at = now
    match.forfeit_reason = forfeit_reason or ''
    fields = ['score_p1', 'score_p2', 'penalties_p1', 'penalties_p2', 'games',
              'winner', 'status', 'completed_at', 'forfeit_reason']
    if recorded_by is not None:
        match.recorded_by = recorded_by
        match.recorded_at = now
        fields += ['recorded_by', 'recorded_at']
    match.save(update_fields=fields)
    _settle_open_disputes(match, recorded_by, now)
    return match


def _settle_open_disputes(match, recorded_by, now):
    """A result recorded by somebody running the tournament settles the open
    disputes on that match. Recording over a dispute IS the organiser's
    decision; leaving the dispute open kept it in the admin queue for good
    (second bracket walk, 28 September 2026). A player confirming their own
    match settles nothing here: that is not a ruling on anybody's dispute."""
    if recorded_by is None or match.participant_owned_by(recorded_by) is not None:
        return
    from .models import TournamentDispute
    TournamentDispute.objects.filter(match=match, status__in=('open', 'under_review')).update(
        status='resolved', resolved_at=now,
        resolution_note='Settled by the result %s recorded: %s.' % (
            recorded_by.username, describe(match)))


def describe(match):
    """The result in words-free parts for a screen: '2-2 (4-3 pens)'."""
    text = '%s-%s' % (match.score_p1, match.score_p2)
    if match.penalties_p1 is not None and match.penalties_p2 is not None:
        text += ' (%s-%s p)' % (match.penalties_p1, match.penalties_p2)
    return text
