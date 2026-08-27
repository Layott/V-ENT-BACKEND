"""Fold the annual EA FC rows into one game with editions.

The games list held "EA FC 24" and "EA FC 25" as two unrelated rows. This makes
a single "EA FC" game, gives it those editions, repoints anything that pointed
at the yearly rows, and then retires the yearly rows rather than deleting them.

Retiring rather than deleting is deliberate: several models cascade from Games,
so deleting "EA FC 24" would delete the tournaments played on it. `is_active`
takes it out of the pickers and leaves the history intact.

Only titles that are genuinely annual are touched, by exact name. Nothing tries
to be clever with pattern matching: guessing which rows are editions of each
other is exactly the kind of thing that quietly mangles somebody's data.
"""
from django.db import migrations

# parent game -> [(old row title, edition name, release year)]
FOLD = {
    'EA FC': [
        ('EA FC 24', 'EA FC 24', 2023),
        ('EA FC 25', 'EA FC 25', 2024),
    ],
}


def fold(apps, schema_editor):
    Games = apps.get_model('vent_auth', 'Games')
    GameSeries = apps.get_model('vent_auth', 'GameSeries')
    Tournament = apps.get_model('vent_tournament', 'Tournament')
    Event = apps.get_model('vent_event', 'Event')

    for parent_title, editions in FOLD.items():
        existing = [e for e in editions
                    if Games.objects.filter(game_title=e[0]).exists()]
        if not existing:
            continue

        parent, _ = Games.objects.get_or_create(
            game_title=parent_title,
            defaults={'description': 'EA SPORTS FC, all editions.'},
        )

        for order, (old_title, edition_name, year) in enumerate(editions):
            old = Games.objects.filter(game_title=old_title).first()
            if old is None:
                continue

            series, _ = GameSeries.objects.get_or_create(
                game=parent, name=edition_name,
                defaults={'release_year': year, 'sort_order': order},
            )

            # Carry the logo over once, so the parent is not blank.
            if old.logo and not parent.logo:
                parent.logo = old.logo
                parent.save(update_fields=['logo'])

            Tournament.objects.filter(tournament_game=old).update(
                tournament_game=parent, tournament_series=series)
            Event.objects.filter(game=old).update(game=parent, series=series)

            # Retired, not deleted: Games cascades into several models, and
            # deleting this row would take the tournaments played on it too.
            old.is_active = False
            old.save(update_fields=['is_active'])


def unfold(apps, schema_editor):
    """Put the yearly rows back in the pickers.

    The tournaments stay on the parent game: moving them back would need the
    edition-to-row mapping to still be trustworthy, and a reverse migration that
    reshuffles live records is more dangerous than one that does less.
    """
    Games = apps.get_model('vent_auth', 'Games')
    for editions in FOLD.values():
        for old_title, _name, _year in editions:
            Games.objects.filter(game_title=old_title).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0050_alter_games_options_games_is_active_games_sort_order_and_more'),
        ('vent_tournament', '0018_tournament_tournament_series'),
        ('vent_event', '0011_event_series'),
    ]

    operations = [
        migrations.RunPython(fold, unfold),
    ]
