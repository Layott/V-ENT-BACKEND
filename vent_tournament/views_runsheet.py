"""The run of show: the API behind the programme flow.

    GET    /event/<e>/run-of-show/            read it, as whoever is asking
    POST   /event/<e>/run-of-show/            create it, or change its settings
    DELETE /event/<e>/run-of-show/            remove it
    POST   /event/<e>/run-of-show/import/     an xlsx file, or pasted CSV/TSV
    POST   /event/<e>/run-of-show/days/       add a day
    PATCH  /event/<e>/run-of-show/days/<d>/   rename or redate one
    DELETE /event/<e>/run-of-show/days/<d>/   remove one
    POST   /event/<e>/run-of-show/items/      add a cue
    PATCH  /event/<e>/run-of-show/items/<i>/  correct one
    DELETE /event/<e>/run-of-show/items/<i>/  remove one
    GET    /run-of-show/<token>/              the share address

Every route also exists under `/tournament/<t>/`, from this same module. V-ENT
runs two kinds of thing and a document built for one of them is a feature half
the platform does not have.

## Three readers, one payload

| Who | Sees |
|---|---|
| the organiser | everything, at any visibility |
| anybody, on a `public` sheet | the running order, and the owners and notes only if the organiser said so |
| anybody holding the token, on a `link` sheet | the same |

A `private` sheet answers **404** to everybody else, not 403. A 403 confirms
that a run of show exists for that event, which is itself the thing being kept
private.

## What the screen is NOT asked to work out

The payload carries, per day, the date and each cue as `HH:MM` plus the zone
the sheet is written on. It does not carry a UTC instant per cue, because a run
sheet's 13:39 is the clock on the wall of the venue, and converting it to the
reader's own zone tells a caster in London the wrong time to be on air.
"""
from datetime import datetime

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import runsheet_import
from .models import RunSheet, RunSheetDay, RunSheetItem
from .production_access import (
    REFUSAL_CODE, find_owner, kind_of, may_run_production, viewer as _viewer)

#: An xlsx of a run of show is tens of kilobytes. A megabyte is already a sheet
#: with pictures pasted into it, which imports fine but is worth a ceiling.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_PASTE_CHARS = 400_000


def _ok(data, message=''):
    return Response({'status': 'success', 'data': data, 'message': message})


def _err(message, code, http=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http)


def _gone():
    """What a reader who may not see this sheet is told.

    404 and not 403, deliberately. "You may not see the run of show for this
    event" tells somebody there IS one, which is half of what they wanted to
    know.
    """
    return _err('No run of show here.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)


def _resolve(kind, ref):
    owner = find_owner(kind, ref)
    if owner is None:
        noun = 'Event' if kind == 'event' else 'Tournament'
        return None, _err('%s not found.' % noun, 'NOT_FOUND',
                          status.HTTP_404_NOT_FOUND)
    return owner, None


def _sheet_for(owner):
    if kind_of(owner) == 'event':
        return RunSheet.objects.filter(event=owner).first()
    return RunSheet.objects.filter(tournament=owner).first()


def _staff(request, owner):
    return may_run_production(_viewer(request), owner)


def _hhmm(value):
    return value.strftime('%H:%M') if value else None


def _item(row, staff, sheet):
    out = {
        'id': row.id,
        'phase': row.phase,
        'activity': row.activity,
        'match': row.match,
        'starts_at': _hhmm(row.starts_at),
        'ends_at': _hhmm(row.ends_at),
        'minutes': float(row.minutes) if row.minutes is not None else None,
        'is_confirmed': row.is_confirmed,
        'position': row.position,
    }
    # An organiser may publish the timings and keep the crew to themselves.
    # Withheld at the API rather than hidden in the page: a column dropped by
    # CSS is a column anybody can read.
    if staff or sheet.show_owners:
        out['owner'] = row.owner
    if staff or sheet.show_notes:
        out['note'] = row.note
    return out


def _day(day, staff, sheet):
    return {
        'id': day.id,
        'label': day.label,
        'date': day.date.isoformat() if day.date else None,
        'note': day.note,
        'position': day.position,
        'items': [_item(i, staff, sheet) for i in day.items.all()],
    }


def _owner_summary(owner):
    if kind_of(owner) == 'event':
        return {'kind': 'event', 'name': owner.name, 'slug': owner.slug,
                'ref': owner.slug or str(owner.event_id)}
    return {'kind': 'tournament', 'name': owner.tournament_title,
            'slug': owner.slug,
            'ref': owner.slug or str(owner.tournament_id)}


def serialize(sheet, staff, owner=None):
    owner = owner or sheet.owner
    return {
        'id': sheet.id,
        'name': sheet.name,
        'subtitle': sheet.subtitle,
        'visibility': sheet.visibility,
        'show_owners': sheet.show_owners,
        'show_notes': sheet.show_notes,
        'time_zone': sheet.time_zone,
        # The share address is the token, never the id. Only somebody who may
        # manage the sheet is told it: handing the token to every reader of a
        # public sheet would hand them an address that keeps working after the
        # organiser makes it private again.
        'token': sheet.token if staff else None,
        'updated_at': sheet.updated_at.isoformat(),
        'owner': _owner_summary(owner),
        'days': [_day(d, staff, sheet) for d in sheet.days.all()],
    }


def _readable(sheet, staff, by_token=False):
    """Whether this reader may see this sheet at all."""
    if staff:
        return True
    if sheet.visibility == RunSheet.PUBLIC:
        return True
    return sheet.visibility == RunSheet.LINK and by_token


def _refuse(kind):
    noun = 'event' if kind == 'event' else 'tournament'
    return _err('Only the organiser can change the run of show for this %s.'
                % noun, REFUSAL_CODE[kind], status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# The sheet itself
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([AllowAny])
def run_sheet(request, kind, ref):
    owner, err = _resolve(kind, ref)
    if err:
        return err
    staff = _staff(request, owner)
    sheet = _sheet_for(owner)

    if request.method == 'GET':
        if sheet is None:
            # Not an error. Most events have no run of show, and the console
            # needs to be able to offer making one.
            if not staff:
                return _gone()
            return _ok({'sheet': None, 'can_manage': True,
                        'owner': _owner_summary(owner)}, 'No run of show yet.')
        if not _readable(sheet, staff):
            return _gone()
        return _ok({'sheet': serialize(sheet, staff, owner),
                    'can_manage': staff}, 'Run of show')

    if not staff:
        return _refuse(kind)

    if request.method == 'DELETE':
        if sheet is None:
            return _gone()
        sheet.delete()
        return _ok({'sheet': None}, 'Run of show removed.')

    if sheet is None:
        sheet = RunSheet(created_by=_viewer(request))
        if kind_of(owner) == 'event':
            sheet.event = owner
        else:
            sheet.tournament = owner
        sheet.name = str(request.data.get('name') or '')[:140]
        sheet.save()

    changed = []
    if 'name' in request.data:
        sheet.name = str(request.data.get('name') or '')[:140]
        changed.append('name')
    if 'subtitle' in request.data:
        sheet.subtitle = str(request.data.get('subtitle') or '')[:240]
        changed.append('subtitle')
    if 'visibility' in request.data:
        wanted = str(request.data.get('visibility') or '').strip()
        if wanted not in dict(RunSheet.VISIBILITY):
            return _err('That is not one of the sharing settings.',
                        'INVALID_VISIBILITY', field='visibility')
        sheet.visibility = wanted
        changed.append('visibility')
    if 'show_owners' in request.data:
        sheet.show_owners = request.data.get('show_owners') is not False
        changed.append('show_owners')
    if 'show_notes' in request.data:
        sheet.show_notes = request.data.get('show_notes') is True
        changed.append('show_notes')
    if 'time_zone' in request.data:
        sheet.time_zone = str(request.data.get('time_zone') or
                              'Africa/Lagos')[:64]
        changed.append('time_zone')

    if changed:
        sheet.save(update_fields=changed + ['updated_at'])
    return _ok({'sheet': serialize(sheet, True, owner), 'can_manage': True},
               'Run of show saved.')


@api_view(['GET'])
@permission_classes([AllowAny])
def by_token(request, token):
    """The share address. Opens signed out, on any device, for anybody holding it."""
    sheet = RunSheet.objects.filter(token=str(token)).first()
    if sheet is None:
        return _gone()
    owner = sheet.owner
    if owner is None:
        return _gone()
    staff = _staff(request, owner)
    if not _readable(sheet, staff, by_token=True):
        return _gone()
    return _ok({'sheet': serialize(sheet, staff, owner), 'can_manage': staff},
               'Run of show')


# ---------------------------------------------------------------------------
# Getting the organiser's spreadsheet in
# ---------------------------------------------------------------------------

@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def import_run_sheet(request, kind, ref):
    """An xlsx, or pasted CSV/TSV.

    `mode=replace` clears the sheet first and is the default, because the
    normal act is "here is the current version" and an import that silently
    doubled a 161 row sheet would be found on air. `mode=append` adds days
    beside what is there, for a day that arrives on its own.
    """
    owner, err = _resolve(kind, ref)
    if err:
        return err
    if not _staff(request, owner):
        return _refuse(kind)

    mode = str(request.data.get('mode') or 'replace').strip().lower()
    if mode not in ('replace', 'append'):
        return _err('Import mode has to be replace or append.',
                    'INVALID_MODE', field='mode')

    upload = request.FILES.get('file')
    days = None

    if upload is not None:
        if upload.size > MAX_UPLOAD_BYTES:
            return _err('That file is too big. The limit is 4MB.',
                        'FILE_TOO_LARGE', field='file')
        name = (upload.name or '').lower()
        data = upload.read()
        if name.endswith('.xlsx') or name.endswith('.xlsm'):
            try:
                days = runsheet_import.from_workbook(data)
            except Exception:
                return _err('That file could not be read as a spreadsheet.',
                            'UNREADABLE_SPREADSHEET', field='file')
        elif name.endswith('.csv') or name.endswith('.tsv') or name.endswith('.txt'):
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                try:
                    text = data.decode('latin-1')
                except Exception:
                    return _err('That file could not be read as text.',
                                'UNREADABLE_TEXT', field='file')
            one = runsheet_import.from_text(
                text, str(request.data.get('label') or 'Day 1'))
            days = [one] if one else []
        else:
            return _err('Upload an .xlsx or a .csv.', 'UNSUPPORTED_FILE',
                        field='file')
    else:
        text = str(request.data.get('text') or '')
        if not text.strip():
            return _err('Paste the rows, or choose a file.', 'NOTHING_TO_IMPORT',
                        field='text')
        if len(text) > MAX_PASTE_CHARS:
            return _err('That is more than one run of show. Split it up.',
                        'PASTE_TOO_LARGE', field='text')
        one = runsheet_import.from_text(
            text, str(request.data.get('label') or 'Day 1'))
        days = [one] if one else []

    days = [d for d in (days or []) if d and d['items']]
    if not days:
        return _err('Nothing in there looked like a running order. A run of '
                    'show needs a column of activities, and usually a start '
                    'time beside each one.', 'NO_ROWS_FOUND')

    sheet = _sheet_for(owner)
    with transaction.atomic():
        if sheet is None:
            sheet = RunSheet(created_by=_viewer(request))
            if kind_of(owner) == 'event':
                sheet.event = owner
            else:
                sheet.tournament = owner
            sheet.name = _owner_summary(owner)['name'] or 'Run of show'
            sheet.save()

        if mode == 'replace':
            sheet.days.all().delete()

        start_at = sheet.days.count()
        for offset, day in enumerate(days):
            row = RunSheetDay.objects.create(
                sheet=sheet, label=day['label'], date=day['date'],
                note=day['note'], position=start_at + offset)
            RunSheetItem.objects.bulk_create([
                RunSheetItem(day=row, **item) for item in day['items']])

        if not sheet.subtitle and days[0].get('note'):
            sheet.subtitle = days[0]['note'][:240]
            sheet.save(update_fields=['subtitle', 'updated_at'])

    imported = sum(len(d['items']) for d in days)
    return _ok({'sheet': serialize(sheet, True, owner), 'can_manage': True,
                'imported_days': len(days), 'imported_items': imported},
               'Imported.')


# ---------------------------------------------------------------------------
# Days and cues, one at a time
# ---------------------------------------------------------------------------

def _sheet_or_refuse(request, kind, ref, create=False):
    owner, err = _resolve(kind, ref)
    if err:
        return None, None, err
    if not _staff(request, owner):
        return None, None, _refuse(kind)
    sheet = _sheet_for(owner)
    if sheet is None:
        if not create:
            return None, None, _gone()
        sheet = RunSheet(created_by=_viewer(request),
                         name=_owner_summary(owner)['name'] or 'Run of show')
        if kind_of(owner) == 'event':
            sheet.event = owner
        else:
            sheet.tournament = owner
        sheet.save()
    return sheet, owner, None


@api_view(['POST'])
def days(request, kind, ref):
    sheet, owner, err = _sheet_or_refuse(request, kind, ref, create=True)
    if err:
        return err
    label = str(request.data.get('label') or '').strip()
    if not label:
        label = 'Day %s' % (sheet.days.count() + 1)
    day_date, err = _read_date(request.data.get('date'))
    if err:
        return err
    RunSheetDay.objects.create(
        sheet=sheet, label=label[:80], date=day_date,
        note=str(request.data.get('note') or '')[:240],
        position=sheet.days.count())
    return _ok({'sheet': serialize(sheet, True, owner), 'can_manage': True},
               'Day added.')


def _read_date(raw):
    if raw in (None, ''):
        return None, None
    try:
        return datetime.strptime(str(raw)[:10], '%Y-%m-%d').date(), None
    except ValueError:
        return None, _err('That date could not be read.', 'INVALID_DATE',
                          field='date')


@api_view(['PATCH', 'DELETE'])
def day_detail(request, kind, ref, day_id):
    sheet, owner, err = _sheet_or_refuse(request, kind, ref)
    if err:
        return err
    day = sheet.days.filter(pk=day_id).first()
    if day is None:
        return _gone()

    if request.method == 'DELETE':
        day.delete()
        return _ok({'sheet': serialize(sheet, True, owner), 'can_manage': True},
                   'Day removed.')

    changed = []
    if 'label' in request.data:
        label = str(request.data.get('label') or '').strip()
        if not label:
            return _err('A day needs a name.', 'VALIDATION_FAILED',
                        field='label')
        day.label = label[:80]
        changed.append('label')
    if 'date' in request.data:
        day_date, err = _read_date(request.data.get('date'))
        if err:
            return err
        day.date = day_date
        changed.append('date')
    if 'note' in request.data:
        day.note = str(request.data.get('note') or '')[:240]
        changed.append('note')
    if not changed:
        return _err('Nothing to change.', 'NO_FIELDS_TO_UPDATE')
    day.save(update_fields=changed)
    return _ok({'sheet': serialize(sheet, True, owner), 'can_manage': True},
               'Day saved.')


def _read_time(raw, field):
    if raw in (None, ''):
        return None, None
    parsed = runsheet_import._as_time(raw)
    if parsed is None:
        return None, _err('That time could not be read. Write it as 14:30.',
                          'INVALID_TIME', field=field)
    return parsed, None


def _apply_item_fields(row, data):
    """The fields a cue carries, read off a payload. Shared by create and edit."""
    if 'phase' in data:
        row.phase = str(data.get('phase') or '')[:80]
    if 'activity' in data:
        row.activity = str(data.get('activity') or '').strip()[:400]
    if 'owner' in data:
        row.owner = str(data.get('owner') or '')[:120]
    if 'match' in data:
        row.match = str(data.get('match') or '')[:120]
    if 'note' in data:
        row.note = str(data.get('note') or '')[:2000]
    if 'is_confirmed' in data:
        row.is_confirmed = data.get('is_confirmed') is not False
    for field in ('starts_at', 'ends_at'):
        if field in data:
            value, err = _read_time(data.get(field), field)
            if err:
                return err
            setattr(row, field, value)
    if 'minutes' in data:
        row.minutes = runsheet_import._as_minutes(data.get('minutes'))
    elif row.minutes is None:
        row.minutes = runsheet_import._minutes_between(row.starts_at,
                                                       row.ends_at)
    return None


@api_view(['POST'])
def items(request, kind, ref):
    sheet, owner, err = _sheet_or_refuse(request, kind, ref, create=True)
    if err:
        return err

    day = None
    if request.data.get('day_id'):
        day = sheet.days.filter(pk=request.data.get('day_id')).first()
        if day is None:
            return _gone()
    if day is None:
        day = sheet.days.first()
    if day is None:
        day = RunSheetDay.objects.create(sheet=sheet, label='Day 1', position=0)

    activity = str(request.data.get('activity') or '').strip()
    if not activity:
        return _err('Say what happens.', 'VALIDATION_FAILED', field='activity')

    row = RunSheetItem(day=day, activity=activity[:400],
                       position=day.items.count())
    err = _apply_item_fields(row, request.data)
    if err:
        return err
    row.save()
    return _ok({'sheet': serialize(sheet, True, owner), 'can_manage': True},
               'Added to the run of show.')


@api_view(['PATCH', 'DELETE'])
def item_detail(request, kind, ref, item_id):
    sheet, owner, err = _sheet_or_refuse(request, kind, ref)
    if err:
        return err
    row = RunSheetItem.objects.filter(pk=item_id, day__sheet=sheet).first()
    if row is None:
        return _gone()

    if request.method == 'DELETE':
        row.delete()
        return _ok({'sheet': serialize(sheet, True, owner), 'can_manage': True},
                   'Removed.')

    if 'activity' in request.data and not str(
            request.data.get('activity') or '').strip():
        return _err('Say what happens.', 'VALIDATION_FAILED', field='activity')

    err = _apply_item_fields(row, request.data)
    if err:
        return err
    row.save()
    return _ok({'sheet': serialize(sheet, True, owner), 'can_manage': True},
               'Saved.')


# ---------------------------------------------------------------------------
# The two mounts
# ---------------------------------------------------------------------------
#
# Django's URL conf names a function, and each kind needs its own entry point,
# so every view above gets a pair of one line adapters.
#
# **They have to carry `csrf_exempt`, and that is not cosmetic.** `@api_view`
# sets `csrf_exempt` on the function it returns; a bare `def` that merely CALLS
# that function does not inherit it, so `CsrfViewMiddleware` runs against the
# adapter and refuses every POST, PATCH and DELETE with "CSRF cookie not set"
# before any of this module's code is reached. Reading worked, because GET is
# not checked, so the fault looks like "saving is broken" rather than like a
# routing mistake.
#
# Found by pressing the import button rather than by reading the code, which is
# the whole of section 5 of the UI rule.

from django.views.decorators.csrf import csrf_exempt


def _mount(view, kind):
    """One kind's entry point for `view`, keeping DRF's exemptions."""
    @csrf_exempt
    def adapter(request, *args, **kwargs):
        first = kwargs.pop('event_id', None) or kwargs.pop('tournament_id', None)
        return view(request, kind, first, *args, **kwargs)
    adapter.__name__ = '%s_%s' % (kind, getattr(view, '__name__', 'view'))
    return adapter


event_run_sheet = _mount(run_sheet, 'event')
event_import = _mount(import_run_sheet, 'event')
event_days = _mount(days, 'event')
event_day_detail = _mount(day_detail, 'event')
event_items = _mount(items, 'event')
event_item_detail = _mount(item_detail, 'event')

tournament_run_sheet = _mount(run_sheet, 'tournament')
tournament_import = _mount(import_run_sheet, 'tournament')
tournament_days = _mount(days, 'tournament')
tournament_day_detail = _mount(day_detail, 'tournament')
tournament_items = _mount(items, 'tournament')
tournament_item_detail = _mount(item_detail, 'tournament')
