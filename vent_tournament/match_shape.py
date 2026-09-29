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


def names(reg):
    """(display name, handle) for an entrant.

    CEO, 27 September 2026, on brackets showing the username and the
    participants tab the full name: "both". A person shows their full name with
    their @username beside it; a team or squad has one name and no handle.
    """
    if reg is None:
        return None, None
    if reg.user_id and reg.user is not None:
        handle = reg.user.username
        return (reg.user.full_name or handle), handle
    return (reg.entrant_name or None), None


def entrant(reg):
    if reg is None:
        return None
    display, handle = names(reg)
    return {
        'registration_id': reg.id,
        'type': reg.entrant_kind,
        'id': reg.entrant_id,
        'name': display,
        'handle': handle,
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
        # The final place this match settles, when it settles one (a
        # stepladder rung, a match for fifth).
        'winner_place': m.winner_place,
        'loser_place': m.loser_place,
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


def place_ranges(matches):
    """{match id: (best, worst)} for every match that leads to stated places.

    A match played for fifth to eighth leads to the match for fifth and the
    match for seventh; its range is what they settle between them. Worked out
    by following the pointers, so a screen can title a section "Places 5 to 8"
    without knowing how the bracket was built. Empty for a plain knockout.
    """
    by_id = {m.id: m for m in matches}
    if not any(m.winner_place or m.loser_place for m in matches):
        return {}
    memo = {}

    def reach(m, depth=0):
        if m.id in memo:
            return memo[m.id]
        found = {p for p in (m.winner_place, m.loser_place) if p}
        if depth < 64:
            for nxt in (m.winner_to_match_id, m.loser_to_match_id):
                if nxt in by_id:
                    found |= reach(by_id[nxt], depth + 1)
        memo[m.id] = found
        return found

    out = {}
    for m in matches:
        found = reach(m)
        if found:
            out[m.id] = (min(found), max(found))
    return out


def rounds_of(matches, *, private_for=None):
    """[{round, bracket_side, group_number, matches}] in playing order."""
    ranges = place_ranges(matches)
    buckets = {}
    for m in matches:
        key = (m.group_number or 0, {'winners': 0, 'losers': 1, 'grand_final': 2,
                                     'placement': 3}
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
            'matches': [dict(match_row(x, private=bool(private_for and private_for(x))),
                             places=list(ranges[x.id]) if x.id in ranges else None)
                        for x in ms],
        })
    return out


def select_related(qs):
    return qs.select_related(
        'participant_1__team', 'participant_1__user', 'participant_1__squad',
        'participant_2__team', 'participant_2__user', 'participant_2__squad',
        'winner__team', 'winner__user', 'winner__squad', 'stage')
