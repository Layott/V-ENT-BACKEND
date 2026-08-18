# Fixes a schema drift: migration 0002 marked `event_link` and `location`
# null=True in STATE ONLY (SeparateDatabaseAndState with database_operations=[])
# because the hosted DB already had them nullable. Locally (and any DB built
# purely from migrations) 0001 created them NOT NULL and the alter never ran, so
# creating a physical event (event_link=NULL) or a virtual event (location=NULL)
# hit "Column cannot be null". RunSQL forces the real ALTER regardless of the
# state Django already tracks; it is a harmless no-op where the columns are
# already nullable.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('vent_event', '0004_reconcile_event_columns'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE vent_event_event MODIFY event_link varchar(255) NULL;",
                "ALTER TABLE vent_event_event MODIFY location varchar(255) NULL;",
            ],
            reverse_sql=[
                "ALTER TABLE vent_event_event MODIFY event_link varchar(255) NOT NULL;",
                "ALTER TABLE vent_event_event MODIFY location varchar(255) NOT NULL;",
            ],
        ),
    ]
