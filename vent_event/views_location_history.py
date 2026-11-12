"""Admin: where people said they were leaving from (inbox 305b, 305c, 596).

CEO, 28 September 2026: "Time limit, don't remove it ever, we always have that
info of area to attendance. Search should be flexible and we can search by
person." Built on 12 November 2026 ("you can build", inbox 596).

Three doors, all behind the `view_location_history` permission, which is its
own name and not part of the general admin role (305c):

  GET /event/admin/location-history/                        search
  GET /event/admin/location-history/person/<username>/      one person, everything
  GET /event/admin/location-history/log/                    who has looked

Search is forgiving, the same matcher as every other search on the site
(fuzzy.py): a person by username, full name or email, an event by its name or
its address, an area by its words, narrowed by the day the area was set.

Every search and every history opened needs a reason and is written to
AdminAction, with the ids of the records it put on the screen, whether it found
anything or not. "Who has looked" reads those rows back: a super admin sees
everybody's, anybody else their own, the same scope as the audit log.

Each row also says what happened at the door (checked in, held a ticket and did
not come in, or had no ticket that counts), because an area on its own says
where somebody meant to come from, and the door says whether they came.
"""
from django.db.models import Count, Max
from rest_framework.decorators import api_view
from rest_framework.response import Response

from vent_auth import fuzzy, inputs
from vent_auth.decorators import ROLE_PERMISSIONS, admin_role_required
from vent_auth.models import AdminAction, Users

from .models import DepartureRecord, Event, Ticket
from .refs import event_by_ref
from .views_together import _err, _ok, _person

PAGE_SIZE = 50
PEOPLE_SHOWN = 20
HISTORY_CAP = 1000
SEARCH_ACTION = 'search_location_history'
VIEW_ACTION = 'view_location_history'
LOOKUPS = (SEARCH_ACTION, VIEW_ACTION)

PERSON_FIELDS = ['username', 'full_name', 'email']
EVENT_FIELDS = ['name', 'slug']


def _reason(request):
    reason = inputs.read_text(request.query_params, 'reason', max_length=500)
    if len(reason) < 5:
        return None
    return reason


def _no_reason():
    return _err('Say why you are looking, for example the report number.', 'REASON_REQUIRED')


def _door(records):
    """(event_id, user_id) -> what the door saw, for the records on the screen.

    'in': a ticket was checked in (the earliest time and its gate).
    'ticket': a ticket that counts (valid) and no check-in.
    'none': no ticket, or only refunded or cancelled ones.
    """
    pairs = {(r.event_id, r.user_id) for r in records}
    if not pairs:
        return {}
    tickets = Ticket.objects.filter(event_id__in={e for e, _ in pairs},
                                    user_id__in={u for _, u in pairs})
    seen = {}
    for t in tickets.values('event_id', 'user_id', 'status', 'checked_in_at', 'checked_in_gate'):
        key = (t['event_id'], t['user_id'])
        if key not in pairs:
            continue
        door = seen.setdefault(key, {'state': 'none', 'at': None, 'gate': ''})
        if t['status'] == 'checked_in' or t['checked_in_at']:
            if door['state'] != 'in' or (t['checked_in_at'] and (door['at'] is None
                                                                  or t['checked_in_at'] < door['at'])):
                door.update(state='in', at=t['checked_in_at'], gate=t['checked_in_gate'] or '')
        elif t['status'] == 'valid' and door['state'] == 'none':
            door['state'] = 'ticket'
    return seen


def _latest_ids(records):
    """Ids of the records that are the latest area for their person and event."""
    pairs = {(r.event_id, r.user_id) for r in records}
    if not pairs:
        return set()
    rows = (DepartureRecord.objects
            .filter(event_id__in={e for e, _ in pairs}, user_id__in={u for _, u in pairs})
            .values('event_id', 'user_id').annotate(last=Max('id')))
    return {r['last'] for r in rows if (r['event_id'], r['user_id']) in pairs}


def _rows(records):
    """The one shape every door answers a record in."""
    door = _door(records)
    latest = _latest_ids(records)
    out = []
    for r in records:
        ev = r.event
        d = door.get((r.event_id, r.user_id), {'state': 'none', 'at': None, 'gate': ''})
        out.append({
            'id': r.id,
            'person': _person(r.user),
            'event': {'name': ev.name, 'slug': ev.slug, 'date': ev.event_date,
                      'location': ev.location or '', 'deleted': ev.deleted_at is not None},
            'area': r.area,
            'visibility': r.visibility,
            'set_at': r.set_at,
            'latest': r.id in latest,
            'door': d,
        })
    return out


def _log(request, action, target_model, target_id, reason, **metadata):
    AdminAction.objects.create(
        admin=request.admin_user, action_type=action, target_model=target_model,
        target_id=str(target_id or '')[:100], reason=reason[:1000], metadata=metadata)


@api_view(['GET'])
@admin_role_required(ROLE_PERMISSIONS['view_location_history'])
def location_history(request):
    """Search by person, event, area and the days the area was set.

    One of person, event or area is required (dates only narrow), and a reason.
    `from` is inclusive and `to` exclusive, both instants with their zone: the
    screen sends the start of the reader's own day, so "12 November" means
    their 12 November and not the server's.
    """
    reason = _reason(request)
    if reason is None:
        return _no_reason()
    q = request.query_params
    person = inputs.read_text(q, 'person', max_length=254).lstrip('@')
    event_ref = inputs.read_text(q, 'event', max_length=200)
    area = inputs.read_text(q, 'area', max_length=80)
    since = inputs.read_datetime(q, 'from')
    until = inputs.read_datetime(q, 'to')
    page = inputs.read_int(q, 'page', default=1, minimum=1, maximum=10000)
    if not (person or event_ref or area):
        return _err('Search by a person, an event or an area.', 'SEARCH_TERM_REQUIRED')
    if since and until and since >= until:
        return _err('The first day must come before the last.', 'DATES_OUT_OF_ORDER')

    rows = DepartureRecord.objects.select_related('event', 'user')
    people = None
    if person:
        with_records = Users.objects.filter(
            user_id__in=DepartureRecord.objects.values('user_id')).order_by('username')
        matched = fuzzy.search(with_records, person, PERSON_FIELDS, limit=PEOPLE_SHOWN)
        rows = rows.filter(user__in=matched)
        totals = dict(DepartureRecord.objects.filter(user__in=matched).values('user_id')
                      .annotate(n=Count('id')).values_list('user_id', 'n'))
        people = [{'person': _person(u), 'records': totals.get(u.user_id, 0)} for u in matched]
    if event_ref:
        exact = event_by_ref(event_ref, queryset=Event.all_objects)
        if exact:
            rows = rows.filter(event=exact)
        else:
            with_records = Event.all_objects.filter(
                event_id__in=DepartureRecord.objects.values('event_id')).order_by('-event_date')
            rows = rows.filter(event__in=fuzzy.search(with_records, event_ref, EVENT_FIELDS))
    if since:
        rows = rows.filter(set_at__gte=since)
    if until:
        rows = rows.filter(set_at__lt=until)
    rows = rows.order_by('-set_at', '-id')
    if area:
        # Best match first, so "lekki" puts Lekki above Lekki Phase 1 above a typo.
        rows = fuzzy.filter(rows, area, ['area']).select_related('event', 'user')

    total = rows.count()
    start = (page - 1) * PAGE_SIZE
    shown = list(rows[start:start + PAGE_SIZE])
    _log(request, SEARCH_ACTION, 'DepartureRecord', person or event_ref or area, reason,
         person=person, event=event_ref, area=area,
         since=since.isoformat() if since else '', until=until.isoformat() if until else '',
         page=page, results=total, shown=[r.id for r in shown])
    return _ok({
        'results': _rows(shown),
        'people': people,
        'total': total,
        'page': page,
        'page_size': PAGE_SIZE,
        'has_more': start + len(shown) < total,
    }, 'Location history')


@api_view(['GET'])
@admin_role_required(ROLE_PERMISSIONS['view_location_history'])
def person_history(request, username):
    """Every area one person ever set, every event, newest first, with the door."""
    reason = _reason(request)
    if reason is None:
        return _no_reason()
    user = Users.objects.filter(username__iexact=str(username or '').lstrip('@')).first()
    if user is None:
        return _err('Nobody has that username.', 'PERSON_NOT_FOUND', http=404)
    records = list(DepartureRecord.objects.select_related('event', 'user')
                   .filter(user=user).order_by('-set_at', '-id')[:HISTORY_CAP])
    _log(request, VIEW_ACTION, 'Users', user.username, reason,
         person=user.username, results=len(records), shown=[r.id for r in records])
    return _ok({
        'person': _person(user),
        'results': _rows(records),
        'events': len({r.event_id for r in records}),
        'total': len(records),
    }, 'Location history')


def _terms(meta):
    """What was searched for, without the empty fields; page only past the first."""
    out = {k: meta[k] for k in ('person', 'event', 'area', 'since', 'until') if meta.get(k)}
    if (meta.get('page') or 1) > 1:
        out['page'] = meta['page']
    return out


@api_view(['GET'])
@admin_role_required(ROLE_PERMISSIONS['view_location_history'])
def lookup_log(request):
    """Who has looked: every location-history search and history opened.

    A super admin sees everybody's lookups; anybody else holding the permission
    sees their own, as on the audit log.
    """
    page = inputs.read_int(request.query_params, 'page', default=1, minimum=1, maximum=10000)
    rows = AdminAction.objects.select_related('admin').filter(action_type__in=LOOKUPS)
    if getattr(request, 'admin_role', None) != 'super_admin':
        rows = rows.filter(admin=request.admin_user)
    rows = rows.order_by('-performed_at', '-id')
    total = rows.count()
    start = (page - 1) * PAGE_SIZE
    shown = list(rows[start:start + PAGE_SIZE])
    return _ok({
        'results': [{
            'id': a.id,
            'admin': _person(a.admin),
            'kind': 'history' if a.action_type == VIEW_ACTION else 'search',
            'terms': _terms(a.metadata or {}),
            'reason': a.reason,
            'results': (a.metadata or {}).get('results', 0),
            'at': a.performed_at,
        } for a in shown],
        'total': total,
        'page': page,
        'page_size': PAGE_SIZE,
        'has_more': start + len(shown) < total,
        'everyone': getattr(request, 'admin_role', None) == 'super_admin',
    }, 'Who has looked')
