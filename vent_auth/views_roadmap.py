"""GET/POST/DELETE /roadmap/interest/ - "Tell me when it opens" (inbox 421).

A module that is not open yet (the shop, wagers, Vermillion City, the anime hub)
shows what it will do and a button to be told when it opens. Pressing it needs
an account, because the only way to tell somebody later is through their
account; the page asks a signed-out visitor to sign up first and this refuses
them too, so a hidden button is never the only guard.

GET answers everybody, signed in or not, with the modules this viewer asked
for (none for a stranger), so the page has one code path for both.
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import inputs
from .models import ModuleInterest
from .views_profile import _user_from_bearer


def _asked(user):
    if user is None:
        return []
    return sorted(ModuleInterest.objects.filter(user=user).values_list('module', flat=True))


@api_view(['GET', 'POST', 'DELETE'])
def module_interest(request):
    if request.method == 'GET':
        user = None
        if request.headers.get('Authorization'):
            user, err = _user_from_bearer(request)
            if err is not None:
                user = None
        return Response({'status': 'success', 'data': {'modules': _asked(user)},
                         'message': 'Modules asked for.'})

    user, err = _user_from_bearer(request)
    if err is not None:
        return err

    module = inputs.read_text(request.data, 'module', max_length=20).lower()
    if module not in ModuleInterest.MODULES:
        return Response({'status': 'error', 'code': 'UNKNOWN_MODULE',
                         'message': 'There is no module by that name.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        ModuleInterest.objects.filter(user=user, module=module).delete()
        return Response({'status': 'success', 'data': {'modules': _asked(user)},
                         'message': 'You will not be told.'})

    ModuleInterest.objects.get_or_create(user=user, module=module)
    return Response({'status': 'success', 'data': {'modules': _asked(user)},
                     'message': 'You will be told when it opens.'})
