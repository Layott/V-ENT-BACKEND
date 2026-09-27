"""One description of a bracket match, for every screen that draws one.

The public bracket built its own dict and knew two kinds of entrant, a team and
a lone player, so a squad came back as nothing; it also dropped which side of
a double elimination a match was on, so the losers bracket was drawn as more
of the winners bracket. Every screen reads this now: the public bracket, the
console, the match page.

What is private stays private here, decided once: the room code and password
go only to the two sides and to staff, because a code on a public page is an
invitation to walk into somebody else's match.
"""


def entrant(reg):
    if reg is None:
        return None
    return {
        'registration_id': reg.id,
        'type': reg.entrant_kind,
        'id': reg.entrant_id,
        'name': reg.entrant_name or None,
        'seed': reg.seed,
    }


def match_row(m, *, private=False):
    row = {
        'match_id': m.id,
        'stage_id': m.stage_id,
        'group_number': m.group_number,
        'round_number': m.round_number,
        'match_number': m.match_number,
        'bracket_side': m.bracket_side,
        'is_final': m.is_final,
        'participant_1': entrant(m.participant_1),
        'participant_2': entrant(m.participant_2),
        'winner': entrant(m.winner),
        'winner_registration_id': m.winner_id,
        'score_p1': m.score_p1,
        'score_p2': m.score_p2,
        'penalties_p1': m.penalties_p1,
        'penalties_p2': m.penalties_p2,
        'games': m.games or [],
        'best_of': m.best_of,
        'legs': m.legs,
        'draw_allowed': m.draw_allowed,
        'status': m.status,
        'forfeit_reason': m.forfeit_reason,
        'scheduled_at': m.scheduled_at,
        'completed_at': m.completed_at,
        'check_in': {
            'deadline': m.check_in_deadline,
            'p1_at': m.checked_in_p1_at,
            'p2_at': m.checked_in_p2_at,
        },
        # Whether there is a room yet, which a stranger may know; never what
        # it is.
        'has_room': bool(m.room_code),
    }
    if private:
        row['room_code'] = m.room_code
        row['room_password'] = m.room_password
    return row


def rounds_of(matches, *, private_for=None):
    """[{round, bracket_side, group_number, matches}] in playing order."""
    buckets = {}
    for m in matches:
        key = (m.group_number or 0, {'winners': 0, 'losers': 1, 'grand_final': 2}
               .get(m.bracket_side, 0), m.round_number)
        buckets.setdefault(key, []).append(m)
    out = []
    for key in sorted(buckets):
        group, _side, rnd = key
        ms = sorted(buckets[key], key=lambda x: x.match_number)
        out.append({
            'round': rnd,
            'bracket_side': ms[0].bracket_side,
            'group_number': group or None,
            'matches': [match_row(x, private=bool(private_for and private_for(x)))
                        for x in ms],
        })
    return out


def select_related(qs):
    return qs.select_related(
        'participant_1__team', 'participant_1__user', 'participant_1__squad',
        'participant_2__team', 'participant_2__user', 'participant_2__squad',
        'winner__team', 'winner__user', 'winner__squad', 'stage')
