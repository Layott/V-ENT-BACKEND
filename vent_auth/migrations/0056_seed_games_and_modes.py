"""The games organisers actually run, their editions, and how each is played.

The catalogue was whatever had been typed in by hand, and the wizard's Game Mode
select was a fixed list that offered Free Fire's modes to somebody running EA FC.

What is here is the competitive field as it stands in 2026, chosen by what is
actually played and organised rather than by what is famous:

  * **Mobile first**, because that is V-ENT's market. Mobile Legends draws the
    largest esports audience of any title; Free Fire and PUBG Mobile / BGMI are
    what runs in Nigeria and across Africa; Call of Duty Mobile and eFootball
    fill out the phone-led scene.
  * **PC**, because Counter-Strike 2, League of Legends, Valorant and Dota 2 are
    the four that never stop running somewhere.
  * **Console-led football and fighting**, because EA FC and the fighting games
    are what a local event in Lagos is most likely to be.

Every mode carries the format it is normally run as, so choosing Battle Royale
pre-selects points scoring with the right placement table instead of leaving an
organiser to work it out. They are defaults; an organiser can still choose
otherwise.

Nothing here deletes or renames an existing game. `get_or_create` on the title
means a game somebody already added keeps its id, its logo and every tournament
pointing at it.
"""
from django.db import migrations


# (title, platform note, [(edition, year)], [(mode, team size, format, placement)])
CATALOGUE = [
    # ---------------------------------------------------------------- mobile
    ('Free Fire', [('Free Fire', None)], [
        ('Battle Royale Squad', 4, 'battle_royale', 'free_fire'),
        ('Battle Royale Duo', 2, 'battle_royale', 'free_fire'),
        ('Battle Royale Solo', 1, 'battle_royale', 'free_fire'),
        ('Clash Squad 4v4', 4, 'single_elimination', ''),
        ('Lone Wolf 2v2', 2, 'single_elimination', ''),
    ]),
    ('PUBG Mobile', [('PUBG Mobile', None)], [
        ('Battle Royale Squad', 4, 'battle_royale', 'pubg_mobile'),
        ('Battle Royale Duo', 2, 'battle_royale', 'pubg_mobile'),
        ('Battle Royale Solo', 1, 'battle_royale', 'pubg_mobile'),
        ('Team Deathmatch 4v4', 4, 'single_elimination', ''),
    ]),
    ('Mobile Legends: Bang Bang', [('Mobile Legends: Bang Bang', None)], [
        ('5v5 Ranked', 5, 'double_elimination', ''),
        ('5v5 Draft Pick', 5, 'double_elimination', ''),
    ]),
    ('Call of Duty: Mobile', [('Call of Duty: Mobile', None)], [
        ('Search and Destroy 5v5', 5, 'double_elimination', ''),
        ('Battle Royale Squad', 4, 'battle_royale', 'pubg_mobile'),
        ('Hardpoint 5v5', 5, 'double_elimination', ''),
    ]),
    ('eFootball', [
        ('eFootball 2026', 2026), ('eFootball 2025', 2025),
    ], [
        ('1v1', 1, 'single_elimination', ''),
        ('2v2 Aggregate', 2, 'aggregate_2v2', ''),
        ('Online League', 1, 'round_robin', ''),
    ]),

    # ------------------------------------------------------- console and PC
    ('EA FC', [
        ('EA FC 26', 2026), ('EA FC 25', 2025), ('EA FC 24', 2024),
    ], [
        ('1v1 Ultimate Team', 1, 'single_elimination', ''),
        ('2v2 Aggregate', 2, 'aggregate_2v2', ''),
        ('Pro Clubs 11v11', 11, 'round_robin', ''),
        ('Online League', 1, 'round_robin', ''),
    ]),
    ('Counter-Strike 2', [('Counter-Strike 2', None)], [
        ('5v5 Competitive', 5, 'swiss', ''),
        ('5v5 Best of Three', 5, 'double_elimination', ''),
        ('2v2 Wingman', 2, 'single_elimination', ''),
    ]),
    ('League of Legends', [('League of Legends', None)], [
        ('5v5 Summoners Rift', 5, 'double_elimination', ''),
        ('5v5 Group Stage', 5, 'gsl', ''),
    ]),
    ('Valorant', [('Valorant', None)], [
        ('5v5 Competitive', 5, 'swiss', ''),
        ('5v5 Best of Three', 5, 'double_elimination', ''),
    ]),
    ('Dota 2', [('Dota 2', None)], [
        ('5v5 Captains Mode', 5, 'double_elimination', ''),
        ('5v5 Group Stage', 5, 'gsl', ''),
    ]),
    ('Tekken 8', [('Tekken 8', None)], [
        ('1v1 Best of Three', 1, 'double_elimination', ''),
        ('1v1 Best of Five', 1, 'double_elimination', ''),
    ]),
    ('Street Fighter 6', [('Street Fighter 6', None)], [
        ('1v1 Best of Three', 1, 'double_elimination', ''),
        ('1v1 Best of Five', 1, 'double_elimination', ''),
    ]),
    ('Rocket League', [('Rocket League', None)], [
        ('3v3 Standard', 3, 'double_elimination', ''),
        ('2v2 Doubles', 2, 'double_elimination', ''),
        ('1v1 Duel', 1, 'single_elimination', ''),
    ]),
    ('NBA 2K', [('NBA 2K26', 2026), ('NBA 2K25', 2025)], [
        ('1v1', 1, 'single_elimination', ''),
        ('5v5 Pro-Am', 5, 'double_elimination', ''),
    ]),
    ('Fortnite', [('Fortnite', None)], [
        ('Battle Royale Solo', 1, 'battle_royale', 'pubg_mobile'),
        ('Battle Royale Duo', 2, 'battle_royale', 'pubg_mobile'),
        ('Zero Build Solo', 1, 'battle_royale', 'pubg_mobile'),
    ]),
]


def seed(apps, schema_editor):
    Games = apps.get_model('vent_auth', 'Games')
    GameSeries = apps.get_model('vent_auth', 'GameSeries')
    GameMode = apps.get_model('vent_auth', 'GameMode')

    for order, (title, editions, modes) in enumerate(CATALOGUE):
        # get_or_create on the title, so a game somebody already added keeps its
        # id, its logo, and every tournament that points at it.
        game, created = Games.objects.get_or_create(
            game_title=title,
            defaults={'sort_order': order, 'is_active': True},
        )

        for edition_order, (name, year) in enumerate(editions):
            GameSeries.objects.get_or_create(
                game=game, name=name,
                defaults={
                    'release_year': year,
                    'sort_order': edition_order,
                    'is_active': True,
                },
            )

        for mode_order, (name, size, default_format, placement) in enumerate(modes):
            GameMode.objects.get_or_create(
                game=game, name=name,
                defaults={
                    'team_size': size,
                    'default_format': default_format,
                    'default_placement_table': placement,
                    'sort_order': mode_order,
                    'is_active': True,
                },
            )


def unseed(apps, schema_editor):
    """Deliberately does nothing.

    Removing these would take any tournament pointing at them with it, and a
    game somebody has since edited is no longer this migration's to delete.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0055_game_modes'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
