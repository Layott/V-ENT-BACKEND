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

from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import BracketMatch, Tournament, TournamentRegistration


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


def _players_of(registration, request, extra=None):
    """The people a registration puts on the field.

    `extra` is the studio's own pictures of them, keyed by username, so a
    broadcast can use a photograph the organiser took rather than only the
    avatar the player uploaded.
    """
    extra = extra or {}
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
            })
        return out

    if registration.user_id:
        user = registration.user
        return [{'ign': user.username, 'id': str(user.user_id), 'img': ''}]
    return []


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


@api_view(['GET'])
def overlay_feed(request, tournament_id):
    tournament = _tournament(tournament_id)
    if tournament is None:
        return _error('Tournament not found.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    # The studio's own pictures of these players, and whatever the organiser
    # assigned to a name an overlay can address.
    extra_shots = player_pictures(tournament, 'tournament', request)
    asset_slots, asset_list = studio_assets_for(tournament, 'tournament', request)

    registrations = (TournamentRegistration.objects
                     .filter(tournament=tournament)
                     .filter(Q(status='confirmed') | Q(status='pending'))
                     .select_related('team', 'user'))

    table = _standings(tournament)

    teams = []
    for registration in registrations:
        if registration.team_id:
            tag = (registration.team.team_name or '')[:6].upper().replace(' ', '')
            name = registration.team.team_name
            logo = _url(request, registration.team.team_logo)
        elif registration.user_id:
            tag = (registration.user.username or '')[:6].upper()
            name = registration.user.full_name or registration.user.username
            logo = ''
        else:
            continue

        stats = table.get(registration.id, {})
        teams.append({
            # `tag` is what an overlay is pointed at with `?t=`, so it has to be
            # short, stable and unique inside one tournament.
            'tag': tag,
            'name': name,
            'logo': logo,
            'players': _players_of(registration, request, extra_shots),
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
        if registration.team_id:
            return registration.team.team_name
        if registration.user_id:
            return (registration.user.full_name
                    or registration.user.username or '')
        return ''

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
            tournament=tournament, status='in_progress')
        .select_related('participant_1__team', 'participant_1__user',
                        'participant_2__team', 'participant_2__user')[:8]
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
        },
        'teams': teams,
        'live': live,
        'sponsors': sponsors,
        # What an uploaded overlay can pull: `asset.<slot>` for a picture the
        # designer positioned themselves, and `assets` for a strip of them.
        'asset': asset_slots,
        'assets': asset_list,
        # What a polling overlay compares to know whether to redraw. Cheaper
        # than diffing the whole payload, and it is the only thing an overlay
        # running for six hours on a hotspot should have to think about.
        'version': '%s-%s-%s' % (
            len(teams),
            sum(t['played'] + t['points_for'] for t in teams),
            len(asset_list)),
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
        # What a polling overlay compares to know whether to redraw. Without
        # it every poll after the first sees `undefined === undefined`, decides
        # nothing moved, and the overlay freezes at its first frame for the
        # rest of the broadcast.
        'version': '%s-%s-%s-%s-%s' % (len(asset_list),
            len(programme), len(sponsors), attending, sold),
    }, 'message': 'Overlay feed'})
