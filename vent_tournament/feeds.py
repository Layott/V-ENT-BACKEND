"""Places pulled from one tournament into another (P2, design section 7).

CEO, 16 October 2026 (inbox 470): brackets connect across tournaments and
events, for an organiser who runs both: "so far they have access to those
events". A feed is a `Progression` whose source stage is in another
tournament. The organiser of the target creates it; firing it registers each
qualifier in the target, and the line's `sent` rows carry those TARGET
registrations, so every reader of `sent` (the stage engine, seats waiting in an
early draw, the Path view) works exactly as it does inside one tournament.

Decisions accepted by silence (8 October rule): the fee is waived, a qualifier
has 48 hours to decline, the pull follows the line's own `fire`, same game only.
"""
from datetime import timedelta


class FeedError(ValueError):
    def __init__(self, code, field=None):
        super().__init__(code)
        self.code = code
        self.field = field


ON_DECLINE = ('next', 'organiser')


def _display(reg):
    """The name every screen shows (`match_shape.names`): a person's full
    name, a team's or squad's own. `entrant_name` is the username."""
    if reg is None:
        return None
    from .match_shape import names
    return names(reg)[0]


def is_feed(line):
    """A line from outside its target's tournament: another tournament's
    stage, or a circuit's ranking (P5c)."""
    if line.is_circuit_line:
        return True
    return line.source.tournament_id != line.target.tournament_id


def _entry_kind(tournament):
    """'team', 'player' or 'any', from who a tournament accepts."""
    access = (tournament.tournament_access or '').strip().lower()
    if access in ('team', 'teams') or (tournament.team_size or 1) > 1:
        return 'team'
    if access in ('individual', 'individuals'):
        return 'player'
    return 'any'


def _feeds_from(tournament_id):
    """Ids of the tournaments `tournament_id` already feeds."""
    from .models import Progression
    return set(Progression.objects.filter(source__tournament_id=tournament_id)
               .exclude(target__tournament_id=tournament_id)
               .values_list('target__tournament_id', flat=True))


def _reaches(start_id, goal_id):
    seen, todo = set(), [start_id]
    while todo:
        here = todo.pop()
        if here == goal_id:
            return True
        if here in seen:
            continue
        seen.add(here)
        todo.extend(_feeds_from(here))
    return False


def _places(source_stage, place_from, place_to, group):
    each = 1 if group is not None or (source_stage.groups or 0) < 2 else source_stage.groups
    return (place_to - place_from + 1) * each


def _line_places(line):
    """How many places a line will send: a ranking is one table."""
    if line.is_circuit_line:
        return line.place_to - line.place_from + 1
    return _places(line.source, line.place_from, line.place_to, line.group)


def _reserved(target):
    """Places the target's feeds have not sent yet."""
    from .models import Progression
    return sum(_line_places(l) for l in Progression.objects.filter(
        target__tournament=target, fired_at__isnull=True).exclude(source__tournament=target)
        .select_related('source'))


def check(target_stage, source_stage, user, place_from, place_to, group=None):
    """Refuse a feed that may not exist, with the code the screen translates."""
    from .access import may_manage
    from .models import Progression
    source, target = source_stage.tournament, target_stage.tournament
    if source.pk == target.pk:
        raise FeedError('FEED_SAME_TOURNAMENT', 'source_stage')
    if not (may_manage(user, source) and may_manage(user, target)):
        raise FeedError('FEED_NOT_MANAGER', 'source_tournament')
    if source.tournament_game_id != target.tournament_game_id:
        raise FeedError('FEED_GAME_MISMATCH', 'source_tournament')
    kinds = {_entry_kind(source), _entry_kind(target)}
    if kinds == {'team', 'player'}:
        raise FeedError('FEED_ENTRY_TYPE_MISMATCH', 'source_tournament')
    if (source.team_size or 1) > (target.team_size or 1):
        raise FeedError('FEED_TEAM_SIZE', 'source_tournament')
    if place_from < 1 or place_to < place_from:
        raise FeedError('FEED_PLACES', 'place_to')
    if group is not None and not (1 <= group <= (source_stage.groups or 0)):
        raise FeedError('FEED_GROUP', 'group')
    capacity = target.max_number_of_teams or target.player_size or 0
    if capacity:
        taken = target.registrations.filter(status__in=('pending', 'confirmed')).count()
        if taken + _reserved(target) + _places(source_stage, place_from, place_to, group) > capacity:
            raise FeedError('FEED_TARGET_FULL', 'place_to')
    if _reaches(target.pk, source.pk):
        raise FeedError('FEED_CYCLE', 'source_tournament')


def ticket_tiers(tournament):
    """The tiers a feed into `tournament` may hand its qualifiers: those of the
    events it is linked to (P2d)."""
    from vent_event.models import TicketTier
    return (TicketTier.objects.filter(event__tournament_links__tournament=tournament)
            .select_related('event').order_by('event_id', 'id'))


def _check_tier(target, tier):
    if tier is not None and not ticket_tiers(target).filter(pk=tier.pk).exists():
        raise FeedError('FEED_TICKET_NOT_THIS_EVENT', 'ticket_tier')


def create(target_stage, source_stage, user, place_from, place_to, group=None, side='any',
           fire='after_preview', decline_hours=48, on_decline='next', ticket_tier=None):
    from . import progressions
    from .models import Progression
    check(target_stage, source_stage, user, place_from, place_to, group)
    if side not in progressions.SIDES or (side != 'any' and target_stage.format != 'double_elimination'):
        raise FeedError('FEED_SIDE', 'side')
    if fire not in progressions.FIRES:
        raise FeedError('FEED_FIRE', 'fire')
    if on_decline not in ON_DECLINE:
        raise FeedError('FEED_ON_DECLINE', 'on_decline')
    if decline_hours is not None and not (1 <= int(decline_hours) <= 720):
        raise FeedError('FEED_DECLINE_HOURS', 'decline_hours')
    _check_tier(target_stage.tournament, ticket_tier)
    last = target_stage.incoming.order_by('-order').values_list('order', flat=True).first()
    return Progression.objects.create(
        source=source_stage, target=target_stage, group=group, place_from=place_from,
        place_to=place_to, side=side, fire=fire, order=(last or 0) + 1,
        decline_hours=decline_hours, on_decline=on_decline, ticket_tier=ticket_tier)


# ---------------------------------------------------------------------------
# A circuit's ranking as the source (P5c, design section 13)
# ---------------------------------------------------------------------------
#
# "A circuit ranking can be the source of a progression: top 8 of the season
# ranking go to the finals, places 9-16 go to the last chance qualifier." The
# same Progression row, with `source_circuit` in place of a source stage. A
# ranking has no registrations, so its rows name entrants (`user:<id>`,
# `team:<id>`) and qualifying registers them directly. It is pulled when the
# organiser says (fire 'manual'): a ranking never closes by itself. A declined
# place goes to the next in the ranking (on_decline 'next'); picking by hand
# needs a field of registrations to pick from, which a ranking is not.

def check_circuit(target_stage, circuit, user, place_from, place_to):
    """Refuse a circuit line that may not exist, with the feed codes."""
    from vent_circuits import access as circuit_access
    from .access import may_manage
    target = target_stage.tournament
    if not (circuit_access.may_manage(user, circuit) and may_manage(user, target)):
        raise FeedError('FEED_NOT_MANAGER', 'source_circuit')
    if circuit.game_id and circuit.game_id != target.tournament_game_id:
        raise FeedError('FEED_GAME_MISMATCH', 'source_circuit')
    kind = _entry_kind(target)
    if kind != 'any' and kind != ('team' if circuit.entrant == 'team' else 'player'):
        raise FeedError('FEED_ENTRY_TYPE_MISMATCH', 'source_circuit')
    if place_from < 1 or place_to < place_from:
        raise FeedError('FEED_PLACES', 'place_to')
    capacity = target.max_number_of_teams or target.player_size or 0
    if capacity:
        taken = target.registrations.filter(status__in=('pending', 'confirmed')).count()
        if taken + _reserved(target) + (place_to - place_from + 1) > capacity:
            raise FeedError('FEED_TARGET_FULL', 'place_to')


def create_from_circuit(target_stage, circuit, user, place_from, place_to, side='any',
                        fire='manual', decline_hours=48, on_decline='next'):
    from . import progressions
    from .models import Progression
    check_circuit(target_stage, circuit, user, place_from, place_to)
    if side not in progressions.SIDES or (side != 'any' and target_stage.format != 'double_elimination'):
        raise FeedError('FEED_SIDE', 'side')
    if fire != 'manual':
        raise FeedError('FEED_FIRE', 'fire')
    if on_decline != 'next':
        raise FeedError('FEED_ON_DECLINE', 'on_decline')
    if decline_hours is not None and not (1 <= int(decline_hours) <= 720):
        raise FeedError('FEED_DECLINE_HOURS', 'decline_hours')
    last = target_stage.incoming.order_by('-order').values_list('order', flat=True).first()
    return Progression.objects.create(
        source_circuit=circuit, target=target_stage, place_from=place_from, place_to=place_to,
        side=side, fire=fire, order=(last or 0) + 1, decline_hours=decline_hours, on_decline=on_decline)


def _ranking_rows(circuit):
    """The circuit's ranking as feed rows, by position (unique), best first."""
    from vent_circuits import standings
    out = []
    for r in standings.table(circuit):
        e = r['entrant']
        out.append({'registration_id': None, 'source_entrant': '%s:%s' % (e['type'], e['id']),
                    'name': e.get('name'), 'handle': e.get('handle'), 'group': None,
                    'rank': r['position'], 'points': float(r['points'])})
    return out


def circuit_rows(line):
    """The places `line` takes from its circuit's ranking."""
    return [r for r in _ranking_rows(line.source_circuit)
            if line.place_from <= r['rank'] <= line.place_to]


def _entrant(key):
    """(user, team) for an entrant key from a ranking row."""
    from vent_auth.models import Teams, Users
    kind, _, ident = str(key or '').partition(':')
    if kind == 'team':
        return None, Teams.objects.filter(team_id=int(ident)).first()
    return Users.objects.filter(user_id=int(ident)).first(), None


def _existing_entrant(target, user, team):
    rows = target.registrations.exclude(status__in=('withdrawn', 'disqualified', 'declined'))
    if team is not None:
        return rows.filter(team=team, squad__isnull=True).first()
    return rows.filter(user=user, team__isnull=True, squad__isnull=True).first()


def _source_name(line):
    return line.source_circuit.name if line.is_circuit_line else line.source.tournament.tournament_title


def _existing(target, source_reg):
    """The same entrant already in the target, matched the way every screen
    names a side (`entrant_kind` and `entrant_id`), so a team, a squad and a
    lone player are each recognised as themselves."""
    kind, ident = source_reg.entrant_kind, source_reg.entrant_id
    for reg in target.registrations.exclude(status__in=('withdrawn', 'disqualified')):
        if reg.entrant_kind == kind and reg.entrant_id == ident:
            return reg
    return None


def _tell(registration, line, place, until):
    from vent_auth.views_notifications import create_notification
    target = line.target.tournament
    recipient = registration.team.team_owner if registration.team_id else registration.user
    if recipient is None:
        return
    body = 'From %s, place %s.' % (_source_name(line), place)
    if until is not None:
        body += ' If you cannot play, decline before %s UTC.' % until.strftime('%d %b %Y %H:%M')
    create_notification(
        recipient, 'tournament', 'You qualified for %s' % target.tournament_title, body,
        link='/tournaments/%s' % (target.slug or target.tournament_id),
        metadata={'tournament_id': target.tournament_id, 'qualified_via': line.id,
                  'source_tournament_id': None if line.is_circuit_line else line.source.tournament_id,
                  'source_circuit': line.source_circuit.slug if line.is_circuit_line else None,
                  'source_place': place})


def qualify(line, rows):
    """Register the qualifiers a feed sends in its target, and give back the
    rows with the TARGET registration ids (`source_registration_id` keeps where
    they came from). Somebody already registered there keeps that registration
    and is marked qualified; nobody is registered twice or charged."""
    from django.utils import timezone
    from .models import TournamentRegistration
    target = line.target.tournament
    until = (timezone.now() + timedelta(hours=line.decline_hours)) if line.decline_hours else None
    out = []
    for row in rows:
        place = row.get('rank')
        if row.get('source_entrant'):
            # From a circuit ranking (P5c): the entrant itself, no registration.
            user, team = _entrant(row['source_entrant'])
            reg = _existing_entrant(target, user, team)
            reused = reg is not None
            if reg is None:
                reg = TournamentRegistration.objects.create(
                    tournament=target, user=None if team is not None else user, team=team,
                    status='confirmed', entry_fee_paid=True, qualified_via=line,
                    source_place=place, decline_until=until)
            else:
                reg.qualified_via, reg.source_place, reg.decline_until = line, place, until
                reg.save(update_fields=['qualified_via', 'source_place', 'decline_until'])
            _tell(reg, line, place, until)
            _reward(line, reg)
            out.append(dict(row, registration_id=reg.id, reused=reused))
            continue
        source_reg = TournamentRegistration.objects.select_related('user', 'team').get(
            pk=row['registration_id'])
        reg = _existing(target, source_reg)
        reused = reg is not None
        if reg is None:
            reg = TournamentRegistration.objects.create(
                tournament=target, user=source_reg.user, team=source_reg.team,
                status='confirmed', entry_fee_paid=True, qualified_via=line,
                source_place=place, decline_until=until)
        else:
            reg.qualified_via, reg.source_place, reg.decline_until = line, place, until
            reg.save(update_fields=['qualified_via', 'source_place', 'decline_until'])
        _tell(reg, line, place, until)
        _reward(line, reg)
        out.append(dict(row, registration_id=reg.id, source_registration_id=source_reg.id,
                        reused=reused))
    return out


def _reference(line, registration):
    return 'qualified:%s:%s' % (line.pk, registration.pk)


def _reward(line, registration):
    """The ticket the feed hands its qualifiers (P2d), through the one way a
    reward ticket is made. A team entry gets none: there is no single person
    to hand it to, the same answer as the round reward."""
    from . import rewards
    if line.ticket_tier_id:
        tier = line.ticket_tier
        rewards.issue(tier.event_id, tier.id,
                      registration.user if registration.user_id else None,
                      _reference(line, registration))


def unqualify(line):
    """Undo what a feed did in its target, when the stage that sent it is
    undone: a registration it made is withdrawn; somebody who had registered
    themselves keeps their place and is simply no longer marked qualified."""
    from .models import TournamentRegistration
    for row in (line.sent or []):
        reg = TournamentRegistration.objects.filter(
            pk=row.get('registration_id'), tournament_id=line.target.tournament_id).first()
        if reg is None:
            continue
        from . import rewards
        rewards.withdraw(_reference(line, reg))
        if row.get('reused'):
            reg.qualified_via, reg.source_place, reg.decline_until = None, None, None
            reg.save(update_fields=['qualified_via', 'source_place', 'decline_until'])
        else:
            reg.status = 'withdrawn'
            reg.save(update_fields=['status'])


# ---------------------------------------------------------------------------
# Declining, picking, recalling (P2b)
# ---------------------------------------------------------------------------

PLAYED = ('completed', 'walkover_p1', 'walkover_p2', 'in_progress', 'pending_opponent_confirm',
          'disputed')


def _now():
    from django.utils import timezone
    return timezone.now()


def _index(line, registration_id):
    for i, row in enumerate(line.sent or []):
        if int(row.get('registration_id') or 0) == int(registration_id):
            return i
    return None


def _taken(line):
    """Source registrations this stage's feeds already sent, or that turned a
    place down or were recalled: none of them is offered a place again. For a
    circuit's lines, the entrants (`user:<id>`) its lines sent or lost."""
    if line.is_circuit_line:
        keys = set()
        for other in line.source_circuit.lines.all():
            keys.update(row['source_entrant'] for row in (other.sent or []) if row.get('source_entrant'))
            keys.update(c['source_entrant'] for c in (other.history or []) if c.get('source_entrant'))
        return keys
    ids = set()
    for other in line.source.outgoing.all():
        for row in (other.sent or []):
            ids.add(int(row.get('source_registration_id') or row.get('registration_id')))
        for change in (other.history or []):
            if change.get('source_registration_id'):
                ids.add(int(change['source_registration_id']))
    return ids


def next_placement(line, group=None):
    """The next placement of the source after the places this feed takes,
    from the same group when the feed takes places group by group."""
    from . import stage_engine
    if line.is_circuit_line:
        taken = _taken(line)
        rows = [r for r in _ranking_rows(line.source_circuit)
                if r['rank'] > line.place_to and r['source_entrant'] not in taken]
        return rows[0] if rows else None
    grouped = (line.source.groups or 0) > 1 and line.group is None
    taken = _taken(line)
    rows = [r for r in stage_engine.standings(line.source)
            if (r.get('rank') or 0) > line.place_to
            and (not grouped or r.get('group') == group)
            and (line.group is None or r.get('group') == line.group)
            and int(r['registration_id']) not in taken]
    rows.sort(key=lambda r: (r.get('rank') or 0, r.get('group') or 0))
    return rows[0] if rows else None


def _matches_of(line, registration):
    from .models import BracketMatch
    from django.db.models import Q
    return (BracketMatch.objects.filter(stage=line.target)
            .filter(Q(participant_1=registration) | Q(participant_2=registration))
            .order_by('-round_number', 'match_number'))


def _seat(line, index, row, leaving, actor):
    """Put a newly qualified row where the leaving one was: the same place in
    what the feed sent, and the same seat in a target already drawn."""
    from . import bracket_edit, progressions
    from .models import TournamentRegistration
    new_row = qualify(line, [row])[0]
    line.sent[index] = progressions._sent_row(new_row, line.carry_points)
    target = line.target
    if target.drawn_at is not None:
        incoming = TournamentRegistration.objects.get(pk=new_row['registration_id'])
        bracket_edit.replace(target.tournament, actor, leaving.id, incoming.id, stage_id=target.id)
    return new_row


def _hand_on(line, index, leaving, actor, mode):
    row = line.sent[index]
    if mode == 'organiser':
        line.waiting = list(line.waiting or []) + [{
            'place': row.get('rank'), 'group': row.get('group'), 'index': index,
            'declined_registration_id': leaving.id}]
        if line.target.drawn_at is None:
            line.sent = [r for i, r in enumerate(line.sent) if i != index]
        return None
    replacement = next_placement(line, row.get('group'))
    if replacement is None:
        if line.target.drawn_at is None:
            line.sent = [r for i, r in enumerate(line.sent) if i != index]
        return None
    return _seat(line, index, replacement, leaving, actor)


def _record(line, kind, row, registration):
    change = {'kind': kind, 'source_registration_id': row.get('source_registration_id'),
              'registration_id': registration.id, 'at': _now().isoformat()}
    if row.get('source_entrant'):
        change['source_entrant'] = row['source_entrant']
    line.history = list(line.history or []) + [change]


def decline(registration, user):
    """The qualifier turns the place down, inside the window and before
    playing a match; the place goes on as the feed says."""
    line = registration.qualified_via
    if line is None or registration.status not in ('pending', 'confirmed'):
        raise FeedError('NOT_A_QUALIFIER')
    actor = registration.acting_user
    if user is None or actor is None or actor.user_id != user.user_id:
        raise FeedError('NOT_YOURS')
    if registration.decline_until is None or _now() > registration.decline_until:
        raise FeedError('DECLINE_CLOSED')
    if _matches_of(line, registration).filter(status__in=PLAYED).exists():
        raise FeedError('DECLINE_CLOSED')
    index = _index(line, registration.id)
    if index is None:
        raise FeedError('NOT_A_QUALIFIER')
    row = line.sent[index]
    registration.status = 'declined'
    registration.save(update_fields=['status'])
    # A place turned down gives back the ticket it earned, if it is unused.
    from . import rewards
    rewards.withdraw(_reference(line, registration))
    _record(line, 'declined', row, registration)
    _hand_on(line, index, registration, user, line.on_decline)
    line.save(update_fields=['sent', 'waiting', 'history'])
    _draw_if_ready(line, user)
    return registration


def pick(line, source_registration_id, user):
    """The organiser fills a declined place with anybody from the source
    stage who has not had a place yet."""
    from . import stage_engine
    from .access import may_manage
    if line.is_circuit_line:
        # A declined place from a ranking goes to the next in it; nothing waits.
        raise FeedError('NOTHING_WAITING')
    if not (may_manage(user, line.target.tournament) and may_manage(user, line.source.tournament)):
        raise FeedError('FEED_NOT_MANAGER')
    if not line.waiting:
        raise FeedError('NOTHING_WAITING')
    row = next((r for r in stage_engine.standings(line.source)
                if int(r['registration_id']) == int(source_registration_id)), None)
    if row is None:
        raise FeedError('NOT_IN_THIS_STAGE', 'source_registration')
    if int(source_registration_id) in _taken(line):
        raise FeedError('ALREADY_QUALIFIED', 'source_registration')
    spot, rest = line.waiting[0], line.waiting[1:]
    line.waiting = rest
    if line.target.drawn_at is not None:
        from .models import TournamentRegistration
        leaving = TournamentRegistration.objects.get(pk=spot['declined_registration_id'])
        new_row = _seat(line, spot['index'], row, leaving, user)
    else:
        from . import progressions
        new_row = qualify(line, [row])[0]
        sent = list(line.sent or [])
        sent.insert(min(spot['index'], len(sent)), progressions._sent_row(new_row, line.carry_points))
        line.sent = sent
    line.history = list(line.history or []) + [{
        'kind': 'picked', 'source_registration_id': int(source_registration_id),
        'registration_id': new_row['registration_id'], 'at': _now().isoformat()}]
    line.save(update_fields=['sent', 'waiting', 'history'])
    _draw_if_ready(line, user)
    return new_row


def _draw_if_ready(line, user):
    from . import progressions, stage_engine
    target = line.target
    target.refresh_from_db()
    if target.drawn_at is None and progressions.ready(target):
        stage_engine.draw(target, user or target.tournament.tournament_creator)


def _match_row(match, registration):
    other = match.participant_2 if match.participant_1_id == registration.id else match.participant_1
    return {'match_id': match.id, 'round': match.round_number, 'number': match.match_number,
            'side': match.bracket_side, 'status': match.status,
            'opponent': _display(other)}


def recall_preview(registration):
    """What a recall touches: every match of the target this qualifier has
    played or is drawn into, and who would take the place."""
    line = registration.qualified_via
    if line is None:
        raise FeedError('NOT_A_QUALIFIER')
    matches = list(_matches_of(line, registration).select_related(
        'participant_1__user', 'participant_1__team', 'participant_2__user', 'participant_2__team'))
    index = _index(line, registration.id)
    group = (line.sent[index].get('group') if index is not None else None)
    nxt = next_placement(line, group)
    return {
        'registration_id': registration.id, 'name': _display(registration),
        'source_place': registration.source_place,
        'played': [_match_row(m, registration) for m in matches if m.status in PLAYED],
        'drawn': [_match_row(m, registration) for m in matches if m.status not in PLAYED
                  and m.status != 'bye'],
        'next': None if nxt is None else {
            'source_registration_id': nxt['registration_id'], 'name': nxt.get('name'),
            'source_place': nxt.get('rank'), 'group': nxt.get('group')},
    }


def recall(registration, user, mode='hand_on', replacement=None):
    """The organiser takes a qualifier's place back (CEO 475: always
    available). `hand_on` keeps what they played and gives everything still to
    play to the next placement (or `replacement`); `undo` takes their results
    off first."""
    from . import bracket_edit, stage_engine
    from .access import may_manage
    from .models import BracketMatch
    line = registration.qualified_via
    if line is None or registration.status not in ('pending', 'confirmed'):
        raise FeedError('NOT_A_QUALIFIER')
    target = line.target
    if not may_manage(user, target.tournament):
        raise FeedError('NOT_YOURS')
    if mode not in ('hand_on', 'undo'):
        raise FeedError('RECALL_MODE', 'mode')
    if mode == 'undo':
        for match in _matches_of(line, registration).filter(status__in=PLAYED):
            bracket_edit.reopen(BracketMatch.objects.select_for_update().get(pk=match.pk), user)
    index = _index(line, registration.id)
    row = line.sent[index] if index is not None else {}
    if replacement is not None and line.is_circuit_line:
        # A ranking hands a place on to its next entrant, never by number.
        raise FeedError('NOT_IN_THIS_STAGE', 'replacement')
    if replacement is not None:
        chosen = next((r for r in stage_engine.standings(line.source)
                       if int(r['registration_id']) == int(replacement)), None)
        if chosen is None:
            raise FeedError('NOT_IN_THIS_STAGE', 'replacement')
        if int(replacement) in _taken(line):
            raise FeedError('ALREADY_QUALIFIED', 'replacement')
    else:
        chosen = next_placement(line, row.get('group'))
    still_to_play = _matches_of(line, registration).exclude(status__in=PLAYED).exclude(status='bye')
    incoming = None
    if index is not None and chosen is not None and (target.drawn_at is None or still_to_play.exists()):
        incoming = _seat(line, index, chosen, registration, user)
    elif index is not None and target.drawn_at is None:
        line.sent = [r for i, r in enumerate(line.sent) if i != index]
    registration.status = 'withdrawn'
    registration.save(update_fields=['status'])
    _record(line, 'recalled', row, registration)
    line.save(update_fields=['sent', 'history'])
    bracket_edit._log(target.tournament, user, 'recall', target, registration=registration.id,
                      incoming=incoming['registration_id'] if incoming else None, mode=mode)
    _tell_recalled(registration, line)
    return {'recalled': registration.id,
            'incoming': incoming['registration_id'] if incoming else None}


def _tell_recalled(registration, line):
    from vent_auth.views_notifications import create_notification
    target = line.target.tournament
    recipient = registration.acting_user
    if recipient is None:
        return
    create_notification(
        recipient, 'tournament', 'Your place in %s was recalled' % target.tournament_title,
        'The organiser took back the place you qualified for.',
        link='/tournaments/%s' % (target.slug or target.tournament_id),
        metadata={'tournament_id': target.tournament_id, 'qualified_via': line.id})


def send_on_close(stage, user=None):
    """A stage that closed by itself (the qualifier's last result) sends its
    feeds that go on close, and draws each target that is then ready."""
    from . import progressions, stage_engine
    due = [line for line in stage.outgoing.select_related('source', 'target')
           if is_feed(line) and line.fire == 'on_close' and line.fired_at is None]
    if not due:
        return
    for line in due:
        progressions.fire(line, user)
    actor = user or stage.tournament.tournament_creator
    stage_engine.after_fire(stage, actor, due)


def rows(tournament):
    """The feeds into and out of `tournament`, for its public page."""
    from .models import Progression

    def side(stage):
        t = stage.tournament
        return {'stage_id': stage.id, 'stage': stage.label, 'format': stage.format,
                'groups': stage.groups, 'status': stage.status,
                'tournament': {'slug': t.slug, 'title': t.tournament_title}}

    def circuit_side(circuit):
        # A circuit's ranking as a source (P5c): the same shape, with the
        # circuit named and no stage or tournament of its own.
        return {'stage_id': None, 'stage': None, 'format': 'circuit', 'groups': 0,
                'status': 'ranking', 'tournament': None,
                'circuit': {'slug': circuit.slug, 'name': circuit.name}}

    def row(line):
        source = circuit_side(line.source_circuit) if line.is_circuit_line else side(line.source)
        return {'id': line.id, 'source': source, 'target': side(line.target),
                'group': line.group, 'place_from': line.place_from, 'place_to': line.place_to,
                'side': line.side, 'fire': line.fire, 'decline_hours': line.decline_hours,
                'on_decline': line.on_decline, 'fired_at': line.fired_at,
                'ticket_tier': ({'id': line.ticket_tier_id, 'name': line.ticket_tier.name}
                                if line.ticket_tier_id else None),
                'sent_count': len(line.sent or []),
                # Who it sent and where they stand (names and places, the facts
                # a standings table shows), and declined places waiting for
                # the organiser's pick (P2b).
                'qualifiers': [{'registration_id': r.id, 'name': _display(r),
                                'source_place': r.source_place, 'status': r.status,
                                'decline_until': r.decline_until}
                               for r in line.qualifiers.select_related('user', 'team', 'squad')
                               .order_by('source_place', 'id')],
                'waiting': [{'place': w.get('place'), 'group': w.get('group')}
                            for w in (line.waiting or [])]}

    base = Progression.objects.select_related('source__tournament', 'target__tournament', 'source_circuit')
    incoming = base.filter(target__tournament=tournament).exclude(source__tournament=tournament)
    outgoing = base.filter(source__tournament=tournament).exclude(target__tournament=tournament)
    return {'incoming': [row(l) for l in incoming.order_by('order', 'id')],
            'outgoing': [row(l) for l in outgoing.order_by('order', 'id')],
            # What a new feed into this tournament may hand its qualifiers.
            'ticket_tiers': [{'id': t.id, 'name': t.name, 'event': t.event.name}
                             for t in ticket_tiers(tournament)]}
