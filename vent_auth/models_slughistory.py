"""Every address a thing has ever had.

The original rule was that a slug is generated once and never changes, so a link
posted in a group chat keeps working after a rename. That protects old links by
letting the URL go stale: rename a tournament and its address still carries the
old name, which is the thing people notice and complain about.

Both halves are gettable. The slug follows the name, and every slug the thing has
ever had is kept here and redirects to the current one. A link shared in June
still opens the right page in December, and the address bar shows today's name.
This is how GitHub handles a renamed repository and Notion a renamed page.

One table rather than a history table per model: the rows are identical in shape,
nothing joins against them, and they are only ever read on the miss path - when a
slug was not found live, which is rare. A single unique index on the slug is what
makes that lookup cheap.
"""
from django.db import models


class SlugHistory(models.Model):
    """A slug that used to point at something, and what it points at now."""

    # 'tournament' | 'event' | 'team'. A plain string rather than a ContentType
    # FK: this table is read on a cache-miss path where a join buys nothing, and
    # the values are a closed set the router already knows by name.
    entity_type = models.CharField(max_length=32, db_index=True)
    entity_id = models.PositiveIntegerField()

    # Unique across everything. Two different tournaments cannot both claim
    # 'naija-weekly', and neither can a team, because the redirect has to be
    # able to answer without being told which kind of thing it is looking for.
    slug = models.SlugField(max_length=160, unique=True, db_index=True)

    retired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['entity_type', 'entity_id'])]
        verbose_name_plural = 'slug history'

    def __str__(self):
        return f'{self.slug} -> {self.entity_type}#{self.entity_id}'


def remember(entity_type, entity_id, slug):
    """Keep an address that is about to stop being current.

    Silent when the slug is already recorded, because renaming a thing back to a
    name it used to have is a normal thing to do and must not raise.
    """
    if not slug:
        return None
    existing = SlugHistory.objects.filter(slug=slug).first()
    if existing is not None:
        # Point it at whoever holds it now. A slug freed by one rename and taken
        # by another entity should redirect to where the name actually lives.
        if existing.entity_type != entity_type or existing.entity_id != entity_id:
            existing.entity_type = entity_type
            existing.entity_id = entity_id
            existing.save(update_fields=['entity_type', 'entity_id'])
        return existing
    return SlugHistory.objects.create(
        entity_type=entity_type, entity_id=entity_id, slug=slug,
    )


def resolve(entity_type, slug):
    """The id a retired slug used to belong to, or None."""
    row = SlugHistory.objects.filter(entity_type=entity_type, slug=slug).first()
    return row.entity_id if row else None


def release(entity_type, entity_id, current_slug):
    """Drop the history row that matches a slug now in live use.

    Called after a rename: if the new slug was previously retired by this same
    entity, the history row is now a self-referential redirect and would send the
    page to itself.
    """
    SlugHistory.objects.filter(
        entity_type=entity_type, entity_id=entity_id, slug=current_slug,
    ).delete()
