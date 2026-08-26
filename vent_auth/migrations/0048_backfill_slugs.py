"""Give every row that predates the slug columns an address.

Without this, everything already in the database has `slug = NULL` and can only
be reached by id - which is the thing we just made illegal. Production holds
real organizations, clubs, threads, posts and scrims, so they are backfilled
here rather than left to be fixed the next time somebody saves them.

Written against the historical models on purpose. Calling the live `save()`
would pull in `sync_slug`, and a data migration that depends on today's model
code stops being reproducible the moment that code changes.
"""
import secrets

from django.db import migrations
from django.utils.text import slugify

MAX_SLUG = 160
TOKEN_ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789'


def unique_slug(model, base, taken):
    """`base`, or `base-2`, `base-3`, ... - whichever is free."""
    base = (base or '')[:MAX_SLUG - 8] or 'item'
    candidate = base
    counter = 2
    while candidate in taken or model.objects.filter(slug=candidate).exists():
        suffix = f'-{counter}'
        candidate = f'{base[:MAX_SLUG - len(suffix)]}{suffix}'
        counter += 1
    taken.add(candidate)
    return candidate


def unique_token(model, prefix, taken, length=10):
    while True:
        candidate = f"{prefix}_{''.join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))}"
        if candidate not in taken and not model.objects.filter(slug=candidate).exists():
            taken.add(candidate)
            return candidate


def fill_named(model, name_field):
    taken = set()
    rows = list(model.objects.filter(slug__isnull=True))
    for row in rows:
        row.slug = unique_slug(model, slugify(getattr(row, name_field, '') or ''), taken)
    if rows:
        model.objects.bulk_update(rows, ['slug'])
    return len(rows)


def fill_tokened(model, prefix):
    taken = set()
    rows = list(model.objects.filter(slug__isnull=True))
    for row in rows:
        row.slug = unique_token(model, prefix, taken)
    if rows:
        model.objects.bulk_update(rows, ['slug'])
    return len(rows)


def forwards(apps, schema_editor):
    named = [
        ('Organization', 'org_name'),
        ('Club', 'name'),
        ('Thread', 'title'),
    ]
    tokened = [('Post', 'p'), ('Scrim', 's')]

    for model_name, field in named:
        model = apps.get_model('vent_auth', model_name)
        fill_named(model, field)

    for model_name, prefix in tokened:
        model = apps.get_model('vent_auth', model_name)
        fill_tokened(model, prefix)


def backwards(apps, schema_editor):
    """Nothing to undo. The columns go with the schema migration, and blanking
    them on the way back would throw away addresses that may already be shared."""


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0047_club_slug_organization_slug_post_slug_scrim_slug_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
