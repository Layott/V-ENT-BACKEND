"""Readable addresses for things that have names.

`/tournaments/25` tells a person nothing and tells a search engine less.
`/tournaments/naija-free-fire-weekly-12` is the same page with its name on it,
and it is what somebody expects to see when they copy a link out of the app.

Two rules make this safe to bolt onto tables that already have rows:

1. **The id keeps working.** Every lookup accepts either, so links already
   shared, bookmarks, and the claim emails sent in August all still resolve.
2. **A slug never changes silently.** It is generated once, on creation. Renaming
   a tournament does not break the link somebody posted in a group chat.
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
