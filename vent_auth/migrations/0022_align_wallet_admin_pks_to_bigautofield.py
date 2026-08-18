# Created 2026-07-06 (BE-auth): align the four wallet/admin PKs to BigAutoField
# on freshly-built databases so cross-app BigAutoField FKs can reference them.
from django.db import migrations, models


def _int_pk():
    return models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')


def _big_pk():
    return models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')


class Migration(migrations.Migration):
    """PK column-type reconciliation for the wallet/admin models.

    `Transaction` (migration 0018) and `WithdrawalRequest` / `KYCDocument` /
    `AdminAction` (migration 0017) were all created with INT `AutoField` PKs.
    Migration 0019 then moved those four PKs to `BigAutoField` in STATE ONLY
    (`SeparateDatabaseAndState(database_operations=[])`), on the assumption the
    then-deployed DB already had BIGINT columns. On a freshly-built database the
    columns are therefore still INT while the migration state claims BigAutoField.

    Consequence on a fresh build: `vent_tournament.PrizePayout.transaction`
    (a BigAutoField-state FK -> `vent_auth.Transaction`) emits a BIGINT column
    that MySQL refuses to point at the INT `transaction.id` (error 3780) — the
    failure the fresh vent_mysql:3307 migrate hit. `DEFAULT_AUTO_FIELD` is
    BigAutoField and none of these models declare an explicit `id`, so the model
    state legitimately wants BigAutoField; the DATABASE is what is out of step.

    Fix (mirrors vent_tournament/0010): rewind the STATE to AutoField (state-only,
    a no-op DDL-wise), then run a REAL `AlterField` -> BigAutoField. Django then
    performs the INT->BIGINT column change and cascades it to any referencing FK
    columns. A plain `AlterField(->BigAutoField)` without the rewind would be a
    state no-op (state is already BigAutoField) and emit no DDL. On a database
    whose columns are already BIGINT this simply re-alters BIGINT->BIGINT
    (harmless). `state_operations` net to zero, so `makemigrations --check` stays
    clean (final state == model == BigAutoField).

    Ordering: this migration MUST run before vent_tournament/0010 creates
    PrizePayout on a fresh DB. vent_tournament/0010's dependency is bumped from
    vent_auth 0020 -> 0022 (coordinated with BE-tournament) so the alter lands
    first.
    """

    dependencies = [
        ('vent_auth', '0021_users_admin_role'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField('transaction', 'id', _int_pk()),
                migrations.AlterField('withdrawalrequest', 'id', _int_pk()),
                migrations.AlterField('kycdocument', 'id', _int_pk()),
                migrations.AlterField('adminaction', 'id', _int_pk()),
            ],
            database_operations=[],
        ),
        migrations.AlterField('transaction', 'id', _big_pk()),
        migrations.AlterField('withdrawalrequest', 'id', _big_pk()),
        migrations.AlterField('kycdocument', 'id', _big_pk()),
        migrations.AlterField('adminaction', 'id', _big_pk()),
    ]
