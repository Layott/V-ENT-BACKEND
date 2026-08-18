from django.db import migrations


CHECK_SQL = (
    "SELECT COUNT(*) FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vent_auth_teams' "
    "AND COLUMN_NAME = 'team_privacy'"
)


def drop_team_privacy(apps, schema_editor):
    """Reconcile state-only drift: migration 0019 removed `team_privacy` from the
    Teams model STATE but never dropped the column from the DB. On every migrated
    DB the column therefore survives as NOT NULL with no default, which blocks
    every Teams INSERT (create_team 500'd with MySQL 1364 — this is why zero teams
    ever existed). Drop it if present. Idempotent + no-op on fresh/non-MySQL DBs.
    """
    conn = schema_editor.connection
    if conn.vendor != 'mysql':
        return
    with conn.cursor() as cursor:
        cursor.execute(CHECK_SQL)
        if cursor.fetchone()[0]:
            cursor.execute("ALTER TABLE vent_auth_teams DROP COLUMN team_privacy")


class Migration(migrations.Migration):
    # MySQL has no transactional DDL; RunPython that issues DDL must not be wrapped
    # in a transaction (SQLite blind spot documented in tasks/lessons.md).
    atomic = False

    dependencies = [
        ('vent_auth', '0026_teammembers_role_teams_join_password_and_more'),
    ]

    operations = [
        migrations.RunPython(drop_team_privacy, migrations.RunPython.noop),
    ]
