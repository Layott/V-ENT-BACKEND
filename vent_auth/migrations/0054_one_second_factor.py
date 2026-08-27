"""Fold the admin's separate authenticator entry into their account's own.

There were two second factors for one person: `AdminTOTP` guarded the console
door and `UserTOTP` guarded the account. That was defensible while the console
had a door of its own. It no longer does - an admin proves the second factor at
the ordinary sign-in - so two secrets for one door would mean an admin holding
two entries in their authenticator app for the same platform and having to know
which one the screen in front of them wanted.

`UserTOTP` is the one that survives, because it is the account's factor and
every member has one, admin or not.

This copies a confirmed `AdminTOTP` across for anybody who has no confirmed
`UserTOTP` of their own, so an existing admin keeps working with the entry
already in their phone and is never asked to enrol again.

Nothing is deleted. The `AdminTOTP` rows stay exactly as they are: if this is
ever read back the wrong way round, the secrets are still there to read.
"""
from django.db import migrations


def fold_admin_factor_into_account(apps, schema_editor):
    AdminTOTP = apps.get_model('vent_auth', 'AdminTOTP')
    UserTOTP = apps.get_model('vent_auth', 'UserTOTP')

    for admin_factor in AdminTOTP.objects.filter(confirmed=True):
        account_factor = UserTOTP.objects.filter(user_id=admin_factor.user_id).first()

        if account_factor is not None and account_factor.confirmed:
            # They already have their own confirmed factor. It wins: it is the
            # one they have been using to sign in to the site.
            continue

        if account_factor is None:
            UserTOTP.objects.create(
                user_id=admin_factor.user_id,
                secret=admin_factor.secret,
                confirmed=True,
                last_used_step=admin_factor.last_used_step,
                confirmed_at=admin_factor.confirmed_at,
            )
            continue

        # Started an enrolment and never finished it. The console entry is the
        # one actually in their phone, so it replaces the half-finished one.
        account_factor.secret = admin_factor.secret
        account_factor.confirmed = True
        account_factor.last_used_step = admin_factor.last_used_step
        account_factor.confirmed_at = admin_factor.confirmed_at
        account_factor.save()


def unfold(apps, schema_editor):
    """Deliberately does nothing.

    Reversing it would mean deleting somebody's working second factor to undo a
    copy, and there is no version of that which is safer than leaving it.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0053_session_2fa_marker'),
    ]

    operations = [
        migrations.RunPython(fold_admin_factor_into_account, unfold),
    ]
