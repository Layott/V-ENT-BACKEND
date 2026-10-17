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


def is_feed(line):
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
        reserved = sum(_places(l.source, l.place_from, l.place_to, l.group)
                       for l in Progression.objects.filter(target__tournament=target, fired_at__isnull=True)
                       .exclude(source__tournament=target).select_related('source'))
        if taken + reserved + _places(source_stage, place_from, place_to, group) > capacity:
            raise FeedError('FEED_TARGET_FULL', 'place_to')
    if _reaches(target.pk, source.pk):
        raise FeedError('FEED_CYCLE', 'source_tournament')


def create(target_stage, source_stage, user, place_from, place_to, group=None, side='any',
           fire='after_preview', decline_hours=48, on_decline='next'):
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
    last = target_stage.incoming.order_by('-order').values_list('order', flat=True).first()
    return Progression.objects.create(
        source=source_stage, target=target_stage, group=group, place_from=place_from,
        place_to=place_to, side=side, fire=fire, order=(last or 0) + 1,
        decline_hours=decline_hours, on_decline=on_decline)


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
    target, source = line.target.tournament, line.source.tournament
    recipient = registration.team.team_owner if registration.team_id else registration.user
    if recipient is None:
        return
    body = 'From %s, place %s.' % (source.tournament_title, place)
    if until is not None:
        body += ' If you cannot play, decline before %s UTC.' % until.strftime('%d %b %Y %H:%M')
    create_notification(
        recipient, 'tournament', 'You qualified for %s' % target.tournament_title, body,
        link='/tournaments/%s' % (target.slug or target.tournament_id),
        metadata={'tournament_id': target.tournament_id, 'qualified_via': line.id,
                  'source_tournament_id': source.tournament_id, 'source_place': place})


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
        source_reg = TournamentRegistration.objects.select_related('user', 'team').get(
            pk=row['registration_id'])
        place = row.get('rank')
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
        out.append(dict(row, registration_id=reg.id, source_registration_id=source_reg.id,
                        reused=reused))
    return out


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
        if row.get('reused'):
            reg.qualified_via, reg.source_place, reg.decline_until = None, None, None
            reg.save(update_fields=['qualified_via', 'source_place', 'decline_until'])
        else:
            reg.status = 'withdrawn'
            reg.save(update_fields=['status'])


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
                'groups': stage.groups,
                'tournament': {'slug': t.slug, 'title': t.tournament_title}}

    def row(line):
        return {'id': line.id, 'source': side(line.source), 'target': side(line.target),
                'group': line.group, 'place_from': line.place_from, 'place_to': line.place_to,
                'side': line.side, 'fire': line.fire, 'decline_hours': line.decline_hours,
                'on_decline': line.on_decline, 'fired_at': line.fired_at,
                'sent_count': len(line.sent or [])}

    base = Progression.objects.select_related('source__tournament', 'target__tournament')
    incoming = base.filter(target__tournament=tournament).exclude(source__tournament=tournament)
    outgoing = base.filter(source__tournament=tournament).exclude(target__tournament=tournament)
    return {'incoming': [row(l) for l in incoming.order_by('order', 'id')],
            'outgoing': [row(l) for l in outgoing.order_by('order', 'id')]}
