"""Connected keys: an organiser's own key to a paid service (inbox 551).

    GET    /auth/connected-keys/                    the viewer's keys and their organisations'
    POST   /auth/connected-keys/                    {provider, key, model?, organization?} connect or replace
    DELETE /auth/connected-keys/<provider>/         ?organization=<slug> for an organisation's

The key itself never leaves the server once sent: the answer carries the
provider and the last four characters (R55, R63). A key is checked with the
provider before it is kept, so a typo is caught here rather than on the first
screenshot read.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import ai_providers
from . import connected_keys as ck
from . import inputs
from .actors import actor_from_request
from .models import ConnectedKey, Organization
from .throttle import limited


def _ok(data, message='OK', http_status=status.HTTP_200_OK):
    return Response({'status': 'success', 'data': data, 'message': message}, status=http_status)


def _err(message, code, http_status=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'status': 'error', 'data': {}, 'message': message, 'code': code}
    if field:
        body['field'] = field
    return Response(body, status=http_status)


def _may_manage(user, org):
    from vent_billing.permissions import may_manage_org
    return may_manage_org(user, org)


def _org(user, slug):
    """The organisation named, if this person may manage its keys; else an error."""
    org = Organization.objects.filter(slug=slug).first() if slug else None
    if org is None:
        return None, _err('That organisation was not found.', 'ORG_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if not _may_manage(user, org):
        return None, _err('Only the people running this organisation can change its keys.',
                          'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    return org, None


# Any provider now (inbox 555), so the sentence names the one chosen. The code
# is what the screen translates; {provider} and {hint} travel in `data`.
REFUSALS = {
    'KEY_PROVIDER_UNKNOWN': ('That service is not one V-ENT can use.', status.HTTP_400_BAD_REQUEST),
    'KEY_SHAPE_WRONG': ('That does not look like a {provider} key. {hint}.', status.HTTP_400_BAD_REQUEST),
    'KEY_WRONG_PROVIDER': ('That key is for {guessed}, not {provider}. Choose {guessed} instead.',
                           status.HTTP_400_BAD_REQUEST),
    'MODEL_NAME_WRONG': ('That is not a model name. Leave it empty to use the usual one.',
                         status.HTTP_400_BAD_REQUEST),
    'KEY_REJECTED': ('{provider} did not accept that key. Check it and try again.', status.HTTP_400_BAD_REQUEST),
    'KEY_CHECK_UNAVAILABLE': ('{provider} could not be reached to check the key. Try again.',
                              status.HTTP_503_SERVICE_UNAVAILABLE),
}


@api_view(['GET', 'POST'])
@limited('connected_keys', 20)
def connected_keys(request):
    user, err = actor_from_request(request)
    if err:
        return err
    if request.method == 'GET':
        orgs = [o for o in Organization.objects.filter(connected_keys__isnull=False).distinct()
                if _may_manage(user, o)]
        rows = list(ConnectedKey.objects.filter(user=user, organization__isnull=True)) + \
            list(ConnectedKey.objects.filter(organization__in=orgs).select_related('organization'))
        return _ok({'keys': [ck.as_row(r) for r in rows],
                    'providers': ai_providers.public_list()})

    provider = inputs.read_choice(request.data, 'provider', list(ck.PROVIDERS), required=True)
    key = inputs.read_text(request.data, 'key', max_length=200, required=True)
    model = inputs.read_text(request.data, 'model', max_length=80)
    org = None
    slug = inputs.read_text(request.data, 'organization', max_length=160)
    if slug:
        org, err = _org(user, slug)
        if err:
            return err
    try:
        ck.check_shape(provider, key, model)
        ck.verify_with_provider(provider, key)
    except ck.ConnectedKeyError as exc:
        message, code_status = REFUSALS[exc.code]
        spec = ai_providers.PROVIDERS.get(provider, {})
        words = {'provider': spec.get('label', provider), 'hint': exc.extra.get('hint', ''),
                 'guessed': exc.extra.get('guessed_label', '')}
        body = _err(message.format(**words), exc.code, code_status,
                    field='model' if exc.code == 'MODEL_NAME_WRONG' else 'key')
        body.data['data'] = {**words, 'guessed_key': exc.extra.get('guessed', '')}
        return body
    row = ck.store(provider=provider, key=key, model=model, user=None if org else user,
                   organization=org, added_by=user)
    return _ok({'key': ck.as_row(row)}, 'Key connected.', status.HTTP_201_CREATED)


@api_view(['DELETE'])
def disconnect_key(request, provider):
    user, err = actor_from_request(request)
    if err:
        return err
    slug = inputs.read_text(request.query_params, 'organization', max_length=160)
    if slug:
        org, err = _org(user, slug)
        if err:
            return err
        rows = ConnectedKey.objects.filter(organization=org, provider=provider)
    else:
        rows = ConnectedKey.objects.filter(user=user, organization__isnull=True, provider=provider)
    if not rows.exists():
        return _err('There is no such key.', 'KEY_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    rows.delete()
    return _ok({}, 'Key removed.')
