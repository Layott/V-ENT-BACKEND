# -*- coding: utf-8 -*-
"""A text layer becomes a layer, and a layer can be a piece of media.

CEO, 4 September 2026, inbox row 51: "there should be elements you can add or
ways to add certan uploaded things like images, sponsor logos, player images or
videos as like elements that will then be movable inside an element once they
are loaded".

Written by hand rather than taken from `makemigrations`, which proposed
CreateModel plus DeleteModel: that drops the table and every row in it. The
model was three hours old and might have held nothing, and "might" is not how a
table gets dropped. A rename keeps the rows and the id sequence.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vent_tournament', '0046_tournamentoverlay_options'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='OverlayTextLayer',
            new_name='OverlayLayer',
        ),
        migrations.AddField(
            model_name='overlaylayer',
            name='kind',
            field=models.CharField(
                choices=[('text', 'Words'),
                         ('asset', 'Something from the media library')],
                default='text', max_length=8),
        ),
        migrations.AddField(
            model_name='overlaylayer',
            name='asset',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='layers', to='vent_tournament.studioasset'),
        ),
        migrations.AddField(
            model_name='overlaylayer',
            name='width_px',
            field=models.SmallIntegerField(default=0),
        ),
    ]
