"""Editing a tournament's structure and its brackets after it has started.

CEO, 12 October 2026 (inbox 450): "i want users to be able to edit their
tournaments, including the formats/brackets, seeding and all that event after
the event has started, they should also be able to like pick or apply
different formats/brackets to different stages of an already existiing
bracket." And on 13 October (inbox 452): "the bracket system and format has to
be really fluid fr editing everything inside."

What used to happen: the stage plan froze the moment any stage was drawn, a
bracket drawn as one format could never gain a stage, the general edit door
relabelled a drawn bracket's format without redrawing a single match, and
inside a bracket the only edit was recording a result.

The rule everything here follows: **a result somebody has played is history.**
An edit may rearrange what has not been played yet, freely. It may throw a
played result away only when the organiser says so in the same request
(`discard_results`), never as a side effect. Correcting a result is still
`results.py`, which already refuses to rewrite a result a later match was
built on.

Every function raises `EditError` with a code the screen translates, and runs
inside the caller's `transaction.atomic()`. Every change writes a
`BracketEdit` row, so "who moved that player" has an answer afterwards.
"""
from django.utils import timezone

from . import formats, stage_engine, stage_settings, stages
from .models import (BracketEdit, BracketGeneration, BracketMatch, TournamentRegistration,
                     TournamentStage)
from .services import bracket as bracket_service

#: A match nobody has started: free to rearrange.
OPEN = ('scheduled',)
#: Something happened in it: a result, a report, a dispute, a game under way.
PLAYED = ('completed', 'walkover_p1', 'walkover_p2', 'in_progress',
          'pending_opponent_confirm', 'disputed')
FINISHED = ('completed', 'cancelled')


class EditError(ValueError):
    def __init__(self, code, field=None, **extra):
        super().__init__(code)
        self.code = code
        self.field = field
        self.extra = extra


def _log(tournament, user, kind, stage=None, **detail):
    BracketEdit.objects.create(tournament=tournament, stage=stage, user=user, kind=kind,
                               detail={k: v for k, v in detail.items() if v is not None})


def not_finished(tournament):
    if tournament.status in FINISHED or tournament.completed_at is not None:
        raise EditError('TOURNAMENT_FINISHED')


def scope(tournament, stage):
    """The matches an edit works on: a stage's own, or a one-format bracket's."""
    if stage is not None:
        return stage.matches.all()
    return tournament.bracket_matches.filter(stage__isnull=True)


def played_count(tournament, stage):
    """How many results an undo would throw away."""
    count = scope(tournament, stage).filter(status__in=PLAYED).count()
    if stage is not None and stage.format == 'battle_royale':
        from .models import BRResult
        count += BRResult.objects.filter(map__lobby__stage=stage).values('map').distinct().count()
    return count


# ---------------------------------------------------------------------------
# A bracket drawn as one format becomes stage 1
# ---------------------------------------------------------------------------

def adopt(tournament, user):
    """Turn the one-format bracket into the first stage, without touching a
    match: every pairing, score and route stays exactly as played. After this
    the stage tools apply to it, and stages can follow it where its format can
    feed another."""
    not_finished(tournament)
    if tournament.stages.exists():
        raise EditError('ALREADY_IN_STAGES')
    matches = tournament.bracket_matches.filter(stage__isnull=True)
    if not matches.exists():
        raise EditError('NO_BRACKET_YET')
    key = bracket_service.normalize_bracket_type(tournament.bracket_type)
    fmt = formats.get(key)
    if fmt is None or key == 'battle_royale':
        raise EditError('FORMAT_HAS_NO_BRACKET', 'format')

    generation = (BracketGeneration.objects.filter(tournament=tournament)
                  .order_by('-id').first())
    payload = (generation.seed_payload or {}) if generation else {}
    entrants = [int(i) for i in payload.get('registration_ids') or []]
    if not entrants:
        seen = []
        for m in matches.order_by('round_number', 'match_number'):
            for rid in (m.participant_1_id, m.participant_2_id):
                if rid and rid not in seen:
                    seen.append(rid)
        entrants = seen

    stage = TournamentStage.objects.create(
        tournament=tournament, order=0, label=fmt.label, format=key,
        advances=0, groups=0, settings=stage_settings.for_tournament(tournament),
        placement='cross', entrants=entrants, status='running',
        drawn_at=getattr(generation, 'generated_at', None) or timezone.now())
    moved = matches.update(stage=stage)
    _log(tournament, user, 'adopt', stage, format=key, matches=moved)
    return stage


# ---------------------------------------------------------------------------
# Undoing a draw, and undoing an advance
# ---------------------------------------------------------------------------

def _next_drawn(stage):
    nxt = stage_engine.next_stage(stage)
    return nxt is not None and (nxt.drawn_at is not None or nxt.matches.exists()
                                or nxt.br_lobbies.exists())


def undraw(stage, user, discard_results=False):
    """Take a stage back to undrawn: its matches go, its plan stays, and it can
    be changed and drawn again. Returns what was removed."""
    tournament = stage.tournament
    not_finished(tournament)
    if stage.drawn_at is None and not stage.matches.exists() and not stage.br_lobbies.exists():
        raise EditError('STAGE_NOT_DRAWN')
    if _next_drawn(stage):
        raise EditError('NEXT_STAGE_DRAWN')
    disputes = tournament.disputes.filter(status__in=('open', 'under_review'),
                                          match__stage=stage).count()
    if disputes:
        raise EditError('DISPUTES_OPEN', count=disputes)
    played = played_count(tournament, stage)
    if played and not discard_results:
        raise EditError('STAGE_HAS_RESULTS', played=played)

    removed = stage.matches.count()
    stage.matches.all().delete()
    stage.br_lobbies.all().delete()
    stage.entrants = []
    stage.drawn_at = None
    stage.status = 'pending'
    stage.advanced = []
    stage.completed_at = None
    stage.save(update_fields=['entrants', 'drawn_at', 'status', 'advanced', 'completed_at'])

    # Nothing drawn anywhere any more: the tournament is back to waiting for
    # its draw, as it was before the first one.
    if not tournament.bracket_matches.exists() and tournament.status == 'live':
        tournament.status = 'registration_closed'
        tournament.save(update_fields=['status'])
    _log(tournament, user, 'undraw', stage, matches=removed, results_discarded=played or None)
    return {'matches_removed': removed, 'results_discarded': played}


def unadvance(stage, user):
    """Open a closed stage again, so its results can be corrected and who goes
    through decided afresh. Only while the next stage is still undrawn."""
    tournament = stage.tournament
    not_finished(tournament)
    if stage.status != 'complete':
        raise EditError('STAGE_NOT_CLOSED')
    if _next_drawn(stage):
        raise EditError('NEXT_STAGE_DRAWN')
    stage.status = 'running'
    stage.advanced = []
    stage.completed_at = None
    stage.save(update_fields=['status', 'advanced', 'completed_at'])
    _log(tournament, user, 'unadvance', stage)
    return stage


# ---------------------------------------------------------------------------
# Changing a stage's format in one press
# ---------------------------------------------------------------------------

def reformat(stage, user, *, fmt_key, settings=None, groups=None, advances=None,
             seed_strategy='registration', manual_order=None, discard_results=False,
             draw=True):
    """Give a stage a different format (and its rules), and draw it again.

    The plan as a whole must still hold with the new format in it: what feeds
    this stage, and what this stage feeds, are checked the same way the plan
    editor checks them.
    """
    tournament = stage.tournament
    not_finished(tournament)
    was_drawn = stage.drawn_at is not None or stage.matches.exists() or stage.br_lobbies.exists()
    if was_drawn:
        undraw(stage, user, discard_results=discard_results)
        stage.refresh_from_db()

    proposed = []
    for other in tournament.stages.all().order_by('order'):
        row = {'format': other.format, 'label': other.label, 'advances': other.advances,
               'groups': other.groups, 'settings': other.settings, 'placement': other.placement,
               'direct_entrants': other.direct_entrants or []}
        if other.pk == stage.pk:
            row['format'] = fmt_key
            row['settings'] = settings if settings is not None else {}
            if groups is not None:
                row['groups'] = groups
            if advances is not None:
                row['advances'] = advances
            if fmt_key != stage.format and row['label'] == _format_label(stage.format):
                row['label'] = ''    # the new format's own name, rather than the old one's
        proposed.append(row)
    cleaned = _checked_plan(proposed)
    ids = list(tournament.stages.order_by('order').values_list('pk', flat=True))
    mine = cleaned[ids.index(stage.pk)]
    old = stage.format
    stage.format = mine['format']
    stage.settings = mine['settings']
    stage.groups = mine['groups']
    stage.advances = mine['advances']
    if not stage.label or stage.label == _format_label(old):
        stage.label = mine['label']
    stage.save(update_fields=['format', 'settings', 'groups', 'advances', 'label'])

    summary = None
    if draw:
        prev = stage_engine.previous_stage(stage)
        if prev is None or prev.status == 'complete':
            try:
                summary = stage_engine.draw(stage, user, seed_strategy, manual_order)
            except stage_engine.StageEngineError as exc:
                raise EditError(exc.code, exc.field, **(exc.extra or {}))
    _log(tournament, user, 'reformat', stage, old=old, new=stage.format,
         drawn=bool(summary), seeding=seed_strategy if summary else None)
    return {'stage_id': stage.id, 'format': stage.format, 'draw': summary}


# ---------------------------------------------------------------------------
# Inside a drawn bracket: moving people
# ---------------------------------------------------------------------------

def _stage_for(tournament, stage_id):
    if stage_id in (None, '', 0):
        return None
    stage = tournament.stages.filter(pk=stage_id).first()
    if stage is None:
        raise EditError('STAGE_NOT_FOUND')
    return stage


def _registration(tournament, rid, field):
    reg = TournamentRegistration.objects.filter(pk=rid, tournament=tournament).first()
    if reg is None:
        raise EditError('NOT_AN_ENTRANT', field)
    return reg


def _touching(matches, rid):
    from django.db.models import Q
    return matches.filter(Q(participant_1_id=rid) | Q(participant_2_id=rid) | Q(winner_id=rid))


def _movable(match):
    """A match a person can be moved out of or into: not started, or a bye
    (nobody played a bye; it only carried somebody on)."""
    return match.status in OPEN or match.status == 'bye'


def _rewrite(match, mapping):
    """Put people where `mapping` ({old registration id: new}) says, in one
    write per match, so two people trading places never pass through a value
    no registration has (MySQL checks a foreign key on every write)."""
    update = {}
    for field in ('participant_1_id', 'participant_2_id', 'winner_id'):
        current = getattr(match, field)
        if current in mapping:
            update[field] = mapping[current]
    if not update:
        return False
    if match.status in OPEN:
        # A side moved into a pairing has not checked in for it.
        update.update(checked_in_p1_at=None, checked_in_p2_at=None)
    BracketMatch.objects.filter(pk=match.pk).update(**update)
    return True


def _no_br(stage):
    if stage is not None and stage.format == 'battle_royale':
        raise EditError('FORMAT_NOT_SUPPORTED', 'format')


def swap(tournament, user, a_id, b_id, stage_id=None):
    """Two entrants trade places in everything of this bracket neither has
    played yet: a knockout seat, a group and all its fixtures, a Swiss pairing.
    """
    not_finished(tournament)
    stage = _stage_for(tournament, stage_id)
    _no_br(stage)
    if a_id == b_id:
        raise EditError('SAME_ENTRANT')
    a = _registration(tournament, a_id, 'a')
    b = _registration(tournament, b_id, 'b')
    matches = scope(tournament, stage)
    if not _touching(matches, a.id).exists() or not _touching(matches, b.id).exists():
        # Somebody not in this bracket is a replacement, not a swap.
        raise EditError('NOT_IN_THIS_BRACKET')
    from django.db.models import Q
    ids = (a.id, b.id)
    touched = list(matches.filter(Q(participant_1_id__in=ids) | Q(participant_2_id__in=ids)
                                  | Q(winner_id__in=ids)))
    played = [m.id for m in touched if not _movable(m)]
    if played:
        raise EditError('ENTRANT_HAS_PLAYED', matches=played)
    for m in touched:
        _rewrite(m, {a.id: b.id, b.id: a.id})

    if stage is not None and stage.entrants:
        order = [int(i) for i in stage.entrants]
        order = [b.id if i == a.id else a.id if i == b.id else i for i in order]
        stage.entrants = order
        stage.save(update_fields=['entrants'])
    _log(tournament, user, 'swap', stage, a=a.id, b=b.id, matches=len(touched))
    return {'matches_changed': len(touched)}


def replace(tournament, user, out_id, in_id, stage_id=None):
    """Somebody takes another entrant's place in everything still to play. What
    the one leaving already played stays theirs; the one coming in must be an
    entrant of this tournament who is not already in this bracket."""
    not_finished(tournament)
    stage = _stage_for(tournament, stage_id)
    _no_br(stage)
    out = _registration(tournament, out_id, 'out')
    incoming = _registration(tournament, in_id, 'in')
    if incoming.status not in ('pending', 'confirmed'):
        raise EditError('NOT_AN_ACTIVE_ENTRANT', 'in')
    matches = scope(tournament, stage)
    if _touching(matches, incoming.id).exists():
        raise EditError('ALREADY_IN_THIS_BRACKET', 'in')
    touched = [m for m in _touching(matches, out.id) if _movable(m)]
    if not touched:
        raise EditError('NOTHING_LEFT_TO_PLAY', 'out')
    for m in touched:
        _rewrite(m, {out.id: incoming.id})
    if stage is not None:
        order = [int(i) for i in (stage.entrants or [])]
        stage.entrants = [incoming.id if i == out.id else i for i in order]
        stage.save(update_fields=['entrants'])
    _log(tournament, user, 'replace', stage, out=out.id, incoming=incoming.id, matches=len(touched))
    return {'matches_changed': len(touched)}


# ---------------------------------------------------------------------------
# Inside a drawn bracket: one match
# ---------------------------------------------------------------------------

def reopen(match, user):
    """Take a result back off a match, so it can be played or entered again.

    The winner it sent on comes back out of the next match, which must not
    have been played yet: the organiser reopens the later match first, the same
    order `results.py` asks for when a result is corrected.
    """
    tournament = match.tournament
    not_finished(tournament)
    if match.status not in ('completed', 'walkover_p1', 'walkover_p2', 'pending_opponent_confirm',
                            'disputed', 'in_progress'):
        raise EditError('NOTHING_TO_REOPEN')
    stage = match.stage
    if stage is not None and stage.status == 'complete':
        raise EditError('STAGE_CLOSED')
    if stage is not None and stage.format == 'swiss' and stage.matches.filter(
            round_number__gt=match.round_number).exists():
        raise EditError('NEXT_ROUND_ALREADY_DRAWN')
    if tournament.disputes.filter(match=match, status__in=('open', 'under_review')).exists():
        raise EditError('DISPUTES_OPEN')

    targets = []
    for target_id, slot, who in ((match.winner_to_match_id, match.winner_to_slot, match.winner_id),
                                 (match.loser_to_match_id, match.loser_to_slot, None)):
        if not target_id:
            continue
        target = BracketMatch.objects.select_for_update().get(pk=target_id)
        if not _movable(target):
            raise EditError('NEXT_MATCH_ALREADY_PLAYED', next_match=target.id)
        targets.append((target, slot))

    for target, slot in targets:
        field = 'participant_%d_id' % (slot or 1)
        update = {field: None}
        if target.status == 'bye':
            update.update(status='scheduled', winner_id=None, completed_at=None)
        BracketMatch.objects.filter(pk=target.pk).update(**update)

    BracketMatch.objects.filter(pk=match.pk).update(
        status='scheduled', winner_id=None, score_p1=0, score_p2=0,
        penalties_p1=None, penalties_p2=None, games=[], completed_at=None,
        forfeit_reason='', recorded_by=None, recorded_at=None)
    match.score_submissions.all().delete()
    _log(tournament, user, 'reopen', stage, match=match.id)
    match.refresh_from_db()
    return match


def set_rules(match, user, *, best_of=None, legs=None, draw_allowed=None, whole_round=False):
    """How a match is played, after the draw: best of, two legs, whether it
    may end level. For one match, or every unplayed match of its round."""
    tournament = match.tournament
    not_finished(tournament)
    if match.status not in OPEN:
        raise EditError('MATCH_ALREADY_PLAYED')
    low, high = stage_settings.BEST_OF_RANGE
    update = {}
    if best_of is not None:
        if not low <= best_of <= high:
            raise EditError('OUT_OF_RANGE', 'best_of', low=low, high=high)
        update['best_of'] = best_of
    if legs is not None:
        if legs not in (1, 2):
            raise EditError('OUT_OF_RANGE', 'legs', low=1, high=2)
        update['legs'] = legs
    if draw_allowed is not None:
        if draw_allowed and (match.winner_to_match_id or match.loser_to_match_id or match.is_final):
            # A knockout match sends somebody on; level, it would send nobody.
            raise EditError('KNOCKOUT_NEEDS_A_WINNER', 'draw_allowed')
        update['draw_allowed'] = bool(draw_allowed)
    if not update:
        raise EditError('NOTHING_TO_CHANGE')

    if whole_round:
        group = (scope(tournament, match.stage).filter(
            round_number=match.round_number, bracket_side=match.bracket_side,
            status__in=OPEN))
        if update.get('draw_allowed'):
            group = group.filter(winner_to_match__isnull=True, loser_to_match__isnull=True,
                                 is_final=False)
        changed = group.update(**update)
    else:
        changed = BracketMatch.objects.filter(pk=match.pk).update(**update)
    _log(tournament, user, 'rules', match.stage, match=match.id, whole_round=whole_round or None,
         **update)
    return {'matches_changed': changed}


# ---------------------------------------------------------------------------
# The plan, after the start
# ---------------------------------------------------------------------------

#: What a drawn stage may still change: nothing a played match depends on.
DRAWN_FREE = ('label', 'rules', 'starts_at', 'ends_at', 'place_type', 'location', 'virtual_link')


def _drawn(stage):
    return stage.drawn_at is not None or stage.status != 'pending'


def save_plan(tournament, raw_stages, user, participants=None, raw_lines=None):
    """Save the stage plan at any point before the tournament ends.

    Stages already drawn stay first, in their order, with their format, groups,
    match rules, placement and invited entrants; their name, rules text, time
    and place still change, and while one runs, how many go through. Stages
    not drawn yet change freely, and are added, removed and reordered.
    """
    not_finished(tournament)
    if not isinstance(raw_stages, list):
        raise EditError('NOT_A_LIST', 'stages')
    existing = list(tournament.stages.all().order_by('order'))
    drawn = [s for s in existing if _drawn(s)]
    by_id = {s.id: s for s in existing}

    # Drawn stages hold the first places, in the order they were played.
    for index, stage in enumerate(drawn):
        if index >= len(raw_stages):
            raise EditError('DRAWN_STAGE_REMOVED', 'stages', stage_index=index)
        sent = raw_stages[index] if isinstance(raw_stages[index], dict) else {}
        try:
            sent_id = int(sent.get('id') or 0)
        except (TypeError, ValueError):
            sent_id = 0
        if sent_id != stage.id:
            raise EditError('STAGE_ORDER_LOCKED', 'stages', stage_index=index)

    merged = []
    for index, raw in enumerate(raw_stages):
        raw = dict(raw) if isinstance(raw, dict) else {}
        stage = by_id.get(int(raw.get('id') or 0)) if str(raw.get('id') or '').isdigit() else None
        if stage is not None and _drawn(stage):
            # Its shape is what was played. A different format or group count is
            # refused rather than ignored, because the screen offers a way to
            # change it (undo the draw, or change the format) and somebody
            # sending one meant it.
            for field, value in (('format', stage.format), ('groups', stage.groups)):
                if raw.get(field) not in (None, '') and _differs(field, raw[field], value):
                    raise EditError('STAGE_DRAWN_FIELD', field, stage_index=index)
            # The rest of its shape is kept as drawn, whatever arrived with it.
            raw.update({
                'format': stage.format, 'groups': stage.groups, 'placement': stage.placement,
                'direct_entrants': [int(i) for i in (stage.direct_entrants or [])],
                'settings': stage.settings or stage_settings.clean(stage.format, {}),
            })
            if stage.status == 'complete':
                raw['advances'] = stage.advances    # who went through is decided
        merged.append(raw)

    cleaned = _checked_plan(merged, participants=participants if not drawn else None,
                            lines=raw_lines is not None)

    # The lines between the stages (P1b). None is the old planner, which only
    # ever meant the chain its order and advances describe.
    from . import progressions
    if raw_lines is None:
        lines = progressions.chain(cleaned)
    else:
        try:
            lines = progressions.clean(raw_lines, cleaned,
                                       participants=participants if not drawn else None)
        except progressions.LineError as exc:
            raise EditError(exc.code, exc.field, line_index=exc.index)
        # `advances` stays meaningful for every reader that still asks it: the
        # most places any line takes out of the stage.
        for index, stage in enumerate(cleaned):
            stage['advances'] = max([l['place_to'] for l in lines if l['source'] == index],
                                    default=0)

    own = set(tournament.registrations.values_list('id', flat=True))
    for index, stage in enumerate(cleaned):
        if stage['direct_entrants'] and index == 0:
            raise EditError('DIRECT_INTO_FIRST_STAGE', 'direct_entrants', stage_index=index)
        if any(i not in own for i in stage['direct_entrants']):
            raise EditError('NOT_AN_ENTRANT', 'direct_entrants', stage_index=index)

    kept = set()
    for order, (raw, row) in enumerate(zip(merged, cleaned)):
        stage = by_id.get(int(raw.get('id') or 0)) if str(raw.get('id') or '').isdigit() else None
        if stage is not None and _drawn(stage):
            for field in DRAWN_FREE:
                setattr(stage, field, row[field])
            if stage.status != 'complete':
                stage.advances = row['advances']
            stage.order = order
            stage.save(update_fields=list(DRAWN_FREE) + ['advances', 'order'])
            kept.add(stage.id)
            continue
        if stage is None:
            stage = TournamentStage(tournament=tournament)
        for field in ('label', 'format', 'advances', 'groups', 'rules', 'starts_at', 'ends_at',
                      'place_type', 'location', 'virtual_link', 'settings', 'placement',
                      'direct_entrants'):
            setattr(stage, field, row[field])
        stage.order = order
        stage.save()
        kept.add(stage.id)

    removed = [s.id for s in existing if s.id not in kept]
    TournamentStage.objects.filter(pk__in=removed).delete()
    _save_lines(tournament, lines)
    if drawn or removed:
        _log(tournament, user, 'plan', None, stages=len(cleaned), removed=len(removed) or None)
    return list(tournament.stages.all().order_by('order'))


def _save_lines(tournament, lines):
    """Write the plan's lines. A line that already sent people is what
    happened: it must come back unchanged, and it is kept as it was."""
    from .models import Progression
    saved = list(tournament.stages.all().order_by('order'))
    wanted = [(saved[l['source']].id, saved[l['target']].id, l['group'], l['place_from'],
               l['place_to'], l['side']) for l in lines]
    existing = list(Progression.objects.filter(source__tournament=tournament))
    fired = {(l.source_id, l.target_id, l.group, l.place_from, l.place_to, l.side): l
             for l in existing if l.fired_at is not None}
    if any(shape not in wanted for shape in fired):
        raise EditError('PROGRESSION_FIRED_LOCKED', 'progressions')
    Progression.objects.filter(pk__in=[l.id for l in existing if l.fired_at is None]).delete()
    for line, shape in zip(lines, wanted):
        if shape in fired:
            kept = fired[shape]
            kept.fire, kept.carry_points, kept.order = line['fire'], line['carry_points'], line['order']
            kept.save(update_fields=['fire', 'carry_points', 'order'])
            continue
        Progression.objects.create(
            source_id=shape[0], target_id=shape[1], group=line['group'],
            place_from=line['place_from'], place_to=line['place_to'], side=line['side'],
            carry_points=line['carry_points'], fire=line['fire'], order=line['order'])


def _format_label(key):
    fmt = formats.get(key)
    return fmt.label if fmt else ''


def _checked_plan(rows, participants=None, lines=False):
    """`stages.plan`, refusing the way the plan door always has: a refusal that
    is a code goes as the code, a sentence goes as VALIDATION_FAILED with the
    sentence beside it and the row it is about."""
    try:
        return stages.plan(rows, participants=participants, lines=lines)
    except stages.StageError as exc:
        text = str(exc)
        if getattr(exc, 'code', None):
            # A sentence with its code: the code is translated, the numbers
            # and names it carries go along for the translation to fill.
            raise EditError(exc.code, getattr(exc, 'field', None), message=text,
                            stage_index=getattr(exc, 'index', None), **exc.params)
        is_code = text.replace('_', '').isalpha() and text.isupper()
        raise EditError(text if is_code else 'VALIDATION_FAILED', getattr(exc, 'field', None),
                        message=None if is_code else text,
                        stage_index=getattr(exc, 'index', None))


def _differs(field, sent, stored):
    if field == 'groups':
        try:
            return int(sent) != int(stored)
        except (TypeError, ValueError):
            return True
    fmt = formats.get(str(sent))
    return (fmt.key if fmt else str(sent)) != stored
