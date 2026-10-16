"""Strip spaces from stored usernames (inbox 461, 16 October 2026).

The onboarding door (edit_profile_info) saved a username exactly as typed, so
one account was stored as "keiji_is_lost " and its profile never opened:
/u/<name> strips the space and finds nobody. The door is fixed in the same
change; this repairs what it already wrote. A stripped name somebody else
already holds is left alone and printed, never merged.
"""
from django.db import migrations


def strip_usernames(apps, schema_editor):
    Users = apps.get_model('vent_auth', 'Users')
    for user in Users.objects.all().only('user_id', 'username'):
        name = user.username or ''
        clean = ''.join(name.split())      # every space, tab or newline out
        if clean == name or not clean:
            continue
        if Users.objects.filter(username__iexact=clean).exclude(pk=user.pk).exists():
            print('  username %r kept: %r is taken' % (name, clean))
            continue
        user.username = clean
        user.save(update_fields=['username'])


class Migration(migrations.Migration):

    dependencies = [
        ('vent_auth', '0092_role_invite'),
    ]

    operations = [
        migrations.RunPython(strip_usernames, migrations.RunPython.noop),
    ]
