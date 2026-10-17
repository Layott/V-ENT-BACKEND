"""Give every tournament planned before P1b the chain of lines it describes.

The same rule as `progressions.convert`, copied because a migration must not
import live code: between each stage and the next by `order`, a line sending
places 1 to `advances` (each group), fired already with what the stage sent if
the stage was advanced. A tournament that already has lines is left alone.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    TournamentStage = apps.get_model('vent_tournament', 'TournamentStage')
    Progression = apps.get_model('vent_tournament', 'Progression')
    by_tournament = {}
    for stage in TournamentStage.objects.order_by('tournament_id', 'order'):
        by_tournament.setdefault(stage.tournament_id, []).append(stage)
    for tid, stages in by_tournament.items():
        if len(stages) < 2:
            continue
        if Progression.objects.filter(source__tournament_id=tid).exists():
            continue
        for index in range(len(stages) - 1):
            source, target = stages[index], stages[index + 1]
            if (source.advances or 0) <= 0:
                continue
            fired = source.status == 'complete'
            Progression.objects.create(
                source=source, target=target, group=None, place_from=1,
                place_to=source.advances, side='any', carry_points=False,
                fire='after_preview', order=index,
                sent=list(source.advanced or []) if fired else [],
                fired_at=source.completed_at if fired else None)


def backwards(apps, schema_editor):
    apps.get_model('vent_tournament', 'Progression').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vent_tournament', '0066_progression'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
