"""The partners console page must accept the admin console's own grant.

`_admin` here used to resolve `_user_from_bearer`, which reads
`login_session_token` - the WEBSITE session. That worked only while the console
token and the site token were the same value. Once the console got its own
grant, this endpoint answered 401 and `useAdminAuth` reacted the way it reacts to
any 401: it cleared the grant and bounced the admin to the login screen. So the
partners page threw the admin out while every other admin page loaded fine.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from vent_auth.models import Users


def make_admin(**overrides):
    values = dict(
        username='partner_admin',
        email='partner_admin@example.com',
        is_staff=True,
        admin_role='super_admin',
        login_session_token='the-website-session',
        admin_session_token='the-console-grant',
    )
    values.update(overrides)
    user = Users.objects.create(**values)
    user.login_session_created_at = timezone.now()
    user.admin_session_created_at = timezone.now()
    user.save(update_fields=['login_session_created_at', 'admin_session_created_at'])
    return user


class PartnersAdminGrantTests(TestCase):
    def test_the_console_grant_opens_the_partners_list(self):
        make_admin()

        response = self.client.get('/partners/admin/list/',
                                   HTTP_AUTHORIZATION='Bearer the-console-grant')

        self.assertEqual(response.status_code, 200)

    def test_the_website_session_does_not_open_it(self):
        """The site session is not an admin credential, even for an admin.

        Reading it here is what coupled the two in the first place.
        """
        make_admin()

        response = self.client.get('/partners/admin/list/',
                                   HTTP_AUTHORIZATION='Bearer the-website-session')

        self.assertEqual(response.status_code, 401)

    def test_a_non_admin_is_refused(self):
        make_admin(username='ordinary', email='ordinary@example.com',
                   is_staff=False, admin_role=None,
                   admin_session_token='not-an-admin-grant')

        response = self.client.get('/partners/admin/list/',
                                   HTTP_AUTHORIZATION='Bearer not-an-admin-grant')

        self.assertIn(response.status_code, (401, 403))
