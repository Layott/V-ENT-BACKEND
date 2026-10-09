"""The admin console's records: every model, every record (inbox 420).

  GET    /auth/admin/records/                          every kind of record, grouped, with counts
  GET    /auth/admin/records/search/?q=                 a forgiving search across names
  GET    /auth/admin/records/bin/                       what has been deleted and can come back
  POST   /auth/admin/records/bin/<id>/restore/          put it back
  DELETE /auth/admin/records/bin/<id>/                  throw it away for good (super admin)
  POST   /auth/admin/records/versions/<id>/revert/      undo one edit
  GET    /auth/admin/records/<model>/?q=&page=          the records of one kind
  GET    /auth/admin/records/<model>/<pk>/              one record: every field, every link, its history
  PATCH  /auth/admin/records/<model>/<pk>/              change it, with a reason
  GET    /auth/admin/records/<model>/<pk>/delete/       what deleting it would take with it
  POST   /auth/admin/records/<model>/<pk>/delete/       delete it into the bin, with a reason
  POST   /auth/admin/records/<model>/<pk>/correct/      a money correction: a new entry with a reason

Reading needs `view_records`, changing `edit_records`, purging `purge_records`,
and a correction `transfer_funds` as well, because it moves money. The rules
themselves live in records.py; these views only ask it and answer in the
envelope, with a code on every refusal.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import fuzzy, inputs, records, wallets
from .decorators import ROLE_PERMISSIONS, admin_role_required
from .models import AdminAction, RecordBin, RecordVersion

VIEW = ROLE_PERMISSIONS['view_records']
EDIT = ROLE_PERMISSIONS['edit_records']
PURGE = ROLE_PERMISSIONS['purge_records']
MONEY = ROLE_PERMISSIONS['transfer_funds']
PAGE = 50


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message},
                    status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, data=None):
    return Response({'status': 'error', 'data': data or {}, 'message': message, 'code': code},
                    status=http_status)


def _refuse(exc):
    http = {
        'NOT_FOUND': status.HTTP_404_NOT_FOUND,
        'MONEY_IS_VIEW_ONLY': status.HTTP_403_FORBIDDEN,
        'NOT_EDITABLE': status.HTTP_403_FORBIDDEN,
        'PROTECTED': status.HTTP_409_CONFLICT,
        'CASCADE_TOUCHES_MONEY': status.HTTP_409_CONFLICT,
        'PAID_ENTRANTS': status.HTTP_409_CONFLICT,
        'RESTORE_CONFLICT': status.HTTP_409_CONFLICT,
        'ALREADY_RESTORED': status.HTTP_409_CONFLICT,
        'NOT_UNIQUE': status.HTTP_409_CONFLICT,
    }.get(exc.code, status.HTTP_400_BAD_REQUEST)
    data = dict(exc.data)
    if exc.field:
        data['field'] = exc.field
    return _err(str(exc), exc.code, http, data)


def _may(request, roles):
    return request.admin_role in roles


def _find(key, pk):
    model = records.model_for(key)
    if model is None:
        return None, None
    try:
        obj = model._base_manager.filter(pk=pk).first()
    except (ValueError, TypeError):
        obj = None
    return model, obj


@api_view(['GET'])
@admin_role_required(VIEW)
def admin_records(request):
    groups = {}
    for model in records.listed_models():
        app = model._meta.app_config
        groups.setdefault(app.label, {'app': app.label, 'name': str(app.verbose_name), 'models': []})
        groups[app.label]['models'].append({
            'key': records.key_of(model),
            'name': str(model._meta.verbose_name_plural),
            'count': model._base_manager.count(),
            'money': records.is_money(model),
            'searchable': bool(records.search_fields(model)),
        })
    return _ok({
        'groups': list(groups.values()),
        'may_edit': _may(request, EDIT),
        'may_purge': _may(request, PURGE),
        'may_correct': _may(request, EDIT) and _may(request, MONEY),
    })


@api_view(['GET'])
@admin_role_required(VIEW)
def admin_records_search(request):
    """Names across every kind of record, forgiving of typos (fuzzy.py)."""
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return _err('Type at least two letters.', 'QUERY_TOO_SHORT')
    out = []
    for model in records.listed_models():
        fields = records.search_fields(model)
        if not fields:
            continue
        hits = fuzzy.filter(model._base_manager.all(), q, fields)[:5]
        rows = [records.row(o) for o in hits]
        if rows:
            out.append({'model': records.key_of(model),
                        'name': str(model._meta.verbose_name_plural), 'rows': rows})
    return _ok({'results': out, 'q': q})


@api_view(['GET'])
@admin_role_required(VIEW)
def admin_records_list(request, key):
    model = records.model_for(key)
    if model is None:
        return _err('No such kind of record.', 'UNKNOWN_MODEL', status.HTTP_404_NOT_FOUND)
    qs = model._base_manager.all()
    q = (request.GET.get('q') or '').strip()
    fields = records.search_fields(model)
    if q:
        if fields:
            qs = fuzzy.filter(qs, q, fields)
        else:
            qs = qs.filter(pk=q) if q.isdigit() or model._meta.pk.get_internal_type() == 'CharField' else qs.none()  # exact-match: a record with no name is found by its number
    else:
        qs = qs.order_by('-pk')
    page = max(1, inputs.read_int(request.GET, 'page', default=1))
    total = qs.count()
    items = qs[(page - 1) * PAGE: page * PAGE]
    return _ok({
        'model': records.key_of(model),
        'name': str(model._meta.verbose_name_plural),
        'money': records.is_money(model),
        'rows': [records.row(o) for o in items],
        'count': total,
        'page': page,
        'pages': max(1, (total + PAGE - 1) // PAGE),
        'searchable': bool(fields),
    })


@api_view(['GET', 'PATCH'])
@admin_role_required(VIEW)
def admin_record(request, key, pk):
    model, obj = _find(key, pk)
    if model is None:
        return _err('No such kind of record.', 'UNKNOWN_MODEL', status.HTTP_404_NOT_FOUND)
    if obj is None:
        return _err('No such record.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'PATCH':
        if not _may(request, EDIT):
            return _err('You may look at records but not change them.', 'NO_EDIT_RECORDS',
                        status.HTTP_403_FORBIDDEN)
        try:
            records.apply_edit(obj, request.data.get('fields'), request.admin_user,
                               request.data.get('reason'))
        except records.RecordError as exc:
            return _refuse(exc)
        obj = model._base_manager.get(pk=obj.pk)

    out = records.detail(obj)
    versions = RecordVersion.objects.filter(model_label=records.key_of(model),
                                            object_pk=str(obj.pk))[:50]
    out.update({
        'model': records.key_of(model),
        'name': str(model._meta.verbose_name),
        'pk': str(obj.pk),
        'label': records.label_of(obj),
        'money': records.is_money(model),
        'versions': [records.version_row(v) for v in versions],
        'may_edit': _may(request, EDIT) and not records.is_money(model),
        'may_correct': (records.is_money(model) and records.wallet_behind(obj) is not None
                        and _may(request, EDIT) and _may(request, MONEY)),
        'deleted': bool(getattr(obj, 'deleted_at', None)),
    })
    return _ok(out, 'Saved.' if request.method == 'PATCH' else 'OK')


@api_view(['GET', 'POST'])
@admin_role_required(VIEW)
def admin_record_delete(request, key, pk):
    model, obj = _find(key, pk)
    if model is None or obj is None:
        return _err('No such record.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if request.method == 'GET':
        return _ok(records.delete_preview(obj))
    if not _may(request, EDIT):
        return _err('You may look at records but not delete them.', 'NO_EDIT_RECORDS',
                    status.HTTP_403_FORBIDDEN)
    try:
        entry = records.move_to_bin(obj, request.admin_user, request.data.get('reason'))
    except records.RecordError as exc:
        return _refuse(exc)
    return _ok(records.bin_row(entry), 'In the bin for %d days.' % records.BIN_DAYS)


@api_view(['POST'])
@admin_role_required(VIEW)
def admin_record_correct(request, key, pk):
    """A new entry with a reason; the record itself is never changed.

    Goes through `wallets.transfer`, the same as every other movement, between
    this record's wallet and the wallet named on the other side.
    """
    if not (_may(request, EDIT) and _may(request, MONEY)):
        return _err('Corrections need Edit records and the money permission.',
                    'NO_CORRECT_RECORDS', status.HTTP_403_FORBIDDEN)
    model, obj = _find(key, pk)
    if model is None or obj is None:
        return _err('No such record.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    wallet = records.wallet_behind(obj) if records.is_money(model) else None
    if wallet is None:
        return _err('This record has no wallet to correct.', 'NO_WALLET')
    reason = str(request.data.get('reason') or '').strip()
    if not reason:
        return _err('Say why this is being corrected.', 'REASON_REQUIRED')
    direction = str(request.data.get('direction') or '').strip()
    if direction not in ('credit', 'debit'):
        return _err('Say whether this adds coins or takes them.', 'VALIDATION_ERROR',
                    data={'field': 'direction'})
    try:
        other = wallets.resolve_target(request.data.get('other_kind'), request.data.get('other'))
        source, target = (other, wallet) if direction == 'credit' else (wallet, other)
        wallets.transfer(source, target, request.data.get('amount'),
                         note=('Correction: ' + reason)[:200], kind='transfer')
    except wallets.WalletError as exc:
        return _err(str(exc), exc.code)
    AdminAction.objects.create(
        admin=request.admin_user, action_type='record_correct',
        target_model=records.key_of(model)[:50], target_id=str(obj.pk)[:100], reason=reason,
        metadata={'direction': direction, 'amount': str(request.data.get('amount')),
                  'wallet': wallets.describe(wallet), 'other': wallets.describe(other)})
    return _ok({'wallet': wallets.describe(wallet), 'other': wallets.describe(other)}, 'Corrected.')


@api_view(['GET'])
@admin_role_required(VIEW)
def admin_records_bin(request):
    rows = RecordBin.objects.select_related('deleted_by')
    if request.GET.get('restored') != '1':
        rows = rows.filter(restored_at__isnull=True)
    q = (request.GET.get('q') or '').strip()
    if q:
        rows = fuzzy.filter(rows, q, ['label', 'model_label', 'reason'])
    return _ok({'rows': [records.bin_row(e) for e in rows[:200]],
                'days': records.BIN_DAYS,
                'may_restore': _may(request, EDIT),
                'may_purge': _may(request, PURGE)})


@api_view(['POST'])
@admin_role_required(VIEW)
def admin_records_bin_restore(request, entry_id):
    if not _may(request, EDIT):
        return _err('You may look at the bin but not restore from it.', 'NO_EDIT_RECORDS',
                    status.HTTP_403_FORBIDDEN)
    entry = RecordBin.objects.filter(pk=entry_id).first()
    if entry is None:
        return _err('Nothing like that in the bin.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        records.restore(entry, request.admin_user)
    except records.RecordError as exc:
        return _refuse(exc)
    return _ok(records.bin_row(entry), 'Restored.')


@api_view(['DELETE'])
@admin_role_required(VIEW)
def admin_records_bin_purge(request, entry_id):
    if not _may(request, PURGE):
        return _err('Only a super admin may throw records away for good.', 'NO_PURGE_RECORDS',
                    status.HTTP_403_FORBIDDEN)
    entry = RecordBin.objects.filter(pk=entry_id).first()
    if entry is None:
        return _err('Nothing like that in the bin.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    records.purge(entry, request.admin_user)
    return _ok({'id': entry_id}, 'Gone for good.')


@api_view(['POST'])
@admin_role_required(VIEW)
def admin_records_version_revert(request, version_id):
    if not _may(request, EDIT):
        return _err('You may look at history but not undo it.', 'NO_EDIT_RECORDS',
                    status.HTTP_403_FORBIDDEN)
    version = RecordVersion.objects.filter(pk=version_id).first()
    if version is None:
        return _err('No such change.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    try:
        new = records.revert(version, request.admin_user, request.data.get('reason'))
    except records.RecordError as exc:
        return _refuse(exc)
    return _ok(records.version_row(new), 'Undone.')
