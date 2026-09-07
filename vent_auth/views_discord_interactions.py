"""The one URL Discord POSTs a slash command to.

Set this as the **Interactions Endpoint URL** in the Discord developer portal:

    https://api.v-ent.co/auth/discord/interactions/

Discord will not accept the URL until it has sent several requests with
deliberately BAD signatures and had every one rejected with 401. So a mistake
in the verification fails at setup, loudly, rather than quietly leaving an open
endpoint in production. That is a good test and it is worth knowing it happens.

## Why csrf_exempt and AllowAny are correct here

This is called by Discord, not by a browser and not by anybody signed in.
There is no session, no cookie and no CSRF token to check, and demanding one
would simply break it.

**The signature IS the authentication.** Nothing is parsed, looked up or acted
on before `verify()` passes, and it is checked against the RAW request body
because re-serialising a dict changes the bytes and invalidates it.

A decorator on the wrong function is a fault this codebase has shipped before
(`feedback_decorator_belongs_to_its_view`), so both decorators sit directly on
the view they belong to.
"""
import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from . import discord_commands as commands

logger = logging.getLogger(__name__)


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def interactions(request):
    """Answer one interaction from Discord."""
    signature = request.headers.get('X-Signature-Ed25519', '')
    timestamp = request.headers.get('X-Signature-Timestamp', '')

    if not commands.public_key():
        # Nothing is configured, so nothing can be verified, so nothing is
        # trusted. Answering 401 rather than 503 on purpose: an unverifiable
        # request is an unauthorised one whatever the reason.
        logger.warning('discord interaction with no public key configured')
        return JsonResponse({'error': 'not configured'}, status=401)

    if not commands.verify(signature, timestamp, request.body):
        return JsonResponse({'error': 'invalid request signature'}, status=401)

    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'bad payload'}, status=400)

    kind = payload.get('type')

    # The handshake. Discord sends this to check the endpoint is alive, both
    # when the URL is saved and periodically afterwards.
    if kind == commands.PING:
        return JsonResponse({'type': commands.PONG})

    if kind == commands.APPLICATION_COMMAND:
        return JsonResponse(commands.handle(payload))

    # A component or an autocomplete, which nothing here registers yet.
    # Answered rather than ignored, so Discord does not show the person a
    # failure for something we simply do not do.
    return JsonResponse(commands.reply(
        'That is not something I can answer yet.'))
