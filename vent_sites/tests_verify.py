"""Proving a site: a DNS TXT record or a meta tag (P8, level 2).

The meta tag check makes the server fetch a page somebody else names, which is
the shape of a server-side request forgery. So these tests are mostly about
what it must NOT do: reach a private address, follow a redirect somewhere
else, or read without limit. Nothing here touches the network: the resolver,
the address lookup and the fetch are replaced.
"""
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from . import verify

TOKEN = 'tok3n-AbC_123'
TAG = '<html><head><meta name="vent-verify" content="%s"></head><body>Hi</body></html>' % TOKEN
PUBLIC = ['93.184.216.34']


class FakeResponse:
    def __init__(self, status=200, body='', location=None):
        self.status_code = status
        self.headers = {'Location': location} if location else {}
        self.encoding = 'utf-8'
        self._body = body.encode('utf-8') if isinstance(body, str) else body
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


def a_site(host='theirsite.com'):
    return SimpleNamespace(host=host, token=TOKEN)


class DnsTests(SimpleTestCase):
    def test_the_record_proves_the_site(self):
        with patch.object(verify, 'txt_records', return_value=['v=spf1 -all', 'vent-verify=' + TOKEN]) as txt:
            self.assertEqual(verify.check(a_site(), 'dns'), (True, 'dns'))
        txt.assert_called_once_with('_vent-verify.theirsite.com')

    def test_somebody_elses_token_proves_nothing(self):
        with patch.object(verify, 'txt_records', return_value=['vent-verify=other']):
            self.assertEqual(verify.check(a_site(), 'dns'), (False, 'SITE_DNS_MISSING'))

    def test_a_resolver_that_does_not_answer_is_unreachable(self):
        with patch.object(verify, 'txt_records', side_effect=verify.Unreachable()):
            self.assertEqual(verify.check(a_site(), 'dns'), (False, 'SITE_UNREACHABLE'))


class MetaTests(SimpleTestCase):
    def run_check(self, responses, addresses=PUBLIC, method='meta', host='theirsite.com'):
        pages = iter(responses)
        with patch.object(verify, 'addresses', return_value=addresses), \
             patch.object(verify, '_get', side_effect=lambda url: next(pages)) as get:
            return verify.check(a_site(host), method), get

    def test_the_tag_proves_the_site(self):
        result, get = self.run_check([FakeResponse(200, TAG)])
        self.assertEqual(result, (True, 'meta'))
        get.assert_called_once_with('https://theirsite.com/')

    def test_a_private_address_is_refused_before_any_request(self):
        for addresses in (['127.0.0.1'], ['10.1.2.3'], ['169.254.169.254'], ['192.168.0.9'], ['::1'],
                          ['::ffff:10.0.0.1'], ['fd00::1'], ['93.184.216.34', '10.0.0.1'], ['0.0.0.0']):
            result, get = self.run_check([FakeResponse(200, TAG)], addresses=addresses)
            self.assertEqual(result, (False, 'SITE_ADDRESS_PRIVATE'), addresses)
            get.assert_not_called()

    def test_a_name_with_no_address_is_unreachable(self):
        result, get = self.run_check([FakeResponse(200, TAG)], addresses=[])
        self.assertEqual(result, (False, 'SITE_UNREACHABLE'))
        get.assert_not_called()

    def test_the_www_twin_is_followed_and_checked_again(self):
        result, get = self.run_check([
            FakeResponse(301, location='https://www.theirsite.com/'),
            FakeResponse(200, TAG),
        ])
        self.assertEqual(result, (True, 'meta'))
        self.assertEqual([c.args[0] for c in get.call_args_list],
                         ['https://theirsite.com/', 'https://www.theirsite.com/'])

    def test_a_redirect_anywhere_else_is_refused(self):
        for location in ('https://evil.example.org/', 'http://www.theirsite.com/',
                         'https://theirsite.com.evil.org/', 'https://www.www.theirsite.com/'):
            result, _get = self.run_check([FakeResponse(302, location=location), FakeResponse(200, TAG)])
            self.assertEqual(result, (False, 'SITE_REDIRECT_AWAY'), location)

    def test_a_redirect_loop_stops(self):
        loop = [FakeResponse(301, location='https://www.theirsite.com/'),
                FakeResponse(301, location='https://theirsite.com/')] * 5
        result, get = self.run_check(loop)
        self.assertEqual(result, (False, 'SITE_REDIRECT_AWAY'))
        self.assertLessEqual(get.call_count, 4)

    def test_only_the_start_of_a_page_is_read(self):
        huge = '<html><body>' + ('x' * (verify.MAX_BYTES + 10)) + '</body>' + TAG
        result, _get = self.run_check([FakeResponse(200, huge)])
        self.assertEqual(result, (False, 'SITE_META_MISSING'))

    def test_a_page_that_does_not_answer_is_unreachable(self):
        result, _get = self.run_check([FakeResponse(500, TAG)])
        self.assertEqual(result, (False, 'SITE_UNREACHABLE'))
        with patch.object(verify, 'addresses', return_value=PUBLIC), \
             patch.object(verify, '_get', side_effect=requests.ConnectionError('down')):
            self.assertEqual(verify.check(a_site(), 'meta'), (False, 'SITE_UNREACHABLE'))


class BothTests(SimpleTestCase):
    def test_with_no_method_dns_then_the_tag(self):
        with patch.object(verify, 'txt_records', return_value=[]), \
             patch.object(verify, 'addresses', return_value=PUBLIC), \
             patch.object(verify, '_get', return_value=FakeResponse(200, TAG)):
            self.assertEqual(verify.check(a_site()), (True, 'meta'))

    def test_neither_found_says_so(self):
        with patch.object(verify, 'txt_records', return_value=[]), \
             patch.object(verify, 'addresses', return_value=PUBLIC), \
             patch.object(verify, '_get', return_value=FakeResponse(200, '<html></html>')):
            self.assertEqual(verify.check(a_site()), (False, 'SITE_NOT_PROVEN'))

    def test_a_refused_page_is_the_reason_given(self):
        with patch.object(verify, 'txt_records', return_value=[]), \
             patch.object(verify, 'addresses', return_value=['10.0.0.1']):
            self.assertEqual(verify.check(a_site()), (False, 'SITE_ADDRESS_PRIVATE'))


class FindMetaTests(SimpleTestCase):
    def test_the_shapes_a_page_writes_it_in(self):
        for html in ("<meta content='%s' name='vent-verify'>" % TOKEN,
                     '<META NAME="Vent-Verify" CONTENT="%s" />' % TOKEN,
                     '<meta name=vent-verify content=%s>' % TOKEN,
                     '<meta data-x="1" name="vent-verify"\n content=" %s ">' % TOKEN):
            self.assertTrue(verify.find_meta(html, TOKEN), html)

    def test_the_token_anywhere_else_proves_nothing(self):
        for html in ('<p>%s</p>' % TOKEN,
                     '<meta name="description" content="%s">' % TOKEN,
                     '<!-- <meta name="vent-verify" content="other"> --> %s' % TOKEN,
                     '<meta name="vent-verify" content="%sX">' % TOKEN):
            self.assertFalse(verify.find_meta(html, TOKEN), html)
