"""Game sessions: a match played in a studio's lobby, and its result (P6d).

Design section 14, item 3: "when a match needs a lobby, V-ENT calls the
studio's create game session webhook with the players, teams, settings and an
idempotency key; the studio answers with a session id and join details; the
result is posted back or polled; a crashed game is replayed inside the same
series." Item 4: "Results: validated against the schema, scored by the
expression with a safe evaluator ... The organiser keeps the final say."

Challengermode's game handling (research file, section 7.4) is the model: the
studio never needs to know the competition, only one game session at a time.

How a match runs:

    sweep()      finds ready matches in connected tournaments and asks for the
                 next game's lobby (create), replays a crashed game as the next
                 attempt of the same game number, cancels sessions whose match
                 was settled another way, and asks a silent studio for news
    update()     the one place a session changes state, used by the studio's
                 signed post, by the answer to our create call and by a poll
    feed()       when the series is decided, the score goes to the match through
                 `vent_tournament.results.decide` and `apply`, the doors every
                 other result uses; anything those refuse waits for the organiser

Nothing here overwrites what a person did: a match already settled by the
organiser makes the session `superseded`, and a room code the players typed is
left alone.
"""
import json
import logging
import os
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import studio_delivery, studio_schema, studio_scoring, studio_usage
from .studio_scoring import ExpressionError

logger = logging.getLogger(__name__)

#: How far ahead of its scheduled time a match gets its lobby.
LEAD = timedelta(minutes=5)
#: Attempts at one game before it goes to the organiser.
MAX_ATTEMPTS = 3
#: A started game with no news for this long is asked about...
POLL_AFTER = timedelta(minutes=5)
#: ...at most this often.
POLL_EVERY = timedelta(minutes=2)
#: A studio that answers "still creating" is asked again after this.
CREATE_AGAIN = timedelta(seconds=15)
#: Create calls for one session before it goes to the organiser.
MAX_CREATE_CALLS = 20
#: Largest join details kept (a lobby code, a password, an address).
MAX_JOIN_CHARS = 2000

OPEN_MATCH = ('scheduled', 'in_progress')
OPEN_STATES = ('creating', 'created', 'starting', 'started')
STATES = ('creating', 'created', 'starting', 'started', 'finished', 'cancelling', 'cancelled')
MOVES = {
    'creating': {'created', 'starting', 'started', 'finished', 'cancelled'},
    'created': {'starting', 'started', 'finished', 'cancelled'},
    'starting': {'started', 'finished', 'cancelled'},
    'started': {'finished', 'cancelled'},
    'cancelling': {'cancelled'},
    'finished': set(),
    'cancelled': set(),
}


class SessionRefused(Exception):
    def __init__(self, code, http=400, data=None):
        super().__init__(code)
        self.code = code
        self.http = http
        self.data = data


# ---------------------------------------------------------------------------
# Which matches, which games
# ---------------------------------------------------------------------------

def is_ready(match, now=None):
    """Whether a match should have a lobby now."""
    now = now or timezone.now()
    if match.participant_1_id is None or match.participant_2_id is None:
        return False
    if match.status not in OPEN_MATCH:
        return False
    if match.tournament.is_draft:
        return False
    if match.check_in_deadline and not (match.checked_in_p1_at and match.checked_in_p2_at):
        return False
    if match.scheduled_at and now < match.scheduled_at - LEAD:
        return False
    return True


def game_may_run(game, tournament):
    """A live game may run any tournament of its title; a sandbox game only
    tournaments its own studio's account runs."""
    if game.is_live:
        return True
    from vent_tournament.access import may_manage
    return may_manage(game.partner.owner, tournament)


def available_games(user, tournament):
    """Games an organiser may connect to this tournament."""
    from .models import StudioGame

    games = (StudioGame.objects.filter(game_id=tournament.tournament_game_id)
             .select_related('partner', 'game').order_by('name'))
    return [g for g in games if g.is_live or (user is not None and g.partner.owner_id == user.user_id)]


def lineups(match, game):
    """Both sides as the studio needs them: who plays, and who they are in the game."""
    links = {l.user_id: l for l in game.account_links.all()}
    out = []
    for side, reg in ((1, match.participant_1), (2, match.participant_2)):
        players = []
        for person in (reg.people if reg is not None else []):
            link = links.get(person.user_id)
            players.append({
                'username': person.username,
                'account_id': link.external_id if link else None,
                'display_name': (link.external_name or None) if link else None,
                'linked': link is not None,
            })
        out.append({'side': side, 'name': reg.entrant_name if reg is not None else None,
                    'players': players})
    return out


def match_ref(match):
    return {'round': match.round_number, 'side': match.bracket_side,
            'number': match.match_number, 'group': match.group_number,
            'best_of': match.best_of or 1, 'legs': match.legs or 1}


def _callback(session):
    base = os.environ.get('BACKEND_PUBLIC_URL', 'https://api.v-ent.co').rstrip('/')
    return '%s/api/v1/studio/sessions/%s/' % (base, session.ref)


# ---------------------------------------------------------------------------
# Asking for a lobby, and cancelling one
# ---------------------------------------------------------------------------

def create(link, match, game_number, attempt):
    """A new session for one game of a match, and the call asking for its lobby."""
    from .models import StudioSession

    session = StudioSession.objects.create(game=link.game, link=link, match=match,
                                           game_number=game_number, attempt=attempt)
    sides = lineups(match, link.game)
    payload = {
        'session': session.ref,
        'idempotency_key': session.idempotency_key,
        'game_number': game_number,
        'attempt': attempt,
        'tournament': {'slug': match.tournament.slug, 'title': match.tournament.tournament_title},
        'match': match_ref(match),
        'settings': link.settings or {},
        'lineups': sides,
        'callback': _callback(session),
    }
    linked = [link_.user_id for link_ in link.game.account_links.filter(
        user__in=[p.user_id for reg in (match.participant_1, match.participant_2) if reg
                  for p in reg.people])]
    studio_usage.count_players(link.game, linked)
    delivery = studio_delivery.queue(link.game, 'create_game_session', payload, session=session)
    if delivery is not None:
        studio_delivery.kick([delivery.ref])
    return session


def cancel(session, reason):
    """Tell the studio a lobby is no longer wanted. Never a finished game."""
    from .models import StudioSession

    if session.state not in OPEN_STATES:
        return session
    StudioSession.objects.filter(pk=session.pk).update(state='cancelling', updated_at=timezone.now())
    session.state = 'cancelling'
    payload = {'session': session.ref, 'session_id': session.studio_session_id or None,
               'idempotency_key': session.idempotency_key, 'reason': reason}
    delivery = studio_delivery.queue(session.game, 'cancel_game_session', payload, session=session)
    if delivery is None:
        StudioSession.objects.filter(pk=session.pk).update(state='cancelled')
        session.state = 'cancelled'
    else:
        studio_delivery.kick([delivery.ref])
    return session


def _flag(session, code):
    from .models import StudioSession

    StudioSession.objects.filter(pk=session.pk).update(outcome='needs_organiser', error_code=code,
                                                       updated_at=timezone.now())
    session.outcome, session.error_code = 'needs_organiser', code


# ---------------------------------------------------------------------------
# A session changes state
# ---------------------------------------------------------------------------

def _read_join(join):
    if join is None:
        return None
    if not isinstance(join, dict):
        raise SessionRefused('JOIN_INVALID')
    try:
        size = len(json.dumps(join))
    except (TypeError, ValueError):
        raise SessionRefused('JOIN_INVALID') from None
    if size > MAX_JOIN_CHARS:
        raise SessionRefused('JOIN_INVALID')
    return join


def update(session, body):
    """Move a session as the studio says, checking every step. Returns the session.

    Raises SessionRefused with a code: STATE_UNKNOWN, STATE_NOT_ALLOWED,
    SESSION_FINISHED (a different result after the game finished),
    RESULT_REQUIRED, STUDIO_RESULT_INVALID (with the problems), JOIN_INVALID.
    Posting the same finished result again is accepted and changes nothing.
    """
    from .models import StudioSession

    if not isinstance(body, dict):
        raise SessionRefused('STATE_UNKNOWN')
    state = body.get('state')
    if state not in STATES:
        raise SessionRefused('STATE_UNKNOWN')
    studio_id = body.get('session_id')
    if studio_id is not None and (not isinstance(studio_id, (str, int)) or len(str(studio_id)) > 190):
        raise SessionRefused('SESSION_ID_INVALID')
    join = _read_join(body.get('join'))
    result = body.get('result')
    now = timezone.now()

    with transaction.atomic():
        current = (StudioSession.objects.select_for_update()
                   .select_related('game', 'link').get(pk=session.pk))
        if current.state == 'finished':
            if state == 'finished' and result == current.result:
                return current
            raise SessionRefused('SESSION_FINISHED', 409)
        if state != current.state and state not in MOVES[current.state]:
            raise SessionRefused('STATE_NOT_ALLOWED', 409)
        if state == 'finished':
            if result is None:
                raise SessionRefused('RESULT_REQUIRED')
            problems = (studio_schema.envelope_problems(result)
                        or studio_schema.validate(result, current.game.result_schema or {}))
            if problems:
                raise SessionRefused('STUDIO_RESULT_INVALID', 422, {'errors': problems})

        current.state = state
        current.last_seen_at = now
        if studio_id is not None:
            current.studio_session_id = str(studio_id)
        if join is not None:
            current.join = join
        if state == 'finished':
            current.result = result
            current.finished_at = now
            try:
                p1, p2 = studio_scoring.score_sides(current.game.scoring_expression, result,
                                                    current.link.settings or {})
                current.scores = {'p1': p1, 'p2': p2}
            except ExpressionError as exc:
                current.outcome, current.error_code = 'needs_organiser', exc.code
        current.save()

    if join and state in ('created', 'starting', 'started'):
        _set_room(current)
    if current.state == 'finished' and current.scores and not current.outcome:
        feed(current)
        current.refresh_from_db()
    return current


def _set_room(session):
    """Put the studio's lobby code on the match, where the match room already
    shows it, unless somebody typed their own."""
    from vent_tournament.models import BracketMatch
    from .models import StudioSession

    code = str((session.join or {}).get('room_code') or '')[:64]
    if not code:
        return
    password = str((session.join or {}).get('room_password') or '')[:64]
    ours = {str((s.join or {}).get('room_code') or '') for s in
            StudioSession.objects.filter(match_id=session.match_id).exclude(pk=session.pk)}
    with transaction.atomic():
        match = BracketMatch.objects.select_for_update().get(pk=session.match_id)
        if match.status not in OPEN_MATCH:
            return
        if match.room_code and match.room_code not in ours and match.room_code != code:
            return
        if (match.room_code, match.room_password) == (code, password):
            return
        match.room_code, match.room_password = code, password
        match.save(update_fields=['room_code', 'room_password'])
    try:
        from vent_auth.views_notifications import create_notification
        for reg in (match.participant_1, match.participant_2):
            person = reg.acting_user if reg is not None else None
            if person is not None:
                create_notification(
                    person.user_id, 'match', 'Your match room is ready',
                    body=match.tournament.tournament_title,
                    link='/tournaments/%s' % (match.tournament.slug or match.tournament_id),
                    metadata={'match_id': match.id})
    except Exception:                                             # noqa: BLE001
        logger.warning('studio room notification failed', exc_info=True)


# ---------------------------------------------------------------------------
# The series, and the result on the match
# ---------------------------------------------------------------------------

def finished_games(sessions):
    """{game number: the last finished, scored attempt of it}."""
    out = {}
    for session in sessions:
        if session.state == 'finished' and session.scores and session.outcome != 'superseded':
            out[session.game_number] = session
    return out


def series_outcome(match, finished):
    """(score 1, score 2, games) once the series is decided, else None."""
    games = [{'p1': finished[n].scores['p1'], 'p2': finished[n].scores['p2']}
             for n in sorted(finished)]
    best_of = match.best_of or 1
    legs = match.legs or 1
    if legs > 1:
        if len(games) < legs:
            return None
        return sum(g['p1'] for g in games), sum(g['p2'] for g in games), games
    if best_of <= 1:
        if not games:
            return None
        return games[0]['p1'], games[0]['p2'], games[:1]
    wins1 = sum(1 for g in games if g['p1'] > g['p2'])
    wins2 = sum(1 for g in games if g['p2'] > g['p1'])
    needed = best_of // 2 + 1
    if max(wins1, wins2) >= needed or len(games) >= best_of:
        return wins1, wins2, games
    return None


def feed(session):
    """Record the series on the match if it is decided. Returns what happened:
    'applied', 'waiting', 'superseded' or 'needs_organiser'."""
    from vent_tournament import results
    from vent_tournament.models import BracketMatch
    from .models import StudioSession

    with transaction.atomic():
        match = BracketMatch.objects.select_for_update().get(pk=session.match_id)
        if match.status not in OPEN_MATCH:
            StudioSession.objects.filter(pk=session.pk).update(outcome='superseded')
            return 'superseded'
        sessions = list(StudioSession.objects.filter(match=match, game=session.game)
                        .order_by('game_number', 'attempt'))
        finished = finished_games(sessions)
        decided = series_outcome(match, finished)
        if decided is None:
            return 'waiting'
        score_1, score_2, games = decided
        try:
            decision = results.decide(match, score_1, score_2, games=games)
            results.apply(match, decision)
        except results.ResultError as exc:
            StudioSession.objects.filter(pk=session.pk).update(outcome='needs_organiser',
                                                               error_code=exc.code)
            return 'needs_organiser'
        StudioSession.objects.filter(pk__in=[s.pk for s in finished.values()]).update(outcome='applied')
    return 'applied'


# ---------------------------------------------------------------------------
# What the studio answered our calls with
# ---------------------------------------------------------------------------

def read_answer(delivery, answer):
    """Called by studio_delivery when a session call was delivered."""
    from .models import StudioSession

    session = delivery.session
    if session is None:
        return
    if delivery.kind == 'cancel_game_session':
        StudioSession.objects.filter(pk=session.pk, state='cancelling').update(
            state='cancelled', updated_at=timezone.now())
        return
    if isinstance(answer, dict) and answer.get('state'):
        try:
            update(session, answer)
        except SessionRefused as exc:
            StudioSession.objects.filter(pk=session.pk).update(error_code=exc.code)
    if delivery.kind == 'create_game_session':
        session.refresh_from_db()
        if session.state == 'creating':
            _ask_again(session, delivery)


def _ask_again(session, delivery):
    """The studio is still making the lobby (or said nothing useful): ask again
    shortly, as Challengermode does, until it answers created or later."""
    calls = session.deliveries.filter(kind='create_game_session').count()
    if calls >= MAX_CREATE_CALLS:
        _flag(session, 'STUDIO_SESSION_NOT_CREATED')
        return
    again = studio_delivery.queue(session.game, 'create_game_session', delivery.payload,
                                  session=session)
    if again is not None:
        again.next_attempt_at = timezone.now() + CREATE_AGAIN
        again.save(update_fields=['next_attempt_at'])


# ---------------------------------------------------------------------------
# The cron pass
# ---------------------------------------------------------------------------

def _advance(link, match):
    """Open the next lobby a match needs, if any. Returns what was done."""
    from .models import StudioSession

    sessions = list(StudioSession.objects.filter(match=match, game=link.game)
                    .order_by('game_number', 'attempt'))
    if any(s.outcome == 'needs_organiser' for s in sessions):
        return None
    if any(s.state in OPEN_STATES or s.state == 'cancelling' for s in sessions):
        return None
    finished = finished_games(sessions)
    if series_outcome(match, finished) is not None:
        return None
    number = len(finished) + 1
    tries = [s for s in sessions if s.game_number == number]
    if not tries:
        create(link, match, number, 1)
        return 'opened'
    last = tries[-1]
    if last.state == 'cancelled' and not last.result:
        if last.attempt >= MAX_ATTEMPTS:
            _flag(last, 'STUDIO_SESSION_GAVE_UP')
            return 'flagged'
        create(link, match, number, last.attempt + 1)
        return 'replayed'
    return None


def sweep(now=None):
    """One pass over every connected tournament. Returns counts by what happened."""
    from vent_tournament.models import BracketMatch
    from .models import StudioSession, StudioTournamentLink

    now = now or timezone.now()
    summary = {'opened': 0, 'replayed': 0, 'flagged': 0, 'cancelled': 0, 'asked': 0}

    links = (StudioTournamentLink.objects.filter(active=True)
             .select_related('game', 'game__partner', 'game__partner__owner', 'tournament'))
    for link in links:
        if not game_may_run(link.game, link.tournament):
            continue
        matches = (BracketMatch.objects
                   .filter(tournament=link.tournament, status__in=OPEN_MATCH,
                           participant_1__isnull=False, participant_2__isnull=False)
                   .select_related('tournament', 'participant_1', 'participant_2'))
        for match in matches:
            if not is_ready(match, now):
                continue
            try:
                done = _advance(link, match)
            except Exception:
                logger.warning('studio session for match %s failed', match.pk, exc_info=True)
                continue
            if done:
                summary[done] += 1

    for session in (StudioSession.objects.filter(state__in=OPEN_STATES)
                    .select_related('match', 'link', 'link__tournament', 'game', 'game__partner')):
        if (session.match.status not in OPEN_MATCH or not session.link.active
                or not game_may_run(session.game, session.link.tournament)):
            cancel(session, 'match_closed' if session.match.status not in OPEN_MATCH else 'disconnected')
            summary['cancelled'] += 1

    for session in StudioSession.objects.filter(state='creating', outcome=''):
        calls = session.deliveries.filter(kind='create_game_session')
        if not calls.filter(status__in=('pending', 'delivered')).exists():
            _flag(session, 'STUDIO_UNREACHABLE')
            summary['flagged'] += 1

    silent = (StudioSession.objects
              .filter(state__in=('created', 'starting', 'started'), outcome='',
                      last_seen_at__lt=now - POLL_AFTER)
              .select_related('game'))
    for session in silent:
        if session.deliveries.filter(kind='get_game_session',
                                     created_at__gte=now - POLL_EVERY).exists():
            continue
        delivery = studio_delivery.queue(
            session.game, 'get_game_session',
            {'session': session.ref, 'session_id': session.studio_session_id or None,
             'idempotency_key': session.idempotency_key},
            session=session)
        if delivery is not None:
            studio_delivery.kick([delivery.ref])
            summary['asked'] += 1
    return summary


def claim_nonce(game, nonce):
    """True the first time a studio uses a request id, False after."""
    from .models import StudioNonce

    try:
        with transaction.atomic():
            StudioNonce.objects.create(game=game, nonce=str(nonce)[:64])
    except IntegrityError:
        return False
    return True


def clear_nonces(now=None, keep=timedelta(minutes=15)):
    from .models import StudioNonce

    cutoff = (now or timezone.now()) - keep
    deleted, _ = StudioNonce.objects.filter(seen_at__lt=cutoff).delete()
    return deleted


def session_row(session):
    """A session as the studio and the organiser read it."""
    match = session.match
    return {
        'ref': session.ref,
        'state': session.state,
        'game_number': session.game_number,
        'attempt': session.attempt,
        'idempotency_key': session.idempotency_key,
        'session_id': session.studio_session_id or None,
        'join': session.join or {},
        'scores': session.scores,
        'outcome': session.outcome or None,
        'error_code': session.error_code or None,
        'match': {'round': match.round_number, 'side': match.bracket_side,
                  'number': match.match_number, 'group': match.group_number},
        'tournament': {'slug': match.tournament.slug, 'title': match.tournament.tournament_title},
        'updated_at': session.updated_at,
    }
