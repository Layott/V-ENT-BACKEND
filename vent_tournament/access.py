"""Who may do what on a tournament, in one place.

CEO, 3 September 2026: "I also hope there will be a place to input results on
the website inside the tournament and then only those given the access to,
should be able to. Then its based off those results inputted that the
leaderboards and production and everything else gets their data."

The last sentence was already true: standings, the studio feed and the
overlays all read the recorded matches and fixtures, nothing else. What was
missing was the middle one: only the organiser could enter a result, so a
five-nation league with three pitches needed the organiser at every one of
them. A scorekeeper was somebody the organiser named who could record results
for that tournament and nothing else.

P7 (design section 16: "granular roles (structure, report, place entrants,
standings, caster)") splits the helper's job into five permissions, after
Toornament's permission attributes and Challengermode's caster role (research
section 2.13). A helper (`TournamentStaff`) holds any of them:

| permission | runs                                                                 |
|------------|----------------------------------------------------------------------|
| structure  | what the tournament is made of: stages, formats, match rules, battle  |
|            | royale lobbies and stage settings                                    |
| place      | who goes where: drawing a stage, undoing it, closing a stage and      |
|            | sending its places on, swapping and replacing entrants, a battle      |
|            | royale seat, finishing a battle royale stage, generating the bracket |
| report     | results: scores, fixtures, match times and rooms, battle royale      |
|            | results and reads, taking a result back off a match                  |
| standings  | the table and the awards: league scoring, adjustments, stat settings, |
|            | MVP metrics, match stats, the award                                  |
| caster     | production: the studio, overlays, studio assets, the run of show     |

The organiser and an admin overriding (`manage_tournaments`) hold all five, and
only they may do what is not on that list: the tournament's settings, its
entries, its money, its staff, deleting it. `may_manage` is that question.

Every door asks `may(user, tournament, '<permission>')`. Every screen asks
`/tournament/<ref>/access/` what the viewer may do and renders that, so the
interface has one code path for organisers, helpers and strangers alike.
"""
from vent_auth.actors import may_override

#: The five permissions a helper can hold, in the order every list is written.
PERMISSIONS = ('structure', 'place', 'report', 'standings', 'caster')

#: Asked of `may` for the organiser's own work.
MANAGE = 'manage'


class PermissionsError(ValueError):
    """A list of permissions that cannot be stored. `code` is translated by the screen."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def clean_permissions(raw):
    """`raw` as a stored list, in PERMISSIONS order, or PermissionsError.

    PERMISSIONS_REQUIRED for nothing at all: a helper who runs nothing is
    somebody to remove, not a row to keep. UNKNOWN_PERMISSION for anything that
    is not one of the five names.
    """
    if raw is None or (isinstance(raw, (list, tuple)) and len(raw) == 0):
        raise PermissionsError('PERMISSIONS_REQUIRED')
    if not isinstance(raw, (list, tuple)):
        raise PermissionsError('UNKNOWN_PERMISSION')
    for name in raw:
        if not isinstance(name, str) or name not in PERMISSIONS:
            raise PermissionsError('UNKNOWN_PERMISSION')
    asked = set(raw)
    return [name for name in PERMISSIONS if name in asked]


def permissions_list(held):
    """A held set as the list every payload carries, in PERMISSIONS order."""
    return [name for name in PERMISSIONS if name in held]


def _staff_row(user, tournament):
    from .models import TournamentStaff
    return TournamentStaff.objects.filter(tournament=tournament, user=user).first()


def role_of(user, tournament):
    """'organiser', 'admin', 'staff' or None, for this viewer."""
    if user is None or tournament is None:
        return None
    if tournament.tournament_creator_id == user.user_id:
        return 'organiser'
    if may_override(user, 'manage_tournaments'):
        return 'admin'
    if _staff_row(user, tournament) is not None:
        return 'staff'
    # An admin who may correct a score but not run the tournament is, for the
    # results desk, a helper holding `report`.
    if may_override(user, 'override_match_score'):
        return 'staff'
    return None


def permissions_of(user, tournament):
    """The permissions this viewer holds here, as a frozenset."""
    if user is None or tournament is None:
        return frozenset()
    if tournament.tournament_creator_id == user.user_id or may_override(user, 'manage_tournaments'):
        return frozenset(PERMISSIONS)
    held = set()
    row = _staff_row(user, tournament)
    if row is not None:
        # Read through the allowlist: a name that is not one of the five grants
        # nothing, whatever wrote it.
        held.update(name for name in (row.permissions or []) if name in PERMISSIONS)
    if may_override(user, 'override_match_score'):
        held.add('report')
    return frozenset(held)


def may(user, tournament, permission):
    """The one question a door asks. `permission` is one of PERMISSIONS, or
    MANAGE for the organiser's own work."""
    if permission == MANAGE:
        return may_manage(user, tournament)
    return permission in permissions_of(user, tournament)


def may_manage(user, tournament):
    return role_of(user, tournament) in ('organiser', 'admin')


def may_record_results(user, tournament):
    """The one question a result-recording view asks."""
    return may(user, tournament, 'report')


def plays_in(user, tournament):
    """Whether `user` plays in this tournament: a place of their own, or a place
    for a team, club or squad they are part of (`plays_for`). A withdrawn or
    disqualified place does not count.

    The lineup doors and the console ask it (16 October 2026): the lineup
    picker sat on a console that refused everybody but the people running the
    tournament, so no player could ever reach it, while the lineup door itself
    took a lineup from anybody signed in, entered or not.
    """
    if user is None or tournament is None:
        return False
    places = tournament.registrations.exclude(status__in=('withdrawn', 'disqualified'))
    if places.filter(user_id=user.user_id).exists():
        return True
    return any(reg.plays_for(user) for reg in places.exclude(user_id=user.user_id)
               .select_related('team', 'squad', 'user'))


def access_payload(user, tournament):
    """What the interface renders from. 200 for everybody; false for a stranger."""
    role = role_of(user, tournament)
    held = permissions_of(user, tournament)
    return {
        'role': role,
        'can_manage': role in ('organiser', 'admin'),
        'can_record_results': 'report' in held,
        # A player opens the console for their lineup and nothing else.
        'is_entrant': plays_in(user, tournament),
        # Which parts of the console are this viewer's, in PERMISSIONS order.
        'permissions': permissions_list(held),
    }
