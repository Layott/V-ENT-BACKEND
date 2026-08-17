"""Reconcile the 3 state-only Event columns on freshly-built databases.

Migration 0002 (`..._event_game_event_interaction_count_event_is_active_and_more`)
added `Event.game`, `Event.is_active`, and `Event.interaction_count` in STATE ONLY
(`SeparateDatabaseAndState(database_operations=[])`), on the assumption the
then-deployed pythonanywhere/Railway DB already had these columns. 0003 adds
*other* new columns with real DDL but never these three, and no RunPython
reconcile exists.

On a freshly-built MySQL database these three columns are therefore MISSING even
though `migrate` runs green. SQLite masks it (0003's `AlterField`s rebuild the
whole event table from state, materializing the columns as a side effect); MySQL's
`ALTER TABLE ADD COLUMN` does not rebuild, so the columns stay absent. Missing,
they break the event module at runtime:
  * `game` (FK)          -> set by create_event; read by serialize_event_card/detail
  * `is_active`          -> get_all_events / view_event / event_vendors filter is_active=True
  * `interaction_count`  -> view_event increments it; serializer reads it

This migration reconciles the DATABASE to the STATE, idempotently, using Django's
own schema editor so every column type is derived from the model — no hand-typed
DDL and no INT/BIGINT guesswork. In particular the `game` FK column matches its
target `vent_auth_games.game_id`, which is an explicit `AutoField` (INT) even
though the project's DEFAULT_AUTO_FIELD is BigAutoField; `add_field` resolves that
from the historical model, so the FK types line up automatically.

Idempotency / safety across both worlds:
  * fresh DB (columns missing)          -> add each missing column (+ FK for game);
  * deployed / SQLite DB (present)      -> every per-column check skips -> no-op.

All three fields are safe to add even on a populated table: `is_active` and
`interaction_count` carry defaults (True / 0) that backfill existing rows, and
`game` is nullable (SET_NULL).

It performs NO state change (0002 already put the fields into state), so
`makemigrations --check` stays clean and state == database afterward.
"""
from django.db import migrations

# Model field names whose columns 0002 created state-only.
RECONCILE_FIELDS = ['game', 'is_active', 'interaction_count']


def reconcile_event_columns(apps, schema_editor):
    conn = schema_editor.connection
    Event = apps.get_model('vent_event', 'Event')
    table = Event._meta.db_table

    with conn.cursor() as cursor:
        existing_columns = {
            col.name for col in conn.introspection.get_table_description(cursor, table)
        }

    for field_name in RECONCILE_FIELDS:
        field = Event._meta.get_field(field_name)
        # field.column is 'game_id' for the FK, 'is_active' / 'interaction_count' otherwise.
        if field.column not in existing_columns:
            schema_editor.add_field(Event, field)


def noop(apps, schema_editor):
    # Non-destructive reverse: leave the reconciled columns in place.
    pass


class Migration(migrations.Migration):

    # MySQL has no transactional DDL: schema_editor DDL inside RunPython must
    # not run inside the transaction Django otherwise forces around it.
    atomic = False

    dependencies = [
        ('vent_event', '0003_event_banner_url_event_capacity_event_category_and_more'),
        # game FK targets vent_auth.Games; ensure that table exists first.
        ('vent_auth', '0019_remove_teams_team_privacy_and_more'),
    ]

    operations = [
        migrations.RunPython(reconcile_event_columns, noop),
    ]
