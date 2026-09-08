"""A vendor stall gets an address made of its name.

Stalls were linked as `?vendor=14`. Adding the column is only half of it: every
stall that already exists would carry NULL, and `?vendor=` would keep falling
back to the primary key for exactly the stalls people have already visited.

So the column and the backfill land together. `django.utils.text.slugify` is
used directly rather than `vent_auth.slugs.build_slug`, because a migration
runs against the historical model and must not import behaviour that may have
changed by the time somebody runs it on an old database.
"""
from django.db import migrations, models
from django.utils.text import slugify


def fill_slugs(apps, schema_editor):
    Vendor = apps.get_model('vent_event', 'Vendor')
    taken = set()
    for vendor in Vendor.objects.all().order_by('id'):
        if vendor.slug:
            taken.add(vendor.slug)
            continue
        base = slugify(vendor.name or '')[:150] or 'stall'
        candidate = base
        # Two stalls at different events may legitimately share a name, so the
        # primary key breaks the tie the same way build_slug does.
        if candidate in taken:
            candidate = f'{base}-{vendor.id}'
        while candidate in taken:
            candidate = f'{base}-{vendor.id}-{len(taken)}'
        vendor.slug = candidate
        vendor.save(update_fields=['slug'])
        taken.add(candidate)


def clear_slugs(apps, schema_editor):
    Vendor = apps.get_model('vent_event', 'Vendor')
    Vendor.objects.update(slug=None)


class Migration(migrations.Migration):

    dependencies = [
        ('vent_event', '0034_doorlookup_kind'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendor',
            name='slug',
            field=models.SlugField(blank=True, max_length=160, null=True, unique=True),
        ),
        migrations.RunPython(fill_slugs, clear_slugs),
    ]
