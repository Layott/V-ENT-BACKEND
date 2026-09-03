"""Who may do what on a tournament, in one place.

CEO, 3 September 2026: "I also hope there will be a place to input results on
the website inside the tournament and then only those given the access to,
should be able to. Then its based off those results inputted that the
leaderboards and production and everything else gets their data."

The last sentence was already true: standings, the studio feed and the
overlays all read the recorded matches and fixtures, nothing else. What was
missing was the middle one: only the organiser could enter a result, so a
five-nation league with three pitches needed the organiser at every one of
them. A scorekeeper is somebody the organiser names, by username, who may
record results for that tournament and nothing else.

The roles, from most to least:

| role        | may                                                        |
|-------------|------------------------------------------------------------|
| organiser   | everything                                                 |
| admin       | everything the override permission allows, audited         |
| scorekeeper | record a result (a knockout score, a league fixture)       |
| nobody      | read the public page                                       |

Every view that records a result asks `may_record_results`. Every screen asks
`/tournament/<ref>/access/` what the viewer may do and renders that, so the
interface has one code path for organisers, scorekeepers and strangers alike.
"""
from vent_auth.actors import may_override


def role_of(user, tournament):
    """'organiser', 'admin', 'scorekeeper' or None, for this viewer."""
    if user is None or tournament is None:
        return None
    if tournament.tournament_creator_id == user.user_id:
        return 'organiser'
    if may_override(user, 'manage_tournaments'):
        return 'admin'
    from .models import TournamentStaff
    if TournamentStaff.objects.filter(tournament=tournament, user=user).exists():
        return 'scorekeeper'
    # An admin who may correct a score but not run the tournament is, for the
    # results desk, a scorekeeper.
    if may_override(user, 'override_match_score'):
        return 'scorekeeper'
    return None


def may_manage(user, tournament):
    return role_of(user, tournament) in ('organiser', 'admin')


def may_record_results(user, tournament):
    """The one question a result-recording view asks."""
    return role_of(user, tournament) in ('organiser', 'admin', 'scorekeeper')


def access_payload(user, tournament):
    """What the interface renders from. 200 for everybody; false for a stranger."""
    role = role_of(user, tournament)
    return {
        'role': role,
        'can_manage': role in ('organiser', 'admin'),
        'can_record_results': role in ('organiser', 'admin', 'scorekeeper'),
    }
