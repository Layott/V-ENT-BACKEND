"""Test settings for vent_tournament.

The production database (remote MySQL) is unreachable from this dev machine, so
tests run against in-memory SQLite with migrations disabled - the schema is built
straight from the current models, which is what these bracket/lifecycle tests
exercise.

Run:  python manage.py test vent_tournament --settings=vent_tournament.test_settings
"""
from vent.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}


class _DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _DisableMigrations()
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
DEFAULT_FILE_STORAGE = 'django.core.files.storage.InMemoryStorage'
