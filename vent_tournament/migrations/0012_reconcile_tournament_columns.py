"""Reconcile the Tournament columns that 0009 added in STATE ONLY.

`vent_tournament/0009_sponsors_delete_sponsor_and_more.py` is a single top-level
`SeparateDatabaseAndState(database_operations=[])` wrapping state-only `AddField`s.
0011 already reconciled the Sponsors table + M2M, but NOT these 11 Tournament
columns. On a freshly-built MySQL database they are therefore absent, which breaks
the tournament module at runtime (get-all-tournaments filters `is_draft=False`,
create sets tournament_creator / tournament_game, etc.).

Why this is easy to miss: on SQLite most later `ALTER`/`AddField` ops rebuild the
whole table from migration state, materializing these columns as a side effect, so
a plain fresh-SQLite build reports them present. MySQL's `ADD COLUMN` does not
rebuild, so they stay missing. This migration is validated against a *drifted*
schema (columns removed), not a plain fresh SQLite build.

Same idempotent RunPython pattern as 0011: introspect, and `ADD` each column only
if absent — a no-op on any DB that already has them (deployed / already-run). The
three FKs (tournament_creator -> Users, tournament_game -> Games,
tournament_organization -> Organization) target models whose PKs are explicit
`AutoField`s (INT), so `schema_editor.add_field` emits INT FK columns that match
their targets (no INT/BIGINT class mismatch). No state change is performed (0009
already put these fields into state), so `makemigrations --check` stays clean and
state + database end in agreement.
"""
from django.db import migrations


# Field names on the current Tournament model that 0009 added state-only.
FIELD_NAMES = [
    'is_draft',
    'tournament_creator',       # FK -> vent_auth.Users
    'tournament_game',          # FK -> vent_auth.Games
    'prize_type',
    'team_size',
    'game_mode',
    'tournament_organization',  # FK -> vent_auth.Organization
    'virtual_link',
    'tiktok_link',
    'bigolive_link',
    'interaction_count',
]


def _column_names(conn, table):
    with conn.cursor() as cursor:
        return {c.name for c in conn.introspection.get_table_description(cursor, table)}


def reconcile_tournament_columns(apps, schema_editor):
    conn = schema_editor.connection
    Tournament = apps.get_model('vent_tournament', 'Tournament')
    table = Tournament._meta.db_table

    # Re-introspect before every add. On MySQL (the real target) ADD COLUMN is a
    # simple column add, so a single upfront read would suffice. On SQLite,
    # add_field rebuilds the whole table from the model and can materialize the
    # other pending columns as a side effect, which would make a cached column
    # set stale and cause a duplicate-column error on the next add. Reading fresh
    # each time keeps this idempotent and correct on both backends.
    for name in FIELD_NAMES:
        field = Tournament._meta.get_field(name)
        if field.column not in _column_names(conn, table):
            schema_editor.add_field(Tournament, field)


def noop(apps, schema_editor):
    # Non-destructive reverse: leave the reconciled columns in place.
    pass


class Migration(migrations.Migration):

    # MySQL has no transactional DDL: schema_editor DDL inside RunPython must
    # not run inside the transaction Django otherwise forces around it.
    atomic = False

    dependencies = [
        ('vent_tournament', '0011_reconcile_sponsors_table'),
    ]

    operations = [
        migrations.RunPython(reconcile_tournament_columns, noop),
    ]
