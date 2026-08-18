# Makes Transaction.reference unique so a single Paystack reference can credit
# a wallet at most once (idempotency backstop for topup verify + webhook).
# Existing rows store '' for "no reference"; MySQL allows many NULLs under a
# unique index but not many empty strings, so we null those out first.
from django.db import migrations, models


def nullify_empty_references(apps, schema_editor):
    Transaction = apps.get_model('vent_auth', 'Transaction')
    Transaction.objects.filter(reference='').update(reference=None)


def restore_empty_references(apps, schema_editor):
    Transaction = apps.get_model('vent_auth', 'Transaction')
    Transaction.objects.filter(reference__isnull=True).update(reference='')


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0019_remove_teams_team_privacy_and_more'),
    ]

    operations = [
        # 1. Allow NULL (drop the implicit NOT NULL / default '') before backfill.
        migrations.AlterField(
            model_name='transaction',
            name='reference',
            field=models.CharField(blank=True, default=None, max_length=255, null=True),
        ),
        # 2. Convert legacy empty-string references to NULL so the unique index holds.
        migrations.RunPython(nullify_empty_references, restore_empty_references),
        # 3. Add the unique constraint.
        migrations.AlterField(
            model_name='transaction',
            name='reference',
            field=models.CharField(blank=True, default=None, max_length=255, null=True, unique=True),
        ),
    ]
