"""Reconcile the Sponsors table + M2M through table on freshly-built databases.

Migration 0009 created the `Sponsors` model, deleted the old singular `Sponsor`
model, and re-pointed the `Tournament.sponsors` M2M — all in STATE ONLY
(`database_operations=[]`), assuming the then-deployed DB already matched. On a
freshly-built database:

  * `vent_tournament_sponsors` (the Sponsors model's table) is never created, and
  * the M2M through table `vent_tournament_tournament_sponsors` keeps its legacy
    `sponsor_id` column whose FK still points at the old `vent_tournament_sponsor`
    table (not the `sponsors_id` -> `vent_tournament_sponsors.sponsor_id` the
    current state describes).

Consequences on a fresh build:
  * `tournament.sponsors.all()` (get_all_tournaments / view_tournament /
    view_user_drafted_tournaments) errors "table vent_tournament_sponsors
    doesn't exist"; and
  * `create_tournament`'s `tournament.sponsors.add(sponsor)` (a live M1 flow —
    the create wizard has a Sponsors step) raises an FK violation, because the
    through FK still validates against the legacy singular table.

This migration reconciles the DATABASE to the STATE, idempotently:
  1. create `vent_tournament_sponsors` if missing;
  2. rebuild the M2M through table from the current state model so its column is
     `sponsors_id` and its FK points at `vent_tournament_sponsors`.

Idempotency / safety across both worlds:
  * fresh DB  -> table missing + legacy (empty) through  -> create + rebuild;
  * deployed DB already matching (`sponsors_id` present)  -> both checks skip (no-op);
  * a legacy through that somehow holds rows -> abort loudly (rebuilding would drop
    associations that reference the old singular table; that needs a dedicated data
    migration, out of scope here). Not a real state for this project's fresh DB.

It performs no state change (0009 already put the model + M2M into state), so
`makemigrations --check` stays clean and state and database end up in agreement.
"""
from django.db import migrations


def reconcile_sponsors(apps, schema_editor):
    conn = schema_editor.connection

    # 1) Ensure the Sponsors model's table exists.
    Sponsors = apps.get_model('vent_tournament', 'Sponsors')
    sponsors_table = Sponsors._meta.db_table
    if sponsors_table not in set(conn.introspection.table_names()):
        schema_editor.create_model(Sponsors)

    # 2) Reconcile the Tournament.sponsors M2M through table to the state model.
    Tournament = apps.get_model('vent_tournament', 'Tournament')
    through = Tournament._meta.get_field('sponsors').remote_field.through
    through_table = through._meta.db_table

    if through_table not in set(conn.introspection.table_names()):
        schema_editor.create_model(through)
        return

    with conn.cursor() as cursor:
        cols = {c.name for c in conn.introspection.get_table_description(cursor, through_table)}
    if 'sponsors_id' in cols:
        return  # already reconciled (deployed-correct or a prior run) -> no-op

    # Legacy structure: column `sponsor_id`, FK -> old `vent_tournament_sponsor`.
    with conn.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM {through_table}')
        row_count = cursor.fetchone()[0]
    if row_count:
        raise RuntimeError(
            f"{through_table} holds {row_count} legacy row(s) referencing the old "
            "vent_tournament_sponsor table. Rebuilding would drop those associations; "
            "a dedicated data migration is required first. Aborting to avoid data loss."
        )

    # Empty legacy through -> rebuild cleanly so column + FK match current state.
    schema_editor.delete_model(through)
    schema_editor.create_model(through)


def noop(apps, schema_editor):
    # Non-destructive reverse: leave the reconciled schema in place.
    pass


class Migration(migrations.Migration):

    # This RunPython issues raw schema_editor DDL (create_model / delete_model).
    # On MySQL (no transactional DDL) an atomic migration wraps the RunPython op in
    # a transaction, and executing DDL inside it raises TransactionManagementError
    # ("Executing DDL statements while in a transaction on databases that can't
    # perform a rollback is prohibited"). atomic=False runs the DDL outside a
    # transaction. Safe because the reconcile is idempotent (introspection-guarded).
    atomic = False

    dependencies = [
        ('vent_tournament', '0010_bracketmatch_bracket_side_bracketmatch_is_final_and_more'),
    ]

    operations = [
        migrations.RunPython(reconcile_sponsors, noop),
    ]
