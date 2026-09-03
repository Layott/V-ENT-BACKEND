"""Sides assembled for one tournament, and entrants an organiser adds directly.

CEO, 3 September 2026:

> "there should also be a way for admins to simply just add teams to the event.
> there should also be a way we can run stuff for this event, because each
> player for team nigeria in the rivalry series is registered to a different
> team, but both nigerian players will be working together as a team for
> nigeria... so we can invite players from different orgs and then they play as
> a team on the site, while still representing their individual teams or orgs."

Two things, and they belong together because an organiser does both in the same
sitting while filling a bracket.

**Adding an entrant directly.** An invitation asks and waits. An organiser who
already knows their sixteen teams does not want to wait for sixteen people to
press accept; they want the bracket filled. So: the organiser puts an entrant
in, confirmed, and nobody is charged, because they were not asked to pay. This
is only ever the organiser's own tournament.

**A squad.** Team Nigeria is two players from two different clubs. A `Teams` row
cannot express that without lying about one thing or the other, so a squad is a
third kind of entrant that carries both facts: the side they play for here, and
the club each of them represents.

    GET    /tournament/<t>/squads/                 list
    POST   /tournament/<t>/squads/                 make one
    DELETE /tournament/<t>/squads/<id>/            remove one
    POST   /tournament/<t>/squads/<id>/members/    add a player
    DELETE /tournament/<t>/squads/<id>/members/<username>/
    POST   /tournament/<t>/squads/<id>/enter/      put it in the tournament
    POST   /tournament/<t>/entrants/               add a team or player directly
"""

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from vent_auth.models import Teams, TeamMembers, Users

from .models import (
    Tournament, TournamentRegistration, TournamentSquad, SquadMember)


def _error(message, code, http=status.HTTP_400_BAD_REQUEST, extra=None):
    return Response({'status': 'error', 'code': code, 'message': message,
                     'data': extra or {}}, status=http)


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _viewer(request):
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def _tournament(key):
    if str(key).isdigit():
        found = Tournament.objects.filter(tournament_id=int(key)).first()
        if found:
            return found
    return Tournament.objects.filter(slug=str(key)).first()


def _may_manage(user, tournament):
    """The organiser, or a platform admin who may manage tournaments."""
    from .access import may_manage
    return may_manage(user, tournament)


def _guard(request, tournament_id):
    """Every route here needs the same three answers. Returns (tournament, error)."""
    tournament = _tournament(tournament_id)
    if tournament is None:
        return None, _error('Tournament not found.', 'NOT_FOUND',
                            status.HTTP_404_NOT_FOUND)
    user = _viewer(request)
    if user is None:
        return None, _error('Sign in first.', 'AUTH_REQUIRED',
                            status.HTTP_401_UNAUTHORIZED)
    if not _may_manage(user, tournament):
        return None, _error('Only the organiser can do that.',
                            'NOT_TOURNAMENT_ORGANIZER', status.HTTP_403_FORBIDDEN)
    return tournament, None


# ---------------------------------------------------------------- who they are

def home_of(user):
    """The club or organisation a player actually belongs to.

    Their team in this game if they have one, else nothing. Read once, when
    they are added to a squad, and stored: see `SquadMember`.
    """
    membership = (TeamMembers.objects
                  .filter(user=user)
                  .select_related('team')
                  .first())
    return membership.team if membership else None


def serialize_member(member, request=None):
    from .views_overlay_feed import _url

    picture = None
    profile = getattr(member.user, 'userprofile_set', None)
    if profile is not None:
        first = profile.first()
        picture = getattr(first, 'profile_picture', None) if first else None

    return {
        'username': member.user.username,
        'full_name': member.user.full_name or '',
        'is_captain': member.is_captain,
        'img': _url(request, picture) if request is not None else None,
        # The whole point of the feature: they are in this squad AND they still
        # play for somebody.
        'represents': member.represents_name or (
            member.represents_team.team_name if member.represents_team_id else ''),
        'represents_slug': (getattr(member.represents_team, 'slug', '')
                            if member.represents_team_id else ''),
    }


def serialize_squad(squad, request=None):
    entered = TournamentRegistration.objects.filter(squad=squad).first()
    return {
        'id': squad.id,
        'name': squad.name,
        'tag': squad.tag,
        'logo': (request.build_absolute_uri(squad.logo.url)
                 if squad.logo and request is not None else None),
        'members': [serialize_member(m, request)
                    for m in squad.members.select_related('user', 'represents_team')],
        'entered': entered is not None,
        'status': entered.status if entered else None,
    }


# -------------------------------------------------------------------- squads

@api_view(['GET', 'POST'])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def squads(request, tournament_id):
    tournament, refusal = _guard(request, tournament_id)
    if refusal is not None:
        return refusal

    if request.method == 'GET':
        rows = (TournamentSquad.objects
                .filter(tournament=tournament)
                .prefetch_related('members__user', 'members__represents_team'))
        return _ok({'squads': [serialize_squad(s, request) for s in rows]})

    name = str(request.data.get('name') or '').strip()[:80]
    if not name:
        return _error('Give the squad a name.', 'VALIDATION_ERROR',
                      extra={'field': 'name'})
    if TournamentSquad.objects.filter(tournament=tournament,
                                      name__iexact=name).exists():
        return _error('There is already a squad with that name in this '
                      'tournament.', 'ALREADY_EXISTS', status.HTTP_409_CONFLICT)

    squad = TournamentSquad.objects.create(
        tournament=tournament, name=name,
        tag=str(request.data.get('tag') or '').strip()[:8].upper(),
        logo=request.FILES.get('logo'),
        created_by=_viewer(request))
    return Response({'status': 'success',
                     'data': {'squad': serialize_squad(squad, request)},
                     'message': 'Squad created.'},
                    status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
def squad_detail(request, tournament_id, squad_id):
    tournament, refusal = _guard(request, tournament_id)
    if refusal is not None:
        return refusal

    squad = TournamentSquad.objects.filter(
        tournament=tournament, id=squad_id).first()
    if squad is None:
        return _error('Squad not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    # Deleting a squad that has played would take its results with it.
    played = TournamentRegistration.objects.filter(squad=squad).exclude(
        status='withdrawn').exists()
    if played and tournament.bracket_matches.exists():
        return _error('That squad is already in the draw. Withdraw it instead.',
                      'ALREADY_IN_DRAW', status.HTTP_409_CONFLICT)

    squad.delete()
    return _ok({'removed': True}, 'Squad removed.')


@api_view(['POST'])
def squad_members(request, tournament_id, squad_id):
    tournament, refusal = _guard(request, tournament_id)
    if refusal is not None:
        return refusal

    squad = TournamentSquad.objects.filter(
        tournament=tournament, id=squad_id).first()
    if squad is None:
        return _error('Squad not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    username = str(request.data.get('username') or '').strip()
    if not username:
        return _error('Name the player.', 'VALIDATION_ERROR',
                      extra={'field': 'username'})
    player = Users.objects.filter(username__iexact=username).first()
    if player is None:
        return _error('No player by that name.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)

    if SquadMember.objects.filter(squad=squad, user=player).exists():
        return _ok({'squad': serialize_squad(squad, request), 'already': True},
                   'They are already in this squad.')

    # A player cannot be two sides in one tournament. This is the fault that
    # would otherwise appear as a bracket where somebody plays themselves.
    in_another = (SquadMember.objects
                  .filter(squad__tournament=tournament, user=player)
                  .exclude(squad=squad)
                  .select_related('squad').first())
    if in_another is not None:
        return _error('%s is already in %s in this tournament.'
                      % (player.username, in_another.squad.name),
                      'ALREADY_IN_A_SQUAD', status.HTTP_409_CONFLICT)

    already_entered = TournamentRegistration.objects.filter(
        tournament=tournament, user=player).exists()
    if already_entered and not request.data.get('anyway'):
        return _error('%s is already entered in this tournament on their own. '
                      'Add them anyway?' % player.username,
                      'ALREADY_ENTERED_ALONE', status.HTTP_409_CONFLICT)

    # And their club may be entered, which is the same problem wearing a
    # different hat. Found on production running the Rivalry Series: seat 1 of
    # the first fixture came out as "naijagameevo v naijagameevo", because they
    # were in a club that had entered AND in the squad facing it. A player
    # cannot represent two sides, and a fixture where somebody plays themselves
    # has no result.
    club = (TournamentRegistration.objects
            .filter(tournament=tournament, team__isnull=False,
                    team__teammembers__user=player)
            .select_related('team').first())
    if club is not None and not request.data.get('anyway'):
        return _error('%s already plays for %s in this tournament. Putting '
                      'them in this squad as well would have them face '
                      'themselves.' % (player.username, club.team.team_name),
                      'ALREADY_PLAYING_FOR_A_CLUB', status.HTTP_409_CONFLICT)

    home = home_of(player)
    SquadMember.objects.create(
        squad=squad, user=player,
        represents_team=home,
        represents_name=(home.team_name if home is not None else ''),
        is_captain=bool(request.data.get('captain')))
    return _ok({'squad': serialize_squad(squad, request)}, 'Added.')


@api_view(['DELETE'])
def squad_member_detail(request, tournament_id, squad_id, username):
    tournament, refusal = _guard(request, tournament_id)
    if refusal is not None:
        return refusal

    squad = TournamentSquad.objects.filter(
        tournament=tournament, id=squad_id).first()
    if squad is None:
        return _error('Squad not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    member = SquadMember.objects.filter(
        squad=squad, user__username__iexact=str(username)).first()
    if member is None:
        return _error('They are not in this squad.', 'NOT_FOUND',
                      status.HTTP_404_NOT_FOUND)
    member.delete()
    return _ok({'squad': serialize_squad(squad, request)}, 'Removed.')


@api_view(['POST'])
def squad_enter(request, tournament_id, squad_id):
    """Put the squad into the tournament, the way a club would enter."""
    tournament, refusal = _guard(request, tournament_id)
    if refusal is not None:
        return refusal

    squad = TournamentSquad.objects.filter(
        tournament=tournament, id=squad_id).first()
    if squad is None:
        return _error('Squad not found.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not squad.members.exists():
        return _error('Put somebody in the squad first.', 'EMPTY_SQUAD')

    registration, made = TournamentRegistration.objects.get_or_create(
        tournament=tournament, squad=squad,
        defaults={'status': 'confirmed'})
    if not made and registration.status != 'confirmed':
        registration.status = 'confirmed'
        registration.save(update_fields=['status'])
    return _ok({'squad': serialize_squad(squad, request), 'added': made},
               'In the tournament.' if made else 'Already in the tournament.')


# --------------------------------------------- entrants, added directly

@api_view(['POST'])
def entrants(request, tournament_id):
    """Put a team or a player straight in, without asking them.

    CEO: "there should also be a way for admins to simply just add teams to the
    event." An organiser filling a bracket from a spreadsheet has already had
    the conversation; making them wait for sixteen accept presses is asking
    them to run the tournament twice.

    Confirmed, and no entry fee is taken, because nobody was asked to pay.
    """
    tournament, refusal = _guard(request, tournament_id)
    if refusal is not None:
        return refusal

    team_ref = str(request.data.get('team') or '').strip()
    username = str(request.data.get('username') or '').strip()
    if bool(team_ref) == bool(username):
        return _error('Name either a team or a player.', 'VALIDATION_ERROR')

    if team_ref:
        team = Teams.objects.filter(team_name__iexact=team_ref).first()
        if team is None and team_ref.isdigit():
            team = Teams.objects.filter(team_id=int(team_ref)).first()
        if team is None:
            return _error('No team by that name.', 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
        target = {'team': team}
        who = team.team_name
    else:
        player = Users.objects.filter(username__iexact=username).first()
        if player is None:
            return _error('No player by that name.', 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
        target = {'user': player}
        who = player.username

    with transaction.atomic():
        registration, made = TournamentRegistration.objects.get_or_create(
            tournament=tournament, defaults={'status': 'confirmed'}, **target)
        if not made and registration.status != 'confirmed':
            registration.status = 'confirmed'
            registration.save(update_fields=['status'])

    # Already in is not an error. An organiser pasting a list twice should be
    # told what is true, not shown a red box.
    return _ok({'added': made, 'who': who,
                'registration_id': registration.id},
               '%s added.' % who if made else '%s was already in.' % who)
