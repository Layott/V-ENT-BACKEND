"""The two kinds of record an embed shows, asked the same questions (P8).

A tournament and an event are embedded the same way (inbox 360 built both), so
the approved-site rules are written once here: which record an address means,
who may set its sites, and which sites may frame it. Every lookup reads the
slug history, so a snippet pasted before a rename keeps its lock.
"""
from . import hosts

KINDS = ('tournament', 'event')


def find(kind, ref, *, public=True):
    """The record `ref` names, or None. `public` leaves out a draft tournament."""
    if kind == 'tournament':
        from vent_tournament.lookup import find as find_tournament
        record = find_tournament(ref)
        if record is not None and public and record.is_draft:
            return None
        return record
    if kind == 'event':
        from vent_event.refs import event_by_ref
        return event_by_ref(ref)
    return None


def may_manage(user, kind, record):
    """The people who run it: the tournament's organiser or an admin; the
    event's creator, a named manager, its organisation's events people or an
    admin. The same questions every other console door asks."""
    if user is None or record is None:
        return False
    if kind == 'tournament':
        from vent_tournament.access import may_manage as may_manage_tournament
        return may_manage_tournament(user, record)
    from vent_event.permissions import may_run_event
    return may_run_event(user, record)


def target(kind, record):
    """The filter that picks this record's rows: {'tournament': t} or {'event': e}."""
    return {kind: record}


def locked_sites(kind, record):
    from .models import Site
    return list(Site.objects.filter(**{'locks__%s' % kind: record}).distinct().order_by('host'))


def frame_ancestors(kind, record):
    """Who may frame this record. [] means anywhere, as before P8; otherwise
    V-ENT itself (the console preview) and the proven sites it is locked to."""
    sources = hosts.ancestors(locked_sites(kind, record))
    return ["'self'"] + sources if sources else []
