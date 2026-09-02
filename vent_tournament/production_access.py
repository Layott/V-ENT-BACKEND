"""Who may run production for a thing V-ENT runs, and how to find that thing.

One answer for the studio and for the overlays, for a tournament and for an
event. There used to be three: `may_use_studio` in views_studio.py,
`_may_manage` and `_may_manage_event` in views_overlays.py. Three copies of
"is this the organiser" is three places for the answer to drift, and the
studio's copy honoured the admin override while the overlays' copies did not,
so an admin correcting a broadcast could start it and not upload to it.

When billing exists, the plan check goes here and only here.
"""
from vent_auth.models import Users


def viewer(request):
    """The account behind a Bearer token, or None."""
    header = request.headers.get('Authorization') or ''
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    return Users.objects.filter(login_session_token=token).first() if token else None


def find_owner(kind, key):
    """The tournament or event a studio or overlay route names, or None.

    Accepts an id or a slug, because the console addresses things by slug and
    older links carry the id.
    """
    if kind == 'event':
        from vent_event.models import Event
        if str(key).isdigit():
            found = Event.objects.filter(event_id=int(key)).first()
            if found:
                return found
        return Event.objects.filter(slug=str(key)).first()

    from .models import Tournament
    if str(key).isdigit():
        found = Tournament.objects.filter(tournament_id=int(key)).first()
        if found:
            return found
    return Tournament.objects.filter(slug=str(key)).first()


def kind_of(owner):
    return 'event' if hasattr(owner, 'event_id') else 'tournament'


def may_run_production(user, owner):
    """Whether this person may run a broadcast or manage overlays for `owner`.

    Ownership, or the admin override for that kind of thing. The plan check
    belongs here when plans exist; gating on a plan nobody can buy would ship a
    control that refuses everybody.
    """
    if user is None or owner is None:
        return False
    from vent_auth.actors import may_override
    if kind_of(owner) == 'event':
        if owner.creator_id == user.user_id:
            return True
        return bool(may_override(user, 'manage_events'))
    if owner.tournament_creator_id == user.user_id:
        return True
    return bool(may_override(user, 'manage_tournaments'))


# The refusal each kind raises. Distinct codes because the frontend translates
# by code, and the event wording ("or their door staff") is wrong on a
# tournament; see BE #127.
REFUSAL_CODE = {
    'event': 'NOT_ORGANIZER',
    'tournament': 'NOT_TOURNAMENT_ORGANIZER',
}
