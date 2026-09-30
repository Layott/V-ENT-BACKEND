"""Django's own /admin/ is not mounted in production (inbox 400).

Uploaded overlay HTML runs its script on the API's origin by design; a staff
member signed in to Django's admin on that origin would have lent it their
session. The switch is DJANGO_ADMIN_ENABLED, on by default only with DEBUG.
"""
import importlib

from django.test import TestCase, override_settings
from django.urls import clear_url_caches

import vent.urls


def _reload():
    clear_url_caches()
    importlib.reload(vent.urls)


class DjangoAdminMountTests(TestCase):
    def tearDown(self):
        _reload()

    @override_settings(DJANGO_ADMIN_ENABLED=False, ROOT_URLCONF='vent.urls')
    def test_off_answers_404(self):
        _reload()
        self.assertEqual(self.client.get('/admin/login/').status_code, 404)

    @override_settings(DJANGO_ADMIN_ENABLED=True, ROOT_URLCONF='vent.urls')
    def test_on_when_asked_for(self):
        _reload()
        self.assertEqual(self.client.get('/admin/login/').status_code, 200)
