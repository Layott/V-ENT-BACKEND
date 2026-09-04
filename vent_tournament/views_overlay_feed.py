"""A tournament in the shape a stream overlay consumes.

CEO, 29 August 2026: could an uploaded HTML overlay have its player images, team
logos and standings filled from a live tournament here.

The answer turned on reading a real one. `champion-berserk-generation.html` from
the KON10DR pack is already built the right way and does not know it:

    window.KON = { teams: [ { tag, name, logo, players: [ {ign, id, img} ] } ] }
    window.build = function () { ...renders from KONteam(?t=TAG)... }
    <img src="konasset:/teams/AX.png">   resolved through window.ASSET_MAP

Three things, and every one of them is the thing an overlay needs to be
driveable: a data object, a render function that can be called again, and
assets addressed by name rather than by URL. The only reason that file is not
live is that the data is a literal and the assets are base64 in the same file.

So this endpoint answers in that shape. An overlay written against V-ENT reads
`window.VENT`, and one written against something else needs an adapter of about
fifteen lines. Both are demonstrated in
`V-ENT-FRONTEND/scripts/overlay-probe.mjs`.

Deliberately public and deliberately cheap: an overlay is loaded by OBS on a
machine at a venue, often over a phone hotspot, and polled every few seconds for
hours. It carries nothing that is not already on the public tournament page.
"""

from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import (
    BracketMatch, TieFixture, Tournament, TournamentRegistration,
)


def _error(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': {}}, status=http)


def _tournament(key):
    if str(key).isdigit():
        found = Tournament.objects.filter(tournament_id=int(key)).first()
        if found:
            return found
    return Tournament.objects.filter(slug=str(key)).first()


def _url(request, image):
    """An absolute URL, because the overlay is loaded by OBS, not by the site.

    A relative path resolves against whatever the overlay was served from, which
    for a file dragged into OBS is the filesystem.
    """
    if not image:
        return ''
    try:
        return request.build_absolute_uri(image.url)
    except Exception:                                       # noqa: BLE001
        return ''


def studio_assets_for(owner, kind, request):
    """What an overlay can pull out of the studio's media library.

    CEO, 3 September 2026: "should still be able to upload images and media
    that they want to be used and assign them to names or text or areas inside
    the overlays so those medias are pulled and shown inside the overlay when
    the overlays are triggered."

    Three shapes, because a designer reaches for different ones:

      `asset.<slot>`  a URL, so `data-vent-src="asset.hero"` fills a picture
                      the designer has already positioned in their own HTML.
      `assets`        a repeat, for a strip of everything uploaded.
      `pictures`      inside a player row; see `_players_of`.

    A slot is one word, typed into an attribute by hand, and the newest asset
    assigned to it wins so an organiser can replace the hero shot at 8pm by
    uploading a new one rather than by editing anything.
    """
    from .models import StudioAsset

    field = 'event' if kind == 'event' else 'tournament'
    rows = StudioAsset.objects.filter(**{field: owner}).select_related('player')

    by_slot = {}
    listed = []
    for row in rows:
        url = _url(request, row.file)
        listed.append({
            'id': row.id,
            'name': row.name,
            'kind': row.kind,
            'url': url,
            'slot': row.slot or '',
            'team_tag': row.team_tag or '',
            'player': row.player.username if row.player_id else '',
        })
        # `rows` is newest first, so the first one to claim a slot is the
        # newest and keeps it.
        if row.slot and row.slot not in by_slot:
            by_slot[row.slot] = url
    return by_slot, listed


def studio_fonts(owner, kind, request):
    """Every font uploaded to this studio, named by its slot.

    CEO, 3 September 2026, asking whether fonts should be uploaded to the
    studio or carried inside the HTML: both, and this is the studio half.

    A font gets a slot exactly as a picture does, and the runtime turns each
    one into a `@font-face` whose family IS the slot. A designer then writes

        font-family: 'hero';

    with no URL to paste and nothing to base64. The organiser decides later
    what hero is, and can change it without the designer re-exporting a file.

    The other half needs no code: a font inlined into the HTML as a data URI
    is just part of the file, always works, and is what the standalone-HTML
    rule already asks for.
    """
    from .models import StudioAsset

    field = 'event' if kind == 'event' else 'tournament'
    rows = (StudioAsset.objects
            .filter(**{field: owner}, kind='font')
            .order_by('-created_at'))

    seen = set()
    out = []
    for row in rows:
        # A slot is what a designer writes, so a font with none cannot be
        # named and is not offered. The newest wins a slot, as with pictures.
        if not row.slot or row.slot in seen:
            continue
        seen.add(row.slot)
        name = (row.file.name or '').lower()
        out.append({
            'slot': row.slot,
            'name': row.name,
            'url': _url(request, row.file),
            # The browser needs to be told the format or it will not use it.
            'format': ('woff2' if name.endswith('.woff2')
                       else 'woff' if name.endswith('.woff')
                       else 'opentype' if name.endswith('.otf')
                       else 'truetype'),
        })
    return out


def player_pictures(owner, kind, request):
    """Extra shots per player, beyond the one on their profile.

    CEO, same message: "should be able to upload more pictures for players
    apart from the ones in their profiles also." An organiser has a proper
    photograph of somebody that the player never uploaded, and a broadcast
    should use it.
    """
    from .models import StudioAsset

    field = 'event' if kind == 'event' else 'tournament'
    out = {}
    rows = (StudioAsset.objects
            .filter(**{field: owner}, kind='image')
            .exclude(player__isnull=True)
            .select_related('player'))
    for row in rows:
        out.setdefault(row.player.username.lower(), []).append(_url(request, row.file))
    return out


def lineups_for(tournament):
    """Every EAFC lineup submitted for this tournament, by player.

    A list rather than a map so an overlay can repeat over it, and each row
    carries the player it belongs to. Empty and cheap for the tournaments not
    using lineups, which is nearly all of them.
    """
    try:
        from vent_cards.models import Lineup
        from vent_cards.views import serialize_lineup
    except Exception:                                       # noqa: BLE001
        return []
    rows = (Lineup.objects.filter(tournament=tournament)
            .select_related('user').prefetch_related('slots__card'))
    return [serialize_lineup(row) for row in rows]

def player_records(tournament):
    """Each player's OWN record, by username.

    CEO's rule for the Rivalry Series: "a player can win their match while
    their country loses the fixture, and both must record it." The player card
    used to draw the player's name above their SIDE's record, so on production
    it read "Layott 0W 1L" on the day Layott won their match 2-0 and Nigeria
    lost the tie 2-3. That is the one thing the two tables exist to keep apart.

    Empty for a tournament that is not a league, where a player has no record
    of their own and the side's is the only one there is.
    """
    try:
        from .services import league
        rows = league.player_table(tournament)
    except Exception:                                       # noqa: BLE001
        # A graphic must never be taken down by a table that will not compute.
        return {}
    out = {}
    for row in rows:
        name = str(row.get('name') or '')
        if not name:
            continue
        out[name.lower()] = {
            'played': row.get('played', 0),
            'won': row.get('won', 0),
            'drawn': row.get('drawn', 0),
            'lost': row.get('lost', 0),
            'goals_for': row.get('goals_for', 0),
            'goals_against': row.get('goals_against', 0),
        }
    return out


def _players_of(registration, request, extra=None, records=None):
    """The people a registration puts on the field.

    `extra` is the studio's own pictures of them, keyed by username, so a
    broadcast can use a photograph the organiser took rather than only the
    avatar the player uploaded.
    """
    extra = extra or {}
    records = records or {}
    if registration.squad_id:
        # A side assembled for this tournament. Each player carries the club
        # they actually play for, which is the entire reason a squad exists:
        # a broadcast puts "Nigeria" on the scorebar and the player's own badge
        # on the player card.
        out = []
        members = (registration.squad.members
                   .select_related('user', 'represents_team'))
        for member in members:
            user = member.user
            if user is None:
                continue
            profile = getattr(user, 'userprofile_set', None)
            picture = None
            if profile is not None:
                first = profile.first()
                picture = getattr(first, 'profile_picture', None) if first else None
            shots = extra.get((user.username or '').lower(), [])
            out.append({
                'ign': user.username,
                'id': str(user.user_id),
                'img': _url(request, picture) or (shots[0] if shots else None),
                'pictures': shots,
                'record': records.get((user.username or '').lower())
                          or {'played': 0, 'won': 0, 'drawn': 0, 'lost': 0, 'goals_for': 0, 'goals_against': 0},
                'represents': member.represents_name or (
                    member.represents_team.team_name
                    if member.represents_team_id else ''),
                'represents_logo': (
                    _url(request, member.represents_team.team_logo)
                    if member.represents_team_id else None),
                'is_captain': member.is_captain,
            })
        return out

    if registration.team_id:
        from vent_auth.models import TeamMembers

        rows = (TeamMembers.objects
                .filter(team=registration.team)
                .select_related('user'))
        out = []
        for row in rows:
            user = row.user
            if user is None:
                continue
            profile = getattr(user, 'userprofile_set', None)
            picture = None
            if profile is not None:
                first = profile.first()
                picture = getattr(first, 'profile_picture', None) if first else None
            shots = extra.get((user.username or '').lower(), [])
            out.append({
                'ign': user.username,
                'id': str(user.user_id),
                # The profile picture, or the studio's own if the player
                # never uploaded one.
                'img': _url(request, picture) or (shots[0] if shots else None),
                'pictures': shots,
                # A club's player represents that club. Carried on every player
                # row and not only on a squad's, because a name that is present
                # on some rows and absent on others fills with '' silently in a
                # repeat, which is the fault this vocabulary exists to prevent.
                'record': records.get((user.username or '').lower())
                          or {'played': 0, 'won': 0, 'drawn': 0, 'lost': 0, 'goals_for': 0, 'goals_against': 0},
                'represents': registration.team.team_name,
                'represents_logo': _url(request, registration.team.team_logo),
                'is_captain': bool(getattr(row, 'is_captain', False)),
            })
        return out

    if registration.user_id:
        user = registration.user
        return [{
            'ign': user.username,
            'id': str(user.user_id),
            'img': '',
            'pictures': [],
            # Entering alone, so they represent nobody but themselves.
            'record': records.get((user.username or '').lower())
                      or {'played': 0, 'won': 0, 'drawn': 0, 'lost': 0, 'goals_for': 0, 'goals_against': 0},
            'represents': '',
            'represents_logo': None,
            'is_captain': False,
        }]
    return []


def side_identity(registration, request):
    """What a side is called, whichever of the three kinds it is.

    One definition, because a fixture card, the standings and the scorebar all
    name the same nation and two of them reading it from different branches is
    how "Nigeria" becomes "NIGERI" on one graphic and "Team Nigeria" on the
    next. Returns None for a registration that is none of the three, which the
    callers treat as a bye.
    """
    if registration is None:
        return None
    if registration.squad_id:
        squad = registration.squad
        return {
            'tag': (squad.tag or squad.name or '')[:6].upper().replace(' ', ''),
            'name': squad.name,
            'logo': _url(request, squad.logo),
        }
    if registration.team_id:
        name = registration.team.team_name or ''
        return {
            'tag': name[:6].upper().replace(' ', ''),
            'name': name,
            'logo': _url(request, registration.team.team_logo),
        }
    if registration.user_id:
        user = registration.user
        return {
            'tag': (user.username or '')[:6].upper(),
            'name': user.full_name or user.username,
            'logo': '',
        }
    return None


def _standings(tournament):
    """Wins and losses per registration, from the bracket.

    Counted from the matches rather than stored, because a stored table and the
    bracket disagreeing is a scoreboard that is wrong on stream and right in the
    database, which is the worst way round.
    """
    table = {}
    matches = BracketMatch.objects.filter(
        tournament=tournament, status='completed')
    for match in matches:
        for side in (match.participant_1_id, match.participant_2_id):
            if side is None:
                continue
            row = table.setdefault(side, {'played': 0, 'won': 0, 'lost': 0,
                                          'points_for': 0, 'points_against': 0})
            row['played'] += 1
        if match.winner_id:
            table.setdefault(match.winner_id, {
                'played': 0, 'won': 0, 'lost': 0,
                'points_for': 0, 'points_against': 0})['won'] += 1
            loser = (match.participant_2_id if match.winner_id == match.participant_1_id
                     else match.participant_1_id)
            if loser:
                table.setdefault(loser, {
                    'played': 0, 'won': 0, 'lost': 0,
                    'points_for': 0, 'points_against': 0})['lost'] += 1
        if match.participant_1_id:
            row = table[match.participant_1_id]
            row['points_for'] += match.score_p1 or 0
            row['points_against'] += match.score_p2 or 0
        if match.participant_2_id:
            row = table[match.participant_2_id]
            row['points_for'] += match.score_p2 or 0
            row['points_against'] += match.score_p1 or 0
    return table


# ---------------------------------------------------------------------------
# The Rivalry Series: a fixture is two matches and one result
# ---------------------------------------------------------------------------
#
# CEO, 4 September 2026, sending the STREAM ELEMENTS tab of the event flow:
# "make sure they are intrgrate with the tournament model and show information
# based of proper stats from the tournament."
#
# Everything below reads what the platform already computes. `services.league`
# owns the aggregate and both tables, `services.bracket` owns how many seats a
# side fields and who sits in them, and `LeagueRules` owns what a win is worth.
# Nothing here adds a number of its own, because the studio disagreeing with
# the page the players are reading is the failure this whole feed exists to
# prevent, and it would first be noticed on air.

#: The block a tournament that is not an aggregate league carries. Every key
#: present and empty rather than the key absent, so an element draws its own
#: designed empty state instead of falling over a missing name.
BLANK_RIVALRY = {
    'enabled': False,
    'seats': 0,
    'fixtures': [],
    'days': [],
    'table_nations': [],
    'table_players': [],
    'now': None,
}


def _digest(raw):
    """A short, stable fingerprint of a string.

    The version is compared by an element page a few times a second on a venue
    connection, so it stays short. Counts alone will not do here: 3-0 and 2-1
    are the same total and a scorebar that did not redraw between them is
    wrong on screen with nothing to show for it.
    """
    import hashlib

    return hashlib.blake2s(raw.encode('utf-8'), digest_size=8).hexdigest()


def _fixture_points(tie, home_goals, away_goals, rules, stats):
    """What this fixture paid each side, or (0, 0) while it is still open.

    The organiser's own numbers, from the same `LeagueRules` row the table adds
    up, so the points on a result card and the points in the standings cannot
    disagree. A walkover pays what the organiser set for one, which is not
    necessarily what beating somebody is worth.
    """
    if tie.status == 'walkover_p1':
        return stats['walkover_points_winner'], stats['walkover_points_loser']
    if tie.status == 'walkover_p2':
        return stats['walkover_points_loser'], stats['walkover_points_winner']
    if tie.status != 'completed':
        return 0, 0
    if home_goals > away_goals:
        return rules.points_win, rules.points_loss
    if away_goals > home_goals:
        return rules.points_loss, rules.points_win
    return rules.points_draw, rules.points_draw


def _player_name(user):
    """The name a person is drawn under, everywhere.

    The same rule `services.league.player_table` uses, so a name on a fixture
    card is the name in the player table. Two rules would put "tolu" on one
    graphic and "Tolu Adebayo" on the next, in the same minute, on the same
    stream.
    """
    if user is None:
        return ''
    return (getattr(user, 'full_name', None)
            or getattr(user, 'username', None)
            or 'Player %s' % user.pk)


def rivalry_for(tournament, request):
    """The aggregate league, in the shape the Rivalry Series graphics draw.

    Returns `(block, stamp)`. The stamp goes into the feed's version, because
    an element page skips its redraw when the version has not moved and a
    scoreline is exactly the thing that must never be one poll stale.
    """
    from .services import bracket as bracket_service
    from .services import league

    # What makes a tournament an aggregate one: a format decided by a table,
    # whose ties are actually made of more than one match.
    #
    # The second half is read off the DRAWN TIES, not off `_seats_for`.
    # `_seats_for` reads LeagueRules and answers 1 when there is no row, which
    # is right where it is used - a format nobody configured must not silently
    # become an aggregate league, and bracket generation depends on that. But it
    # is wrong here: an organiser who set the format to aggregate and never
    # opened the league settings has ties with two seats in them and would have
    # got a blank score bar, a blank fixture card and two empty tables, on air,
    # with nothing anywhere saying why. That is the state the Rivalry Series
    # tournament was in when this was found.
    #
    # Counting the seats that exist cannot turn a plain round robin into a
    # rivalry, because a plain round robin's ties carry one fixture each or
    # none. `_seats_for` is still the answer before a draw exists, so a
    # tournament configured but not yet drawn reports itself correctly too.
    if not bracket_service.decided_by_table(tournament.bracket_type):
        return dict(BLANK_RIVALRY), ''

    drawn = (TieFixture.objects
             .filter(tie__tournament=tournament)
             .values('tie_id')
             .annotate(seats=Count('id'))
             .order_by('-seats')
             .values_list('seats', flat=True)
             .first())
    seats = drawn if drawn else bracket_service._seats_for(tournament)
    if seats < 2:
        return dict(BLANK_RIVALRY), ''

    rules = league.rules_for(tournament)
    stats = league.stat_settings(tournament)

    ties = list(
        BracketMatch.objects
        .filter(tournament=tournament)
        .select_related('participant_1__team', 'participant_1__user',
                        'participant_1__squad', 'participant_2__team',
                        'participant_2__user', 'participant_2__squad')
        .prefetch_related('fixtures__player_1', 'fixtures__player_2')
        # The organiser's running order first, which is given rather than
        # generated, then the draw. Same ordering as the running order screen,
        # so the fixture list on air is the one the desk is reading.
        .order_by('day', 'running_order', 'round_number', 'match_number'))

    # Which side each player sat for, and in which seat, read off the fixtures
    # actually drawn rather than off the roster. A player who sat seat 2 for
    # Nigeria is in seat 2 of the player table whatever order the squad was
    # built in.
    seat_of = {}
    nation_of = {}

    fixtures = []
    fingerprint = []
    now = None

    for tie in ties:
        home = side_identity(tie.participant_1, request)
        away = side_identity(tie.participant_2, request)
        if home is None or away is None:
            continue                    # a bye has one side and is not a tie

        legs_rows = sorted(tie.fixtures.all(), key=lambda f: f.slot)

        # The running aggregate while a tie is open, and the settled score once
        # it is closed. `league.aggregate` counts only completed seats, which is
        # exactly what a live scorebar should add up; `settle` writes the same
        # total onto the tie, so a walkover's notional scoreline survives here
        # rather than being recomputed as nothing.
        if tie.status == 'completed':
            home_goals, away_goals = tie.score_p1, tie.score_p2
        else:
            home_goals, away_goals = league.aggregate(tie)

        legs = []
        for leg in legs_rows:
            for user, side_name in ((leg.player_1, home['name']),
                                    (leg.player_2, away['name'])):
                if user is not None:
                    seat_of[user.pk] = leg.slot
                    nation_of[user.pk] = side_name
            legs.append({
                'seat': leg.slot,
                'home_player': _player_name(leg.player_1),
                'away_player': _player_name(leg.player_2),
                # The username as well as the name drawn, because the operator
                # types a username into a head to head payload and the player
                # rows under `teams` are keyed by it. A card matching one
                # against the other fills with nothing and looks like a design
                # that did not load.
                'home_player_username': getattr(leg.player_1, 'username', '') or '',
                'away_player_username': getattr(leg.player_2, 'username', '') or '',
                'home_score': leg.goals_1,
                'away_score': leg.goals_2,
                'status': leg.status,
            })
            fingerprint.append('%s:%s:%s-%s:%s' % (
                tie.pk, leg.slot, leg.goals_1, leg.goals_2, leg.status))

        home_points, away_points = _fixture_points(
            tie, home_goals, away_goals, rules, stats)
        decided = tie.status in ('completed', 'walkover_p1', 'walkover_p2')

        fixtures.append({
            'id': tie.pk,
            'status': tie.status,
            'home': dict(home, aggregate=home_goals),
            'away': dict(away, aggregate=away_goals),
            'legs': legs,
            'points': {'home': home_points, 'away': away_points},
            'decided': decided,
            # Which day of the series this is on, and where in that day.
            # The organiser sets both on the running order screen, and the
            # matchday card draws a day of the draw off them. Blank means
            # unscheduled, which is where every fixture starts: a card must
            # then draw its empty state rather than guess a day, because a
            # Saturday draw shown on Friday is worse on air than no card.
            'day': tie.day.isoformat() if tie.day else '',
            'running_order': tie.running_order,
        })
        fingerprint.append('%s:%s:%s-%s:%s:%s' % (
            tie.pk, tie.status, tie.score_p1, tie.score_p2,
            # A fixture moved to another day changes no score and no status, so
            # without these two the matchday card would keep drawing yesterday's
            # order until something else on the feed happened to move.
            tie.day.isoformat() if tie.day else '', tie.running_order))

        if now is None:
            live_leg = next((l for l in legs if l['status'] == 'in_progress'), None)
            if tie.status == 'in_progress' or live_leg is not None:
                # The seat being played, or the next one still to be played, so
                # a graphic left blank in the gap between two matches of the
                # same tie still knows which tie it is on.
                pending = next((l for l in legs
                                if l['status'] != 'completed'), None)
                chosen = live_leg or pending
                now = {'fixture_id': tie.pk,
                       'seat': chosen['seat'] if chosen else None}

    # The two tables, both from `services.league`, which is where every other
    # screen reads them. The nations table is joined back to the sides only for
    # the tag and the badge, which a table row has no way to know.
    identities = {}
    for reg in (tournament.registrations
                .filter(status='confirmed')
                .select_related('team', 'user', 'squad')):
        found = side_identity(reg, request)
        if found is not None:
            identities[reg.id] = found

    table_nations = []
    for row in league.team_table(tournament):
        badge = identities.get(row.get('registration_id')) or {}
        table_nations.append({
            'place': row['position'],
            'name': row['name'],
            'tag': badge.get('tag', ''),
            'logo': badge.get('logo', ''),
            'played': row['played'],
            'won': row['won'],
            'drawn': row['drawn'],
            'lost': row['lost'],
            'goals_for': row['goals_for'],
            'goals_against': row['goals_against'],
            'goal_difference': row['goal_difference'],
            'points': row['points'],
        })

    # The badge of the side a player sat for, keyed by the side's own name,
    # which is the only thing the player table carries about them. Without this
    # a player row can say which nation somebody played for and has no way to
    # show it, and the approved board draws that badge on every row.
    badge_of_side = {}
    for badge in identities.values():
        if badge.get('name') and badge.get('logo'):
            badge_of_side.setdefault(badge['name'], badge['logo'])

    table_players = []
    for row in league.player_table(tournament):
        side = nation_of.get(row.get('user_id'), '')
        table_players.append({
            'place': row['position'],
            'name': row['name'],
            'nation': side,
            'logo': badge_of_side.get(side, ''),
            'seat': seat_of.get(row.get('user_id'), 0),
            'played': row['played'],
            'won': row['won'],
            'drawn': row['drawn'],
            'lost': row['lost'],
            'goals_for': row['goals_for'],
            'goals_against': row['goals_against'],
            'goal_difference': row['goal_difference'],
            'points': row['points'],
        })

    # The days the draw is spread over, in order, numbered the way the venue
    # numbers them. Worked out here rather than in the page: a graphic that
    # counted days itself would disagree with the running order screen the
    # moment a fixture moved, and they are read side by side on the desk.
    days = []
    for stamp in sorted({f['day'] for f in fixtures if f['day']}):
        days.append({
            'date': stamp,
            'number': len(days) + 1,
            'fixtures': [f['id'] for f in fixtures if f['day'] == stamp],
        })

    block = {
        'enabled': True,
        'seats': seats,
        'fixtures': fixtures,
        'days': days,
        'table_nations': table_nations,
        'table_players': table_players,
        'now': now,
    }
    stamp = 'r%s-%s' % (len(fixtures), _digest('|'.join(fingerprint)))
    return block, stamp


# ---------------------------------------------------------------------------
# The run of show, as two cues
# ---------------------------------------------------------------------------

#: What a tournament with no run of show carries, or one whose sheet its
#: organiser has not published. Same reason as BLANK_RIVALRY: an element draws
#: its empty state rather than falling over a name that is not there.
BLANK_RUN_OF_SHOW = {
    'day_label': '',
    'time_zone': '',
    'now': None,
    'next': None,
}


def _sheet_for_tournament(tournament):
    """This tournament's run of show, or the one belonging to its event.

    A tournament inside an event is usually a segment of that event's day, and
    the organiser writes ONE run of show for the day. Reading the event's sheet
    when the tournament has none of its own is what makes the now and next
    graphic work on a stream from inside a convention without anybody having to
    type the running order twice.
    """
    from .models import RunSheet

    sheet = RunSheet.objects.filter(tournament=tournament).first()
    if sheet is not None:
        return sheet
    link = getattr(tournament, 'event_link', None)
    if link is None or link.event_id is None:
        return None
    return RunSheet.objects.filter(event_id=link.event_id).first()


def _cue(item):
    """One row of the run of show, in the shape the graphic draws.

    `HH:MM` and not an instant, exactly as `views_runsheet` serialises it. A run
    sheet's 13:39 is the clock on the wall of the venue, and converting it to
    the reader's own zone tells a caster in London the wrong time to be on air.
    """
    from .views_runsheet import _hhmm

    return {
        'starts_at': _hhmm(item.starts_at),
        'ends_at': _hhmm(item.ends_at),
        'activity': item.activity,
        'owner': item.owner,
        'match': item.match,
    }


def run_of_show_for(tournament, include_private=False):
    """What is on and what follows, from the run sheet, against the venue clock.

    Returns `(block, stamp)`.

    `include_private` is the whole of the access question and it has exactly two
    callers. The studio passes True: it is the organiser's own surface, reached
    only through a session token they hold, and a private sheet is the one they
    are most likely to be running the show from. The public overlay feed passes
    False, because that address is public and a run of show carries staff names
    and when the money is counted. A `link` sheet is private here too: "anybody
    holding the address" is not the same as "anybody who can find the
    tournament".
    """
    from datetime import timezone as _clock

    from django.utils import timezone as _tz

    from .models import RunSheet

    sheet = _sheet_for_tournament(tournament)
    if sheet is None:
        return dict(BLANK_RUN_OF_SHOW), ''
    if not include_private and sheet.visibility != RunSheet.PUBLIC:
        return dict(BLANK_RUN_OF_SHOW), ''

    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(sheet.time_zone or 'Africa/Lagos')
    except Exception:                                       # noqa: BLE001
        # A graphic must never be taken down by a zone name. UTC is wrong by
        # an hour rather than blank, and the zone is published beside it so the
        # page can say which clock it is reading.
        zone = _clock.utc

    local = _tz.now().astimezone(zone)
    block = dict(BLANK_RUN_OF_SHOW, time_zone=sheet.time_zone)

    # The day whose date is today, in the venue's zone. A day with no date is a
    # running order and not a set of moments, so it cannot answer "what is on
    # now" and is not guessed at.
    days = list(sheet.days.all())
    day = next((d for d in days if d.date == local.date()), None)
    if day is None:
        return block, _digest('%s|%s|no-day' % (sheet.id, local.date()))

    block['day_label'] = day.label

    clock = local.time()
    all_items = list(day.items.all())
    items = [i for i in all_items if i.starts_at]
    items.sort(key=lambda i: (i.starts_at, i.position, i.id))

    now_item = None
    next_item = None
    for item in items:
        if item.starts_at <= clock:
            now_item = item
        else:
            next_item = item
            break

    # A cue that has ended and has nothing after it yet is a gap, and a gap is a
    # real state on a run sheet. Saying so beats leaving the last thing that
    # happened on screen as though it were still happening.
    if now_item is not None and now_item.ends_at and clock > now_item.ends_at:
        now_item = None

    if now_item is not None:
        block['now'] = _cue(now_item)
    if next_item is not None:
        block['next'] = _cue(next_item)

    # The ids of what is on and what is next are in the stamp deliberately: the
    # sheet does not change when the clock rolls past 14:00, but the graphic
    # has to. Everything else on the day is fingerprinted too, so correcting a
    # cue mid-show reaches the screen on the next poll.
    raw = '%s|%s|%s|%s|' % (sheet.id, day.id,
                            getattr(now_item, 'id', ''),
                            getattr(next_item, 'id', ''))
    raw += '|'.join('%s:%s:%s:%s:%s:%s' % (
        i.id, i.starts_at, i.ends_at, i.activity, i.owner, i.match)
        for i in all_items)
    return block, _digest(raw)


@api_view(['GET'])
def overlay_feed(request, tournament_id):
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    # The studio's own pictures of these players, and whatever the organiser
    # assigned to a name an overlay can address.
    extra_shots = player_pictures(tournament, 'tournament', request)
    records = player_records(tournament)
    lineups = lineups_for(tournament)
    asset_slots, asset_list = studio_assets_for(tournament, 'tournament', request)
    fonts = studio_fonts(tournament, 'tournament', request)

    # The aggregate league, and the running order. Both carry a stamp of their
    # own so the version below moves when a scoreline or a cue does.
    rivalry, rivalry_stamp = rivalry_for(tournament, request)
    # False on purpose: this endpoint is public. See `run_of_show_for`, which
    # is called again by the studio with the organiser's own token behind it.
    run_of_show, run_stamp = run_of_show_for(tournament, include_private=False)

    registrations = (TournamentRegistration.objects
                     .filter(tournament=tournament)
                     .filter(Q(status='confirmed') | Q(status='pending'))
                     .select_related('team', 'user', 'squad'))

    table = _standings(tournament)

    teams = []
    for registration in registrations:
        identity = side_identity(registration, request)
        if identity is None:
            continue

        stats = table.get(registration.id, {})
        teams.append({
            # `tag` is what an overlay is pointed at with `?t=`, so it has to be
            # short, stable and unique inside one tournament.
            'tag': identity['tag'],
            'name': identity['name'],
            'logo': identity['logo'],
            'players': _players_of(registration, request, extra_shots, records),
            'played': stats.get('played', 0),
            'won': stats.get('won', 0),
            'lost': stats.get('lost', 0),
            'points_for': stats.get('points_for', 0),
            'points_against': stats.get('points_against', 0),
        })

    # Best record first, which is the order a standings overlay wants without
    # having to sort it itself.
    teams.sort(key=lambda t: (-t['won'], t['lost'], -t['points_for']))
    for place, team in enumerate(teams, start=1):
        team['place'] = place

    # Who is playing, not only what the score is. This used to carry the round,
    # the match number, the status and the score and nothing else, so the
    # bracket graphic went on air reading "R2  0 - 0" and named nobody. A
    # scoreline with no names tells an audience less than no graphic at all.
    def _side(registration):
        if registration is None:
            return ''
        return registration.entrant_name or ''

    live = [
        {
            'round': m.round_number,
            'match': m.match_number,
            'status': m.status,
            'home': _side(m.participant_1),
            'away': _side(m.participant_2),
            'score': [m.score_p1 or 0, m.score_p2 or 0],
        }
        for m in BracketMatch.objects.filter(
            tournament=tournament,
            # In progress first, then what has just been played and what is
            # next. A graphic showing ONLY matches in progress is blank for
            # most of a matchday: on a Rivalry Series day an operator puts the
            # fixture list up between ties and used to get nothing at all.
            status__in=('in_progress', 'completed', 'scheduled'))
        .select_related('participant_1__team', 'participant_1__user',
                        'participant_1__squad', 'participant_2__team',
                        'participant_2__user', 'participant_2__squad')
        .order_by('status', 'round_number', 'match_number')[:8]
    ]

    # The people who paid for the banners. The event feed carried them from
    # the day it was written and the tournament feed did not, so a sponsor
    # wall existed for one kind of thing V-ENT runs and not the other.
    sponsors = [
        {'name': s.name, 'logo': _url(request, s.logo), 'website': s.website or ''}
        for s in tournament.sponsors.all()
    ]

    return Response({'status': 'success', 'data': {
        'tournament': {
            'title': tournament.tournament_title,
            'slug': tournament.slug,
            'game': getattr(tournament.tournament_game, 'game_title', ''),
            'logo': _url(request, tournament.tournament_logo),
            'starts_at': tournament.start_date_and_time,
            # Where it is being played. Under the same name the event feed
            # already answers to, so a graphic that draws a venue does not have
            # to know which of the two it is looking at. The play area frame
            # names the room, and had nothing to name it with.
            'venue': tournament.tournament_location or '',
        },
        'teams': teams,
        # Every lineup submitted for this tournament, keyed by the player who
        # picked it, so an uploaded overlay can draw a team sheet without a
        # second request. Empty for a tournament not using lineups, which is
        # almost all of them.
        'lineups': lineups,
        'live': live,
        'sponsors': sponsors,
        # The aggregate league: fixtures with both legs, the two tables, and
        # which seat is being played. `enabled` is false and everything else is
        # empty for a tournament that is not one, so a graphic pointed at the
        # wrong tournament draws its empty state instead of nothing at all.
        'rivalry': rivalry,
        # What is on and what follows, from the run of show. Empty here unless
        # the organiser published the sheet; the studio gets it either way.
        'run_of_show': run_of_show,
        # What an uploaded overlay can pull: `asset.<slot>` for a picture the
        # designer positioned themselves, and `assets` for a strip of them.
        'asset': asset_slots,
        'assets': asset_list,
        # Fonts the organiser uploaded, each named by its slot. The
        # runtime turns these into @font-face rules.
        'fonts': fonts,
        # What a polling overlay compares to know whether to redraw. Cheaper
        # than diffing the whole payload, and it is the only thing an overlay
        # running for six hours on a hotspot should have to think about.
        # A lineup lives in another table entirely, so nothing above moves when
        # a player changes their squad. Without this the version never changed,
        # the element page's `if version === last: return` skipped every
        # redraw, and the squad depth graphic froze on the first lineup it ever
        # saw. Found on production by changing a lineup and watching the
        # overlay not follow, which is the CEO's whole ask for that graphic:
        # "updated automatically for each player".
        #
        # The count alone is not enough: swapping one card for another leaves
        # it identical. The latest `updated_at` is what actually moves.
        #
        # The last two are the Rivalry Series blocks, and they are here for the
        # same reason the lineup stamp is. Nothing above moves when a seat's
        # goals change: `teams` counts bracket wins, not tie goals, so a 3-0
        # becoming 3-1 left the version identical and every fixture card,
        # result card and standings table on air would have kept its first
        # frame for the rest of the day. A run sheet is worse again, because
        # its cue changes with the clock and not with any row at all.
        'version': '%s-%s-%s-%s-%s-%s-%s' % (
            len(teams),
            sum(t['played'] + t['points_for'] for t in teams),
            len(asset_list),
            len(lineups),
            max([str(l.get('updated_at') or '') for l in lineups] or ['']),
            rivalry_stamp,
            run_stamp),
    }, 'message': ''})


# ---------------------------------------------------------------------------
# The same feed, for an event
# ---------------------------------------------------------------------------
#
# An event overlay is pointed at this by `serve_overlay`. Without it the
# runtime would fetch a 404 every four seconds and the overlay would sit on
# screen showing whatever placeholder text the designer drew - which looks
# like a working overlay with stale data rather than a broken one, and is
# therefore the worse failure.
#
# An event has no bracket. What it has is a programme, a door count, ticket
# sales and the people who paid for the banners, so those are the names.

@api_view(['GET'])
def event_overlay_feed(request, event_id):
    """GET /event/<id>/overlay-feed/ - what an event overlay fills itself from.

    Public for the same reason the overlay itself is: a browser source in OBS
    has no session and cannot sign in. Nothing here is private - it is the
    same programme and sponsor list the public event page shows.
    """
    from django.utils import timezone as _tz
    from vent_event.models import Event, EventSession, Sponsor, Ticket

    def _find(key):
        if str(key).isdigit():
            found = Event.objects.filter(event_id=int(key)).first()
            if found:
                return found
        return Event.objects.filter(slug=str(key)).first()

    event = _find(event_id)
    if event is None:
        return _error('Event not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    now = _tz.now()

    # Whatever the organiser assigned to a name an overlay can address.
    asset_slots, asset_list = studio_assets_for(event, 'event', request)
    fonts = studio_fonts(event, 'event', request)

    # What is on, and what follows it. Read from the programme rather than
    # typed by an operator, so a screen behind a stage cannot disagree with the
    # schedule the audience is holding.
    #
    # `stage` is the model's name for the room. An earlier draft of this read
    # `.room`, which does not exist, so every overlay would have shown an empty
    # room for ever and looked like a design problem rather than a typo.
    sessions = list(EventSession.objects.filter(
        event=event, is_published=True).order_by('starts_at'))
    now_on = next((s for s in sessions
                   if s.starts_at and s.ends_at
                   and s.starts_at <= now <= s.ends_at), None)
    next_on = next((s for s in sessions if s.starts_at and s.starts_at > now), None)

    sold = Ticket.objects.filter(event=event).exclude(
        status__in=('cancelled', 'refunded')).count()
    attending = Ticket.objects.filter(
        event=event, checked_in_at__isnull=False).count()

    sponsors = [
        {'name': s.name, 'logo': _url(request, s.logo)}
        for s in Sponsor.objects.filter(event=event)
    ]
    programme = [
        {
            'title': s.title,
            'room': s.stage or '',
            'speaker': s.description or '',
            'starts_at': s.starts_at,
            'ends_at': s.ends_at,
        }
        for s in sessions
    ]

    return Response({'status': 'success', 'data': {
        # Nested under `event` for the same reason a tournament feed nests
        # under `tournament`: the runtime resolves a dotted path against a
        # named root, and a flat key at the top level resolves to nothing.
        'event': {
            'name': event.name,
            'venue': event.venue_name or event.location or '',
            'starts_at': event.start_date,
            'now_on': getattr(now_on, 'title', '') or '',
            'room': getattr(now_on, 'stage', '') or '',
            'next_on': getattr(next_on, 'title', '') or '',
            'next_room': getattr(next_on, 'stage', '') or '',
            'attending': attending,
            'tickets_sold': sold,
            'capacity': getattr(event, 'capacity', 0) or 0,
        },
        'programme': programme,
        'sponsors': sponsors,
        'asset': asset_slots,
        'assets': asset_list,
        # Fonts the organiser uploaded, each named by its slot. The
        # runtime turns these into @font-face rules.
        'fonts': fonts,
        # What a polling overlay compares to know whether to redraw. Without
        # it every poll after the first sees `undefined === undefined`, decides
        # nothing moved, and the overlay freezes at its first frame for the
        # rest of the broadcast.
        'version': '%s-%s-%s-%s-%s' % (len(asset_list),
            len(programme), len(sponsors), attending, sold),
    }, 'message': 'Overlay feed'})
