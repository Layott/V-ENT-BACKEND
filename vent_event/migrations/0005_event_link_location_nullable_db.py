# Fixes a schema drift: migration 0002 marked `event_link` and `location`
# null=True in STATE ONLY (SeparateDatabaseAndState with database_operations=[])
# because the hosted DB already had them nullable. Locally (and any DB built
# purely from migrations) 0001 created them NOT NULL and the alter never ran, so
# creating a physical event (event_link=NULL) or a virtual event (location=NULL)
# hit "Column cannot be null". RunSQL forces the real ALTER regardless of the
# state Django already tracks; it is a harmless no-op where the columns are
# already nullable.
from django.db import migrations


def _make_nullable(apps, schema_editor):
    """Force the real ALTER on MySQL; do nothing anywhere else.

    The SQL is MySQL's own MODIFY syntax, so running it verbatim on SQLite -
    which a local development database now uses - fails with a syntax error and
    blocks every migration after it. On SQLite the columns are already nullable
    from the model state, so there is nothing to do.
    """
    if schema_editor.connection.vendor != 'mysql':
        return
    schema_editor.execute("ALTER TABLE vent_event_event MODIFY event_link varchar(255) NULL;")
    schema_editor.execute("ALTER TABLE vent_event_event MODIFY location varchar(255) NULL;")


def _make_not_null(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    schema_editor.execute("ALTER TABLE vent_event_event MODIFY event_link varchar(255) NOT NULL;")
    schema_editor.execute("ALTER TABLE vent_event_event MODIFY location varchar(255) NOT NULL;")


class Migration(migrations.Migration):

    dependencies = [
        ('vent_event', '0004_reconcile_event_columns'),
    ]

    operations = [
        migrations.RunPython(_make_nullable, _make_not_null),
    ]
