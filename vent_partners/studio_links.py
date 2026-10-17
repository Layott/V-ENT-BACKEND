"""Who a player is in a studio's game (design section 14, item 2).

Two ways to tie a game account to a V-ENT account, both ending in the same
`StudioAccountLink` row:

1. **A code shown in the game.** The studio's server asks for a code for one
   of its accounts; the game shows it; the player, signed in on V-ENT, types
   it at /link-game, sees which game account it belongs to, and confirms. The
   game proved the account, V-ENT's session proved the person. The code is
   eight characters from an alphabet with no 0/O or 1/I, lives ten minutes,
   is stored only as a hash, and is never put in an address.

2. **Sign in with V-ENT.** The game signs the player in with the OAuth flow
   partners already have and hands its server the access token; the server
   posts the token with its account id. The token must be this studio's own,
   still valid, and carry the `identity` scope.

A sandbox game links only its studio's own account and the testers it named,
so an unreviewed game cannot collect real players. One account per player per
game, and one player per account: anything else is refused with a code rather
than silently moving a link from one person to another.
"""
import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
CODE_LENGTH = 8
LIFETIME = timedelta(minutes=10)


class LinkRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def normalise(text):
    return ''.join(c for c in str(text or '').upper() if c.isalnum())


def hash_code(text):
    return hashlib.sha256(normalise(text).encode()).hexdigest()


def new_code():
    raw = ''.join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return '%s-%s' % (raw[:4], raw[4:])


def issue(game, external_id, external_name):
    """A fresh code for one of the studio's accounts. Returns (row, code).

    Any earlier unclaimed code for the same account stops working, so a player
    who asks twice can only ever use the one their game shows now.
    """
    from .models import StudioLinkCode

    now = timezone.now()
    StudioLinkCode.objects.filter(game=game, external_id=external_id, claimed_at__isnull=True,
                                  expires_at__gt=now).update(expires_at=now)
    code = new_code()
    row = StudioLinkCode.objects.create(game=game, code_hash=hash_code(code),
                                        external_id=external_id, external_name=external_name,
                                        expires_at=now + LIFETIME)
    return row, code


def find(code):
    """The live, unclaimed code row for what somebody typed, or None."""
    from .models import StudioLinkCode

    if len(normalise(code)) != CODE_LENGTH:
        return None
    return (StudioLinkCode.objects.select_related('game', 'game__partner')
            .filter(code_hash=hash_code(code), claimed_at__isnull=True,
                    expires_at__gt=timezone.now()).first())


def _link(game, user, external_id, external_name, method):
    """The one place a link is made, with every refusal."""
    from .models import StudioAccountLink

    if not game.is_live and not game.may_test(user):
        raise LinkRefused('SANDBOX_TESTERS_ONLY')
    existing = StudioAccountLink.objects.filter(game=game, external_id=external_id).first()
    if existing is not None:
        if existing.user_id == user.user_id:
            return existing, False
        raise LinkRefused('ACCOUNT_TAKEN')
    if StudioAccountLink.objects.filter(game=game, user=user).exists():
        raise LinkRefused('STUDIO_ALREADY_LINKED')
    link = StudioAccountLink.objects.create(game=game, user=user, external_id=external_id,
                                            external_name=external_name or '', method=method)
    return link, True


def _announce(link):
    from . import studio_events

    studio_events.publish_to_game(link.game, 'account_linked', {
        'account_id': link.external_id, 'username': link.user.username, 'method': link.method})


def claim(code_row, user):
    """Spend a code for `user`. Returns the link; raises LinkRefused."""
    from .models import StudioLinkCode

    with transaction.atomic():
        row = (StudioLinkCode.objects.select_for_update().select_related('game', 'game__partner')
               .filter(pk=code_row.pk, claimed_at__isnull=True,
                       expires_at__gt=timezone.now()).first())
        if row is None:
            raise LinkRefused('LINK_CODE_NOT_FOUND')
        link, made = _link(row.game, user, row.external_id, row.external_name, 'code')
        row.claimed_by = user
        row.claimed_at = timezone.now()
        row.link = link
        row.save(update_fields=['claimed_by', 'claimed_at', 'link'])
    if made:
        _announce(link)
    return link


def link_by_token(game, token, external_id, external_name):
    """Link through a Sign in with V-ENT access token this studio holds."""
    from .models import OAuthAccessToken

    record = (OAuthAccessToken.objects.select_related('user')
              .filter(token_hash=hashlib.sha256(str(token or '').encode()).hexdigest(),
                      partner=game.partner).first())
    if record is None or not record.is_valid() or 'identity' not in (record.scopes or []):
        raise LinkRefused('OAUTH_TOKEN_INVALID')
    with transaction.atomic():
        link, made = _link(game, record.user, external_id, external_name, 'oauth')
    if made:
        _announce(link)
    return link


def unlink(link, by):
    """Remove a link and tell the studio who removed it ('player' or 'studio')."""
    from . import studio_events

    game, account, username = link.game, link.external_id, link.user.username
    link.delete()
    studio_events.publish_to_game(game, 'account_unlinked', {
        'account_id': account, 'username': username, 'by': by})


def link_row(link):
    """A link as the studio reads it."""
    return {
        'ref': link.ref,
        'account_id': link.external_id,
        'display_name': link.external_name,
        'username': link.user.username,
        'method': link.method,
        'linked_at': link.linked_at,
    }


def my_link_row(link):
    """A link as the player reads it."""
    return {
        'ref': link.ref,
        'game': {'name': link.game.name, 'title': link.game.game.game_title if link.game.game_id else None},
        'studio': link.game.partner.name,
        'display_name': link.external_name,
        'method': link.method,
        'linked_at': link.linked_at,
    }
