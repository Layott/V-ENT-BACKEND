"""Signing secrets at rest, signed webhooks, and the addresses we will call.

Design section 14, items 1 and 5: a signing secret encrypted at rest (owner rule
R63), and every webhook "signed with HMAC SHA-256 over method, path, timestamp,
host and body hash; five minute replay window" (R72), in the form Challengermode
uses rather than Toornament's sha256(body + secret).
"""
import base64
import hashlib
import hmac
import os
from unittest import mock

from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from vent_partners import studio_secrets, studio_signing, studio_urls


class SecretTests(SimpleTestCase):
    def test_a_secret_comes_back_from_its_ciphertext(self):
        secret = studio_secrets.new_signing_secret()
        token = studio_secrets.encrypt(secret)
        self.assertEqual(studio_secrets.decrypt(token), secret)

    def test_a_new_secret_says_what_it_is(self):
        self.assertTrue(studio_secrets.new_signing_secret().startswith('vent_whsec_'))
        self.assertNotEqual(studio_secrets.new_signing_secret(),
                            studio_secrets.new_signing_secret())

    def test_the_ciphertext_never_contains_the_secret(self):
        secret = studio_secrets.new_signing_secret()
        token = studio_secrets.encrypt(secret)
        self.assertNotIn(secret, token)
        self.assertNotIn(secret[len('vent_whsec_'):], token)

    def test_an_older_key_still_opens_what_it_locked(self):
        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        with mock.patch.dict(os.environ, {'STUDIO_SECRETS_KEY': old}):
            token = studio_secrets.encrypt('vent_whsec_abc')
        with mock.patch.dict(os.environ, {'STUDIO_SECRETS_KEY': '%s,%s' % (new, old)}):
            self.assertEqual(studio_secrets.decrypt(token), 'vent_whsec_abc')
            # And the first key is the one that locks from now on.
            fresh = studio_secrets.encrypt('vent_whsec_def')
        with mock.patch.dict(os.environ, {'STUDIO_SECRETS_KEY': new}):
            self.assertEqual(studio_secrets.decrypt(fresh), 'vent_whsec_def')

    def test_a_wrong_key_is_a_coded_refusal_not_a_crash(self):
        with mock.patch.dict(os.environ, {'STUDIO_SECRETS_KEY': Fernet.generate_key().decode()}):
            token = studio_secrets.encrypt('vent_whsec_abc')
        with mock.patch.dict(os.environ, {'STUDIO_SECRETS_KEY': Fernet.generate_key().decode()}):
            with self.assertRaises(studio_secrets.SecretUnreadable):
                studio_secrets.decrypt(token)

    def test_without_a_key_one_is_derived_from_the_site_secret(self):
        env = {k: v for k, v in os.environ.items() if k != 'STUDIO_SECRETS_KEY'}
        with mock.patch.dict(os.environ, env, clear=True):
            token = studio_secrets.encrypt('vent_whsec_abc')
            self.assertEqual(studio_secrets.decrypt(token), 'vent_whsec_abc')


METHOD, PATH, TS, HOST = 'POST', '/hook?x=1', 1760659200, 'studio.example'
BODY = b'{"a":1}'
SECRET = 's3cret'


def by_hand(secret=SECRET, method=METHOD, path=PATH, ts=TS, host=HOST, body=BODY):
    content = base64.b64encode(hashlib.sha256(body).digest()).decode()
    text = '%s\n%s\n%s;%s;%s' % (method, path, ts, host, content)
    return base64.b64encode(hmac.new(secret.encode(), text.encode(), hashlib.sha256).digest()).decode()


def headers_for(signature, body=BODY, ts=TS):
    return {
        'V-Ent-Timestamp': str(ts),
        'V-Ent-Content-SHA256': base64.b64encode(hashlib.sha256(body).digest()).decode(),
        'V-Ent-Signature': 'v1=%s' % signature,
        'V-Ent-Delivery': 'dl_0123456789abcdef',
    }


class SignatureTests(SimpleTestCase):
    def test_the_signature_is_the_documented_one(self):
        self.assertEqual(studio_signing.sign(SECRET, METHOD, PATH, TS, HOST, BODY), by_hand())

    def test_the_string_to_sign_is_the_documented_one(self):
        content = studio_signing.body_hash(BODY)
        self.assertEqual(
            studio_signing.string_to_sign('post', PATH, TS, HOST, content),
            'POST\n/hook?x=1\n1760659200;studio.example;%s' % content)

    def test_outbound_headers_carry_one_signature_per_secret(self):
        headers = studio_signing.outbound_headers(
            [SECRET, 'older'], METHOD, 'https://studio.example/hook?x=1', BODY,
            delivery_ref='dl_abc', now=TS)
        self.assertEqual(headers['V-Ent-Timestamp'], str(TS))
        self.assertEqual(headers['V-Ent-Delivery'], 'dl_abc')
        self.assertEqual(headers['V-Ent-Signature'],
                         'v1=%s,v1=%s' % (by_hand(), by_hand(secret='older')))
        self.assertEqual(headers['Content-Type'], 'application/json')

    def test_a_fresh_signature_is_accepted(self):
        studio_signing.verify([SECRET], METHOD, PATH, HOST, headers_for(by_hand()), BODY,
                              now=TS + 299)

    def test_a_signature_past_five_minutes_is_stale(self):
        with self.assertRaises(studio_signing.SignatureError) as caught:
            studio_signing.verify([SECRET], METHOD, PATH, HOST, headers_for(by_hand()), BODY,
                                  now=TS + 301)
        self.assertEqual(caught.exception.code, 'SIGNATURE_STALE')

    def test_a_signature_from_the_future_is_stale_too(self):
        with self.assertRaises(studio_signing.SignatureError) as caught:
            studio_signing.verify([SECRET], METHOD, PATH, HOST, headers_for(by_hand()), BODY,
                                  now=TS - 301)
        self.assertEqual(caught.exception.code, 'SIGNATURE_STALE')

    def test_a_changed_body_is_caught(self):
        with self.assertRaises(studio_signing.SignatureError) as caught:
            studio_signing.verify([SECRET], METHOD, PATH, HOST, headers_for(by_hand()),
                                  b'{"a":2}', now=TS)
        self.assertEqual(caught.exception.code, 'CONTENT_HASH_MISMATCH')

    def test_a_changed_path_is_caught(self):
        with self.assertRaises(studio_signing.SignatureError) as caught:
            studio_signing.verify([SECRET], METHOD, '/hook?x=2', HOST, headers_for(by_hand()),
                                  BODY, now=TS)
        self.assertEqual(caught.exception.code, 'SIGNATURE_INVALID')

    def test_a_changed_host_is_caught(self):
        with self.assertRaises(studio_signing.SignatureError) as caught:
            studio_signing.verify([SECRET], METHOD, PATH, 'evil.example', headers_for(by_hand()),
                                  BODY, now=TS)
        self.assertEqual(caught.exception.code, 'SIGNATURE_INVALID')

    def test_either_secret_during_a_rotation_is_accepted(self):
        headers = headers_for(by_hand(secret='older'))
        studio_signing.verify([SECRET, 'older'], METHOD, PATH, HOST, headers, BODY, now=TS)

    def test_the_second_of_two_signatures_may_be_the_good_one(self):
        headers = headers_for(by_hand())
        headers['V-Ent-Signature'] = 'v1=%s,v1=%s' % (by_hand(secret='nope'), by_hand())
        studio_signing.verify([SECRET], METHOD, PATH, HOST, headers, BODY, now=TS)

    def test_missing_headers_are_missing(self):
        for name in ('V-Ent-Timestamp', 'V-Ent-Signature', 'V-Ent-Content-SHA256'):
            headers = headers_for(by_hand())
            headers.pop(name)
            with self.assertRaises(studio_signing.SignatureError) as caught:
                studio_signing.verify([SECRET], METHOD, PATH, HOST, headers, BODY, now=TS)
            self.assertEqual(caught.exception.code, 'SIGNATURE_MISSING', name)

    def test_a_timestamp_that_is_not_a_number_is_missing(self):
        headers = headers_for(by_hand())
        headers['V-Ent-Timestamp'] = 'yesterday'
        with self.assertRaises(studio_signing.SignatureError) as caught:
            studio_signing.verify([SECRET], METHOD, PATH, HOST, headers, BODY, now=TS)
        self.assertEqual(caught.exception.code, 'SIGNATURE_MISSING')

    def test_no_secret_at_all_never_verifies(self):
        with self.assertRaises(studio_signing.SignatureError) as caught:
            studio_signing.verify([], METHOD, PATH, HOST, headers_for(by_hand()), BODY, now=TS)
        self.assertEqual(caught.exception.code, 'SIGNATURE_INVALID')


class WebhookAddressTests(SimpleTestCase):
    def test_a_public_https_address_is_fine(self):
        self.assertIsNone(studio_urls.webhook_url_problem('https://api.studio.example/vent/hook'))

    def test_private_hosts_are_refused(self):
        cases = {
            'https://10.0.0.5/x': 'URL_PRIVATE_HOST',
            'https://192.168.1.4/x': 'URL_PRIVATE_HOST',
            'https://169.254.169.254/latest/meta-data': 'URL_PRIVATE_HOST',
            'https://[::1]/x': 'URL_PRIVATE_HOST',
            'https://127.0.0.1/x': 'URL_PRIVATE_HOST',
            'https://localhost/x': 'URL_PRIVATE_HOST',
            'https://user:pw@host.example/x': 'URL_HAS_CREDENTIALS',
            'http://studio.example/x': 'URL_NOT_HTTPS',
            'ftp://studio.example/x': 'URL_NOT_HTTPS',
            'https://studio.example/' + 'a' * 300: 'URL_TOO_LONG',
            'https:///nohost': 'URL_NOT_HTTPS',
        }
        for url, code in cases.items():
            self.assertEqual(studio_urls.webhook_url_problem(url), code, url)

    @override_settings(DEBUG=True)
    def test_localhost_over_http_is_allowed_while_developing(self):
        self.assertIsNone(studio_urls.webhook_url_problem('http://localhost:9000/hook'))
        self.assertIsNone(studio_urls.webhook_url_problem('http://127.0.0.1:9000/hook'))

    @override_settings(DEBUG=False)
    def test_localhost_is_refused_in_production(self):
        self.assertEqual(studio_urls.webhook_url_problem('http://localhost:9000/hook'),
                         'URL_NOT_HTTPS')

    def test_send_time_check_reads_every_address_the_name_resolves_to(self):
        public = [(2, 1, 6, '', ('93.184.216.34', 443))]
        mixed = public + [(2, 1, 6, '', ('10.0.0.8', 443))]
        with mock.patch('vent_partners.studio_urls.socket.getaddrinfo', return_value=public):
            self.assertTrue(studio_urls.resolves_public('https://studio.example/hook'))
        with mock.patch('vent_partners.studio_urls.socket.getaddrinfo', return_value=mixed):
            self.assertFalse(studio_urls.resolves_public('https://studio.example/hook'))

    def test_a_name_that_does_not_resolve_is_not_public(self):
        import socket
        with mock.patch('vent_partners.studio_urls.socket.getaddrinfo',
                        side_effect=socket.gaierror('nope')):
            self.assertFalse(studio_urls.resolves_public('https://nowhere.example/hook'))
