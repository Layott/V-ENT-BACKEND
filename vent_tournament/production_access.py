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
    # The two apps' own resolvers, which read the slug history; a copy here
    # did not, so a renamed event's studio answered 404 (18 September 2026).
    if kind == 'event':
        from vent_event.refs import event_by_ref
        return event_by_ref(key)

    from .lookup import find
    return find(key)


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
        # Whoever runs the event runs its production: the creator, a named
        # manager, the organisation's events people. One rule, in
        # vent_event.permissions; this used to ask for the creator alone
        # and refused every manager the Production tab (18 September).
        from vent_event.permissions import may_run_event
        if may_run_event(user, owner):
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
