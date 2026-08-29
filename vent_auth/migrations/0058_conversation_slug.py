# Give every direct-message conversation a public address of its own.
#
# The model now issues an opaque `d_...` token on save, which means conversations
# created from here on carry one. Existing rows would only get theirs on the next
# message, and until then a notification about them would have to fall back to the
# primary key - the address this token exists to keep out of URLs. So they are
# filled in here instead.

from django.db import migrations, models


def give_every_conversation_a_token(apps, schema_editor):
    Conversation = apps.get_model('vent_auth', 'Conversation')
    from vent_auth.slugs import public_token

    taken = set(
        Conversation.objects.exclude(slug=None).values_list('slug', flat=True))
    for convo in Conversation.objects.filter(slug=None).iterator():
        while True:
            candidate = public_token('d')
            if candidate not in taken:
                break
        taken.add(candidate)
        convo.slug = candidate
        convo.save(update_fields=['slug'])


def drop_them(apps, schema_editor):
    """Reversible: the column goes away with the field, so there is nothing to undo."""


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0057_game_mode_unique_per_edition'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='slug',
            field=models.SlugField(blank=True, max_length=160, null=True, unique=True),
        ),
        migrations.RunPython(give_every_conversation_a_token, drop_them),
    ]
