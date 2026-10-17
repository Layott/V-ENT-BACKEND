"""What happened in a tournament, in one list (P1e).

CEO, 16 October 2026: "i aprove the design". Section 14b of the bracket
structure design: "One event list feeds overlays, the run of show and studios
... The same list becomes the signed webhooks for game studios in P6, so there
is one catalogue, not two."

Each fact is written from the one door it already passes through, inside the
same transaction, so a result that is rolled back leaves nothing behind:

  * result_confirmed   `results.apply`, which every door that records a result calls
  * stage_started      the stage engine, when a stage is drawn or first filled
  * stage_closed       the stage engine and `services.advance`, when a stage closes
  * line_sent          `progressions.fire`
  * tournament_won     wherever a tournament finishes

Rows are history and are never rewritten. Whether a result still stands (it
can be reopened, or its stage undrawn) is worked out when the list is read: the
match still exists and still carries the `completed_at` the row recorded.

A row carries facts (names, scores, a stage's label, which match), never a
sentence: the screen says it in the reader's language. And never who recorded
a result, because the list is public.
"""

KINDS = ('result_confirmed', 'stage_started', 'stage_closed', 'line_sent', 'tournament_won')


def record(kind, tournament_id, *, stage=None, match_id=None, **data):
    from .models import Happening
    if kind not in KINDS:
        raise ValueError(kind)
    if stage is not None:
        data.setdefault('stage', stage.label)
    return Happening.objects.create(
        tournament_id=tournament_id, kind=kind, stage=stage, match_id=match_id, data=data)


def _name(reg):
    """The name every screen shows (`match_shape.names`): a person's full
    name, a team's or squad's own. `entrant_name` gave the username, so the
    ticker read "demo_amara" beside a bracket reading "Amara Nwosu" (walk,
    17 October 2026)."""
    if reg is None:
        return None
    from .match_shape import names
    return names(reg)[0]


def result(match):
    """A result has been written onto `match` (called by `results.apply`)."""
    stage = match.stage if match.stage_id else None
    pens = None
    if match.penalties_p1 is not None and match.penalties_p2 is not None:
        pens = [match.penalties_p1, match.penalties_p2]
    record('result_confirmed', match.tournament_id, stage=stage, match_id=match.id,
           home=_name(match.participant_1), away=_name(match.participant_2),
           score='%s-%s' % (match.score_p1, match.score_p2), penalties=pens,
           winner=_name(match.winner), forfeit=match.forfeit_reason or None,
           format=stage.format if stage else match.tournament.bracket_type,
           match={'round': match.round_number, 'side': match.bracket_side,
                  'number': match.match_number, 'group': match.group_number,
                  'is_final': match.is_final, 'winner_place': match.winner_place,
                  'loser_place': match.loser_place},
           completed_at=match.completed_at.isoformat() if match.completed_at else None)


def started(stage):
    record('stage_started', stage.tournament_id, stage=stage)


def closed(stage):
    record('stage_closed', stage.tournament_id, stage=stage)


def sent(line):
    record('line_sent', line.source.tournament_id, stage=line.source,
           target=line.target.label, count=len(line.sent or []),
           place_from=line.place_from, place_to=line.place_to, group=line.group,
           side=line.side)


def won(tournament):
    champion = tournament.registrations.filter(final_position=1).first()
    record('tournament_won', tournament.pk, champion=_name(champion),
           title=tournament.tournament_title)


def rows(tournament, limit=20):
    """The latest `limit` happenings, newest first, each result saying whether
    it still stands."""
    from .models import BracketMatch, Happening
    found = list(Happening.objects.filter(tournament=tournament)
                 .select_related('stage').order_by('-id')[:limit])
    ids = [h.match_id for h in found if h.match_id]
    done = dict(BracketMatch.objects.filter(id__in=ids).values_list('id', 'completed_at'))
    out = []
    for h in found:
        data = dict(h.data or {})
        if h.stage is not None:
            data['stage'] = h.stage.label
        row = {'id': h.id, 'kind': h.kind, 'at': h.at, 'data': data}
        if h.kind == 'result_confirmed':
            at = done.get(h.match_id)
            row['standing'] = bool(at and at.isoformat() == data.get('completed_at'))
        out.append(row)
    return out


def stamp(tournament):
    """What moves when anything is added, for the overlay feed's version."""
    from .models import Happening
    last = Happening.objects.filter(tournament=tournament).order_by('-id').values_list('id', flat=True).first()
    return str(last or 0)
