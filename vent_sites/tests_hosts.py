"""Which names a site may be, and what a proof of one allows (P8, level 2).

CEO, inbox 480: "we want people to be able to whitelist v-ent on their site or
put tournament registrations and brackets system under their own website from
v-ent." A host is what goes into a frame-ancestors header, so anything that is
not plainly a public name is refused before it is stored.
"""
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from . import hosts
from .hosts import BadHost


class NormaliseTests(SimpleTestCase):
    def code(self, raw):
        with self.assertRaises(BadHost) as caught:
            hosts.normalize(raw)
        return caught.exception.code

    def test_what_people_paste_becomes_a_host(self):
        self.assertEqual(hosts.normalize('TheirSite.com'), 'theirsite.com')
        self.assertEqual(hosts.normalize('  https://www.theirsite.com/tournaments?x=1 '), 'www.theirsite.com')
        self.assertEqual(hosts.normalize('http://cups.theirsite.co.uk/'), 'cups.theirsite.co.uk')
        self.assertEqual(hosts.normalize('theirsite.com.'), 'theirsite.com')

    def test_a_name_in_another_script_is_stored_as_the_browser_sends_it(self):
        self.assertEqual(hosts.normalize('bücher.de'), 'xn--bcher-kva.de')

    def test_addresses_ports_and_credentials_are_refused(self):
        for raw in ('127.0.0.1', '10.0.0.8', 'http://192.168.1.4/', '[::1]', 'theirsite.com:8443',
                    'https://user:pass@theirsite.com/', 'https://theirsite.com:443/'):
            self.assertEqual(self.code(raw), 'SITE_HOST_INVALID', raw)

    def test_local_and_reserved_names_are_refused(self):
        for raw in ('localhost', 'printer.local', 'box.internal', 'shop.test', 'a.localhost', 'intranet'):
            self.assertEqual(self.code(raw), 'SITE_HOST_INVALID', raw)

    def test_shapes_that_are_not_names_are_refused(self):
        for raw in ('', '   ', 'the ir.com', '-bad.com', 'bad-.com', 'a..com', 'ftp://theirsite.com',
                    'javascript:alert(1)', 'theirsite.123', 'x' * 64 + '.com', "theirsite.com';"):
            self.assertEqual(self.code(raw), 'SITE_HOST_INVALID', raw)

    @override_settings(FRONTEND_URL='https://v-ent.co')
    def test_v_ent_is_not_somebody_elses_site(self):
        for raw in ('v-ent.co', 'https://app.v-ent.co/', 'www.v-ent.co'):
            self.assertEqual(self.code(raw), 'SITE_HOST_OURS', raw)


class AncestorTests(SimpleTestCase):
    def site(self, host, method, verified=True):
        return SimpleNamespace(host=host, method=method, verified_at=object() if verified else None)

    def test_a_dns_proof_covers_the_name_and_everything_under_it(self):
        self.assertEqual(hosts.ancestors([self.site('theirsite.com', 'dns')]),
                         ['https://theirsite.com', 'https://*.theirsite.com'])

    def test_a_meta_tag_proves_one_page_so_one_host(self):
        self.assertEqual(hosts.ancestors([self.site('www.theirsite.com', 'meta')]),
                         ['https://www.theirsite.com'])

    def test_an_unproven_site_frames_nothing_and_nothing_repeats(self):
        self.assertEqual(hosts.ancestors([
            self.site('a.com', 'meta'), self.site('b.com', '', verified=False), self.site('a.com', 'meta'),
        ]), ['https://a.com'])
