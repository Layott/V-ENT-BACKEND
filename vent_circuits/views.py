"""Circuits, the doors (P5). Mounted at /circuits/.

    GET    /circuits/                                  published circuits; ?mine=1 my own, drafts too;
                                                       ?tournament=<ref> the circuit a tournament is in
    POST   /circuits/                                  make one
    GET    /circuits/<ref>/                            one: its ranking, tiers, tournaments, adjustments
    PATCH  /circuits/<ref>/                            change it, publish it
    DELETE /circuits/<ref>/                            delete it; its tournaments stay as they are
    POST   /circuits/<ref>/tiers/                      add a tier
    PATCH  /circuits/<ref>/tiers/<tier>/               rename it, reorder it, change its table
    DELETE /circuits/<ref>/tiers/<tier>/               remove it; its tournaments use the default table
    POST   /circuits/<ref>/tournaments/                count a tournament I also run, at a tier
    PATCH  /circuits/<ref>/tournaments/<tournament>/   move it to another tier
    DELETE /circuits/<ref>/tournaments/<tournament>/   stop counting it
    POST   /circuits/<ref>/adjustments/                give or take points, with a public reason
    DELETE /circuits/<ref>/adjustments/<id>/           take an adjustment back

Reads are public once a circuit is published, with no session asked for. A
draft answers the same 404 as an address that names nothing, to everybody but
its manager (R88). Writes are its manager's (access.py); every refusal carries
a code the screen translates. The ranking itself is standings.py.
"""
import functools
from datetime import date

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vent_auth import inputs, regions
from vent_auth.actors import actor_from_request
from vent_auth.throttle import limited

from . import access, standings, tables
from .models import Circuit, CircuitAdjustment, CircuitTier, CircuitTournament

ENTRANTS = [key for key, _label in Circuit.ENTRANTS]
#: Points from final standings, or Elo from the matches (P5b).
ENGINES = ['points', 'elo']
LIST_LIMIT = 60


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def _ok(data, message='OK', http=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http)


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, field=None, **extra):
    body = {'status': 'error', 'data': extra, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http)


def _table_err(exc, field):
    return _err('That point table cannot be read.', exc.code, field=field, part=exc.part)


def _limit_writes(view):
    """30 writes a minute per caller (R59); a read is never refused."""
    guarded = limited('circuit-write', 30)(view)

    @functools.wraps(view)
    def choose(request, *args, **kwargs):
        if request.method == 'GET':
            return view(request, *args, **kwargs)
        return guarded(request, *args, **kwargs)
    return choose


def _viewer(request):
    """The signed-in viewer on a public read, or None. Never an error."""
    if not (request.headers.get('Authorization') or '').startswith('Bearer '):
        return None
    user, _error = actor_from_request(request)
    return user


def _media(request, path):
    return request.build_absolute_uri(path) if path else None


# ---------------------------------------------------------------------------
# Finding things
# ---------------------------------------------------------------------------

def _find(ref, viewer):
    """(circuit, moved_to_slug, error). A draft the viewer may not read is
    answered exactly like an address that names nothing."""
    from vent_auth.models_slughistory import resolve
    ref = str(ref or '').strip()
    circuit = Circuit.objects.select_related('game', 'owner', 'organization').filter(slug=ref).first()
    if circuit is None and ref:
        moved_id = resolve('circuit', ref)
        current = Circuit.objects.filter(pk=moved_id).first() if moved_id else None
        if current is not None and access.may_read(viewer, current):
            return None, current.slug, None
    if circuit is None or not access.may_read(viewer, circuit):
        return None, None, _err('No such circuit.', 'CIRCUIT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return circuit, None, None


def _managed(request, ref):
    """(user, circuit, error) for a write: signed in, the circuit readable,
    and theirs to manage. Asked before anything in the body is read (R88)."""
    user, err = actor_from_request(request)
    if err:
        return None, None, err
    circuit, moved, err = _find(ref, user)
    if err or moved:
        return None, None, err or _err('No such circuit.', 'CIRCUIT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not access.may_manage(user, circuit):
        return None, None, _err('Only the person running this circuit can change it.',
                                'NOT_YOUR_CIRCUIT', status.HTTP_403_FORBIDDEN)
    return user, circuit, None


def _tournament(ref):
    from vent_tournament.lookup import find
    t = find(ref) if ref else None
    if t is None or getattr(t, 'deleted_at', None):
        return None
    return t


# ---------------------------------------------------------------------------
# Shapes (named fields only, R71)
# ---------------------------------------------------------------------------

def _game(game, request):
    if game is None:
        return None
    return {'id': game.game_id, 'title': game.game_title,
            'logo': _media(request, game.logo.url) if getattr(game, 'logo', None) else None}


def _person(user):
    if user is None:
        return None
    return {'name': user.full_name or user.username, 'handle': user.username}


def _org(org, request):
    if org is None:
        return None
    logo = getattr(org, 'logo', None)
    return {'name': org.org_name, 'slug': getattr(org, 'slug', None),
            'logo': _media(request, logo.url) if logo else None}


def _circuit_row(circuit, request, viewer=None):
    return {
        'slug': circuit.slug,
        'name': circuit.name,
        'description': circuit.description,
        'game': _game(circuit.game, request),
        'entrant': circuit.entrant,
        'season': circuit.season,
        'region': circuit.region,
        'starts_on': circuit.starts_on.isoformat() if circuit.starts_on else None,
        'ends_on': circuit.ends_on.isoformat() if circuit.ends_on else None,
        'engine': circuit.engine,
        'elo_k': standings.elo_k(circuit),
        'default_table': tables.text(circuit.default_table),
        'owner': _person(circuit.owner),
        'organization': _org(circuit.organization, request),
        'is_published': circuit.is_published,
        'can_manage': access.may_manage(viewer, circuit),
    }


def _tier_row(tier):
    return {'id': tier.id, 'name': tier.name, 'order': tier.order,
            'table': tables.text(tier.table), 'uses_default': not tier.table}


def _tournament_rows(circuit, viewer):
    manager = access.may_manage(viewer, circuit)
    links = (circuit.links.select_related('tournament', 'tournament__tournament_game', 'tier')
             .filter(tournament__deleted_at__isnull=True)
             .order_by('tournament__start_date_and_time', 'id'))
    counted = {link.pk for link in standings.counted_links(circuit)}
    out = []
    for link in links:
        t = link.tournament
        if t.is_draft and not manager:
            continue            # a draft tournament is its organiser's, not the public's
        out.append({
            'slug': t.slug, 'title': t.tournament_title, 'status': t.status, 'is_draft': t.is_draft,
            'starts_at': t.start_date_and_time.isoformat() if t.start_date_and_time else None,
            'game': t.tournament_game.game_title if t.tournament_game else None,
            'tier': {'id': link.tier.id, 'name': link.tier.name} if link.tier else None,
            'counts': link.pk in counted and t.status == 'completed',
        })
    return out


def _standing_rows(circuit, request):
    rows = standings.table(circuit)
    for row in rows:
        entrant = row['entrant']
        entrant['avatar'] = _media(request, entrant.pop('avatar_path', None))
        row.pop('name', None)
        row['points'] = tables.plain(row['points'])
        for event in row['events']:
            event['points'] = tables.plain(event['points'])
        for adj in row['adjustments']:
            adj['points'] = tables.plain(adj['points'])
    return rows


def _adjustment_rows(circuit):
    out = []
    for adj in circuit.adjustments.select_related('user', 'team', 'tournament'):
        if adj.team_id:
            entrant = {'type': 'team', 'name': adj.team.team_name, 'handle': None, 'slug': adj.team.slug}
        else:
            entrant = {'type': 'user', 'name': adj.user.full_name or adj.user.username,
                       'handle': adj.user.username, 'slug': adj.user.username}
        out.append({'id': adj.id, 'entrant': entrant, 'points': tables.plain(adj.points), 'reason': adj.reason,
                    'tournament': ({'slug': adj.tournament.slug, 'title': adj.tournament.tournament_title}
                                   if adj.tournament else None),
                    'at': adj.at.isoformat()})
    return out


def _detail(circuit, request, viewer):
    return {
        'circuit': _circuit_row(circuit, request, viewer),
        'tiers': [_tier_row(t) for t in circuit.tiers.all()],
        'tournaments': _tournament_rows(circuit, viewer),
        'standings': _standing_rows(circuit, request),
        'adjustments': _adjustment_rows(circuit),
    }


# ---------------------------------------------------------------------------
# Reading what a write sends
# ---------------------------------------------------------------------------

def _date(data, key):
    raw = inputs.read_text(data, key, max_length=10)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise inputs.BadInput(key, 'not a date')


def _read_circuit(data, user, partial):
    """The circuit fields present in `data`, read and bounded. (values, error)."""
    from vent_auth import org_link
    from vent_auth.models import Games
    out = {}
    if not partial or 'name' in data:
        out['name'] = inputs.read_text(data, 'name', max_length=120)
        if not out['name']:
            return None, _err('Give it a name.', 'NAME_REQUIRED', field='name')
    if not partial or 'description' in data:
        out['description'] = inputs.read_text(data, 'description', max_length=2000)
    if not partial or 'game' in data:
        game_id = inputs.read_int(data, 'game', minimum=1)
        game = Games.objects.filter(game_id=game_id).first() if game_id else None
        if game_id and game is None:
            return None, _err('No such game.', 'GAME_NOT_FOUND', field='game')
        out['game'] = game
    if not partial or 'entrant' in data:
        out['entrant'] = inputs.read_choice(data, 'entrant', ENTRANTS, default='player')
    if not partial or 'engine' in data:
        out['engine'] = inputs.read_choice(data, 'engine', ENGINES, default='points')
    if not partial or 'season' in data:
        out['season'] = inputs.read_text(data, 'season', max_length=60)
    if not partial or 'region' in data:
        out['region'] = inputs.read_choice(data, 'region', [''] + list(regions.ORDER), default='') or ''
    for key in ('starts_on', 'ends_on'):
        if not partial or key in data:
            out[key] = _date(data, key)
    if not partial or 'default_table' in data:
        try:
            out['default_table'] = tables.parse(inputs.read_text(data, 'default_table', max_length=2000))
        except tables.TableError as exc:
            return None, _table_err(exc, 'default_table')
    if not partial or 'organization' in data:
        org, code = org_link.resolve(inputs.read_text(data, 'organization', max_length=160), user)
        if code:
            return None, _err('That organisation cannot run this circuit.', code, field='organization')
        out['organization'] = org
    if 'is_published' in data:
        out['is_published'] = inputs.read_bool(data, 'is_published')
    if 'elo_k' in data:
        # Kept in settings: how far one match moves a rating (P5b).
        low, high = standings.ELO_K_RANGE
        out['elo_k'] = inputs.read_int(data, 'elo_k', minimum=low, maximum=high, required=True)
    return out, None


def _dates_problem(starts_on, ends_on):
    if starts_on and ends_on and ends_on < starts_on:
        return _err('The season cannot end before it starts.', 'CIRCUIT_DATES_ORDER', field='ends_on')
    return None


# ---------------------------------------------------------------------------
# The circuit
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
@_limit_writes
def circuit_list(request):
    if request.method == 'POST':
        return _create(request)
    viewer = _viewer(request)
    rows = Circuit.objects.select_related('game', 'owner', 'organization')
    if inputs.read_bool(request.query_params, 'mine'):
        user, err = actor_from_request(request)
        if err:
            return err
        rows = rows.filter(owner=user)
        viewer = user
    else:
        rows = rows.filter(is_published=True)
    tournament_ref = inputs.read_text(request.query_params, 'tournament', max_length=160)
    t = _tournament(tournament_ref) if tournament_ref else None
    if tournament_ref:
        rows = rows.filter(links__tournament=t) if t is not None else rows.none()
    game = inputs.read_int(request.query_params, 'game', minimum=1)
    if game:
        rows = rows.filter(game_id=game)
    region = inputs.read_choice(request.query_params, 'region', list(regions.ORDER))
    if region:
        rows = rows.filter(region=region)
    query = inputs.read_text(request.query_params, 'q', max_length=80)
    if query:
        # The one forgiving matcher every search uses (inbox 383): a
        # misspelt name still finds the circuit.
        from vent_auth import fuzzy
        rows = fuzzy.filter(rows, query, ['name', 'season'])
    out = []
    for circuit in rows[:LIST_LIMIT]:
        row = _circuit_row(circuit, request, viewer)
        row['tournaments'] = circuit.links.filter(tournament__deleted_at__isnull=True).count()
        if tournament_ref:
            # The tournament page's line: which tier it counts at and what
            # its places are worth there.
            link = circuit.links.select_related('tier').filter(tournament=t).first()
            if link is not None:
                row['for_tournament'] = {'tier': link.tier.name if link.tier else None,
                                         'table': tables.text(standings.table_for(circuit, link))}
        out.append(row)
    return _ok({'circuits': out}, 'Circuits')


def _create(request):
    user, err = actor_from_request(request)
    if err:
        return err
    values, err = _read_circuit(request.data, user, partial=False)
    if err:
        return err
    problem = _dates_problem(values.get('starts_on'), values.get('ends_on'))
    if problem:
        return problem
    k = values.pop('elo_k', None)
    circuit = Circuit(owner=user, **values)
    if k is not None:
        circuit.settings = {'k': k}
    circuit.save()
    return _ok({'circuit': _circuit_row(circuit, request, user)}, 'Circuit made.', status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([AllowAny])
@_limit_writes
def circuit_detail(request, circuit_ref):
    if request.method == 'GET':
        viewer = _viewer(request)
        circuit, moved, err = _find(circuit_ref, viewer)
        if moved:
            return Response({'status': 'moved', 'data': {'slug': moved, 'url': '/circuits/%s' % moved},
                             'message': 'This circuit has a new address.'})
        if err:
            return err
        return _ok(_detail(circuit, request, viewer), circuit.name)

    user, circuit, err = _managed(request, circuit_ref)
    if err:
        return err
    if request.method == 'DELETE':
        circuit.delete()
        return _ok({'deleted': True}, 'Deleted. The tournaments in it stay as they are.')

    values, err = _read_circuit(request.data, user, partial=True)
    if err:
        return err
    problem = _dates_problem(values.get('starts_on', circuit.starts_on), values.get('ends_on', circuit.ends_on))
    if problem:
        return problem
    k = values.pop('elo_k', None)
    if k is not None:
        circuit.settings = dict(circuit.settings or {}, k=k)
    for name, value in values.items():
        setattr(circuit, name, value)
    circuit.save()
    return _ok(_detail(circuit, request, user), 'Saved.')


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

def _read_tier(data, partial):
    out = {}
    if not partial or 'name' in data:
        out['name'] = inputs.read_text(data, 'name', max_length=60)
        if not out['name']:
            return None, _err('Give the tier a name.', 'NAME_REQUIRED', field='name')
    if not partial or 'order' in data:
        out['order'] = inputs.read_int(data, 'order', default=0, minimum=0, maximum=99)
    if not partial or 'table' in data:
        try:
            out['table'] = tables.parse(inputs.read_text(data, 'table', max_length=2000))
        except tables.TableError as exc:
            return None, _table_err(exc, 'table')
    return out, None


def _save_tier(tier):
    try:
        with transaction.atomic():
            tier.save()
    except IntegrityError:
        return _err('This circuit already has a tier with that name.', 'TIER_NAME_TAKEN',
                    status.HTTP_409_CONFLICT, field='name')
    return None


@api_view(['POST'])
@_limit_writes
def tier_list(request, circuit_ref):
    user, circuit, err = _managed(request, circuit_ref)
    if err:
        return err
    values, err = _read_tier(request.data, partial=False)
    if err:
        return err
    tier = CircuitTier(circuit=circuit, **values)
    err = _save_tier(tier)
    if err:
        return err
    return _ok({'tier': _tier_row(tier)}, 'Tier added.', status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@_limit_writes
def tier_detail(request, circuit_ref, circuit_tier_id):
    user, circuit, err = _managed(request, circuit_ref)
    if err:
        return err
    tier = circuit.tiers.filter(pk=circuit_tier_id).first()
    if tier is None:
        return _err('No such tier in this circuit.', 'TIER_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if request.method == 'DELETE':
        tier.delete()
        return _ok({'deleted': True}, 'Tier removed. Its tournaments score by the default table.')
    values, err = _read_tier(request.data, partial=True)
    if err:
        return err
    for name, value in values.items():
        setattr(tier, name, value)
    err = _save_tier(tier)
    if err:
        return err
    return _ok({'tier': _tier_row(tier)}, 'Saved.')


# ---------------------------------------------------------------------------
# Tournaments in a circuit
# ---------------------------------------------------------------------------

def _tier_of(circuit, data):
    """(tier or None, error) for a `tier` id that must belong to this circuit."""
    tier_id = inputs.read_int(data, 'tier', minimum=1)
    if not tier_id:
        return None, None
    tier = circuit.tiers.filter(pk=tier_id).first()
    if tier is None:
        return None, _err('No such tier in this circuit.', 'TIER_NOT_FOUND', status.HTTP_404_NOT_FOUND,
                          field='tier')
    return tier, None


def _fits(circuit, t):
    """A refusal when tournament `t` cannot count in this circuit, or None."""
    if circuit.game_id and t.tournament_game_id != circuit.game_id:
        return _err('That tournament is for another game.', 'CIRCUIT_GAME_MISMATCH',
                    status.HTTP_409_CONFLICT, field='tournament')
    if circuit.entrant == 'team' and t.tournament_access not in ('team', 'team_and_individual'):
        return _err('A team circuit counts tournaments clubs enter.', 'CIRCUIT_ENTRANT_MISMATCH',
                    status.HTTP_409_CONFLICT, field='tournament')
    return None


@api_view(['POST'])
@_limit_writes
def tournament_list(request, circuit_ref):
    user, circuit, err = _managed(request, circuit_ref)
    if err:
        return err
    t = _tournament(inputs.read_text(request.data, 'tournament', max_length=160, required=True))
    if t is None:
        return _err('Tournament not found', 'TOURNAMENT_NOT_FOUND', status.HTTP_404_NOT_FOUND,
                    field='tournament')
    if not access.may_link(user, circuit, t):
        return _err('Only the organiser can put their tournament in a circuit.',
                    'ONLY_TOURNAMENT_ORGANIZER_CAN', status.HTTP_403_FORBIDDEN, field='tournament')
    problem = _fits(circuit, t)
    if problem:
        return problem
    tier, err = _tier_of(circuit, request.data)
    if err:
        return err
    existing = CircuitTournament.objects.filter(tournament=t).select_related('circuit').first()
    if existing is not None and existing.circuit_id != circuit.pk:
        return _err('That tournament already counts in another circuit.', 'CIRCUIT_TOURNAMENT_TAKEN',
                    status.HTTP_409_CONFLICT, field='tournament')
    if existing is not None:
        existing.tier = tier
        existing.save(update_fields=['tier'])
    else:
        CircuitTournament.objects.create(circuit=circuit, tournament=t, tier=tier, added_by=user)
    return _ok({'tournaments': _tournament_rows(circuit, user)}, 'Counted in %s.' % circuit.name,
               status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@_limit_writes
def tournament_detail(request, circuit_ref, tournament_ref):
    user, circuit, err = _managed(request, circuit_ref)
    if err:
        return err
    t = _tournament(tournament_ref)
    link = circuit.links.filter(tournament=t).first() if t is not None else None
    if link is None:
        return _err('That tournament is not in this circuit.', 'TOURNAMENT_NOT_IN_CIRCUIT',
                    status.HTTP_404_NOT_FOUND)
    if request.method == 'DELETE':
        link.delete()
        return _ok({'tournaments': _tournament_rows(circuit, user)}, 'No longer counted.')
    tier, err = _tier_of(circuit, request.data)
    if err:
        return err
    link.tier = tier
    link.save(update_fields=['tier'])
    return _ok({'tournaments': _tournament_rows(circuit, user)}, 'Saved.')


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------

def _entrant(circuit, ref):
    """The user (player circuit, by username) or club (team circuit, by its
    address) an adjustment is for, or None."""
    from vent_auth.models import Teams, Users
    ref = str(ref or '').strip().lstrip('@')
    if not ref:
        return None
    if circuit.entrant == 'team':
        return Teams.objects.filter(slug=ref).first()
    return Users.objects.filter(username__iexact=ref).first()


def _tell(circuit, adj):
    from vent_auth.views_notifications import create_notification
    recipient = adj.team.team_owner if adj.team_id else adj.user
    if recipient is None:
        return
    sign = '+' if adj.points >= 0 else ''
    create_notification(
        recipient, 'tournament', '%s: %s%s points' % (circuit.name, sign, adj.points), adj.reason,
        link='/circuits/%s' % circuit.slug, metadata={'circuit': circuit.slug, 'adjustment': adj.id})


@api_view(['POST'])
@_limit_writes
def adjustment_list(request, circuit_ref):
    user, circuit, err = _managed(request, circuit_ref)
    if err:
        return err
    who = _entrant(circuit, inputs.read_text(request.data, 'entrant', max_length=160, required=True))
    if who is None:
        return _err('Nobody by that name.', 'ENTRANT_NOT_FOUND', status.HTTP_404_NOT_FOUND, field='entrant')
    points = inputs.read_decimal(request.data, 'points', required=True, minimum=-1000000,
                                 maximum=1000000, places=2)
    if points == 0:
        return _err('An adjustment of nothing changes nothing.', 'ADJUSTMENT_ZERO', field='points')
    reason = inputs.read_text(request.data, 'reason', max_length=240)
    if len(reason) < 3:
        return _err('Say why, in a few words everybody will read.', 'REASON_REQUIRED', field='reason')
    tournament = None
    tournament_ref = inputs.read_text(request.data, 'tournament', max_length=160)
    if tournament_ref:
        t = _tournament(tournament_ref)
        if t is None or not circuit.links.filter(tournament=t).exists():
            return _err('That tournament is not in this circuit.', 'TOURNAMENT_NOT_IN_CIRCUIT',
                        status.HTTP_404_NOT_FOUND, field='tournament')
        tournament = t
    adj = CircuitAdjustment.objects.create(
        circuit=circuit, points=points, reason=reason, tournament=tournament, by=user,
        **({'team': who} if circuit.entrant == 'team' else {'user': who}))
    transaction.on_commit(lambda: _tell(circuit, adj))
    return _ok({'adjustments': _adjustment_rows(circuit)}, 'Adjustment saved.', status.HTTP_201_CREATED)


@api_view(['DELETE'])
@_limit_writes
def adjustment_detail(request, circuit_ref, circuit_adjustment_id):
    user, circuit, err = _managed(request, circuit_ref)
    if err:
        return err
    adj = circuit.adjustments.filter(pk=circuit_adjustment_id).first()
    if adj is None:
        return _err('No such adjustment in this circuit.', 'ADJUSTMENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    adj.delete()
    return _ok({'adjustments': _adjustment_rows(circuit)}, 'Adjustment taken back.')
