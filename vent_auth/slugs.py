"""Readable addresses for things that have names.

`/tournaments/25` tells a person nothing and tells a search engine less.
`/tournaments/naija-free-fire-weekly-12` is the same page with its name on it,
and it is what somebody expects to see when they copy a link out of the app.

Two rules make this safe to bolt onto tables that already have rows:

1. **The id keeps working.** Every lookup accepts either, so links already
   shared, bookmarks, and the claim emails sent in August all still resolve.
2. **A rename moves the address, and the old one still works.** The slug follows
   the name, and every slug a thing has ever had is kept in `SlugHistory` and
   redirects to the current one. So the URL shows today's name and the link
   somebody posted in a group chat in June still opens the right page.
"""
import re

from django.utils.text import slugify

MAX_SLUG = 160


def build_slug(name, *, model, field='slug', instance_pk=None, pk_field='pk'):
    """A unique, readable slug for this name within this table.

    Falls back to the numeric id when a name produces nothing sluggable, which
    happens with a name written entirely in a script slugify strips.
    """
    base = slugify(name or '')[:MAX_SLUG - 8]
    if not base:
        base = 'item'

    candidate = base
    counter = 2
    while True:
        query = model.objects.filter(**{field: candidate})
        if instance_pk is not None:
            query = query.exclude(**{pk_field: instance_pk})
        if not query.exists():
            return candidate
        suffix = f'-{counter}'
        candidate = f'{base[:MAX_SLUG - len(suffix)]}{suffix}'
        counter += 1


NUMERIC = re.compile(r'^\d+$')


def lookup_kwargs(key, *, id_field, slug_field='slug'):
    """Filter kwargs that resolve either an id or a slug.

    `/tournaments/25` and `/tournaments/naija-weekly` reach the same view; which
    one was used is not the view's business.
    """
    key = str(key or '').strip()
    if NUMERIC.match(key):
        return {id_field: int(key)}
    return {slug_field: key}


def sync_slug(instance, name, *, entity_type, id_attr, field='slug'):
    """Keep `instance.slug` matching `name`, remembering whatever it replaces.

    Returns True when the slug changed, so a caller mid-save knows to include the
    field in `update_fields`.

    The order matters. The old slug is remembered before the new one is written,
    and the new one is released from history afterwards - otherwise renaming a
    thing back to a previous name leaves a history row pointing the live URL at
    itself, which is a redirect loop.
    """
    from .models_slughistory import release, remember

    current = getattr(instance, field, None)
    desired = build_slug(
        name, model=type(instance), field=field,
        instance_pk=instance.pk, pk_field='pk',
    )

    if current == desired:
        return False

    entity_id = getattr(instance, id_attr, None) or instance.pk
    if current and entity_id:
        remember(entity_type, entity_id, current)

    setattr(instance, field, desired)

    if entity_id:
        release(entity_type, entity_id, desired)
    return True


def resolve_or_redirect(key, *, entity_type, id_field, model, queryset=None):
    """Find the thing, or say which address it moved to.

    Returns `(instance, moved_to_slug)`. Exactly one is set:

    - the instance, when the id or the live slug matched;
    - `moved_to_slug`, when the key is an address this thing used to have;
    - both None, when there is genuinely no such thing.

    Callers answer a move with 200 and `status: 'moved'`, not a real 301. The
    browser's fetch() follows redirects transparently, so a 301 carrying a
    frontend path would be chased against the API host and arrive as a 404 with
    the body thrown away. The app reads the envelope and rewrites the address
    itself, which is also the only place that knows what a frontend URL is.

    The history table is only touched on the miss path, which is the rare one.
    """
    from .models_slughistory import resolve

    rows = queryset if queryset is not None else model.objects.all()
    instance = rows.filter(**lookup_kwargs(key, id_field=id_field)).first()
    if instance is not None:
        return instance, None

    key = str(key or '').strip()
    if NUMERIC.match(key):
        return None, None          # an id that no longer exists is simply gone

    moved_id = resolve(entity_type, key)
    if moved_id is None:
        return None, None

    current = rows.filter(**{id_field: moved_id}).first()
    if current is None:
        return None, None          # the thing itself was deleted since
    return None, getattr(current, 'slug', None) or str(moved_id)


# ---------------------------------------------------------------------------
# Things that cannot be named
# ---------------------------------------------------------------------------

TOKEN_ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789'   # no l/o/0/1, misread aloud


def public_token(prefix, length=10):
    """A short, stable, non-enumerable address for something with no name.

    A post and a direct-message conversation cannot be given a readable slug -
    there is nothing to slugify, and the first line of somebody's message has no
    business being in a URL. They still must not carry the primary key: a
    sequential id in an address lets anybody walk the entire table by counting,
    which is how scrapers enumerate content and how a private conversation gets
    found by somebody who was never in it.
    """
    import secrets
    body = ''.join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))
    return f'{prefix}_{body}'


def ensure_token(instance, prefix, *, field='slug', length=10):
    """Give `instance` a public token if it has none. Returns True if set.

    Unlike a name-derived slug this never changes: there is no name for it to
    follow, and a stable address is the whole point.
    """
    if getattr(instance, field, None):
        return False
    model = type(instance)
    while True:
        candidate = public_token(prefix, length)
        if not model.objects.filter(**{field: candidate}).exists():
            setattr(instance, field, candidate)
            return True
