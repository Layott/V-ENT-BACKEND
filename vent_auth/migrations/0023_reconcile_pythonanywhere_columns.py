# Created 2026-07-06 (BE-auth): reconcile the columns/table that 0019 added in
# STATE ONLY so freshly-built databases actually get them.
from django.db import migrations


def _columns(schema_editor, table):
    conn = schema_editor.connection
    with conn.cursor() as cursor:
        return {c.name for c in conn.introspection.get_table_description(cursor, table)}


def reconcile(apps, schema_editor):
    """Add the four Users columns, the six Teams columns, and the UserGallery
    table that migration 0019 declared with `database_operations=[]` (state only,
    on the assumption the then-deployed Railway/pythonanywhere DB already had
    them). On a freshly-built database none of them exist, so:

      * every authenticated request 500s (`Users.login_session_created_at` is read
        on the 120-min session-expiry check),
      * the gallery endpoints 500 (`vent_auth_usergallery` missing), and
      * team create / listing / serialization 500 (six Teams columns missing).

    Idempotent and safe on both worlds: each column/table is introspected and only
    added when absent, so on the deployed DB (which already has them) this is a
    pure no-op. Performs no state change (0019 already put these in state), so
    `makemigrations --check` stays clean.

    The fields are pulled from the historical models, so the exact definitions 0019
    put into state (defaults, null, upload_to) are reused verbatim.
    """
    conn = schema_editor.connection
    existing_tables = set(conn.introspection.table_names())

    # --- Users: 4 columns ---
    # Re-introspect before each add: on SQLite, adding a field that has a default
    # (or is NOT NULL) remakes the whole table from state and materializes the
    # remaining state-only columns in one shot, so a guard captured once up front
    # goes stale and the next add duplicates. MySQL adds one column per ALTER
    # (no remake); per-column introspection is correct on both.
    Users = apps.get_model('vent_auth', 'Users')
    users_table = Users._meta.db_table
    if users_table in existing_tables:
        for fname in ('login_session_created_at', 'social_id', 'state', 'tst'):
            field = Users._meta.get_field(fname)
            if field.column not in _columns(schema_editor, users_table):
                schema_editor.add_field(Users, field)

    # --- Teams: 6 columns ---
    Teams = apps.get_model('vent_auth', 'Teams')
    teams_table = Teams._meta.db_table
    if teams_table in existing_tables:
        for fname in ('allow_membership_requests', 'description', 'number_of_members',
                      'penalty_points', 'team_banner', 'team_logo'):
            field = Teams._meta.get_field(fname)
            if field.column not in _columns(schema_editor, teams_table):
                schema_editor.add_field(Teams, field)

    # --- UserGallery: whole table ---
    UserGallery = apps.get_model('vent_auth', 'UserGallery')
    if UserGallery._meta.db_table not in existing_tables:
        schema_editor.create_model(UserGallery)

    # Orphan `vent_auth_teams.team_privacy`: 0019 removed it in STATE ONLY, so the
    # physical column survives on both fresh and deployed DBs. Intentionally LEFT in
    # place (not dropped): it is CharField(default='public', max_length=7); the
    # DB-level default keeps Django INSERTs (which omit it) working, so it is inert.
    # Dropping it would be irreversible on the deployed DB for no functional gain.


def noop(apps, schema_editor):
    # Non-destructive reverse: leave the reconciled schema in place.
    pass


class Migration(migrations.Migration):

    # MySQL has no transactional DDL: schema_editor DDL inside RunPython must
    # not run inside the transaction Django otherwise forces around it.
    atomic = False

    dependencies = [
        ('vent_auth', '0022_align_wallet_admin_pks_to_bigautofield'),
    ]

    operations = [
        migrations.RunPython(reconcile, noop),
    ]
