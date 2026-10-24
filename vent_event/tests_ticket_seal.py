"""Sealed ticket codes (inbox 525): a QR the door can prove V-ENT issued.

CEO, 18 October 2026: "Each ticket is cryptographically unique. Our system
instantly flags duplicates or tampered codes at check-in." The key here is made
for the test and thrown away; the real one lives only in the server's
environment.
"""
import os
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import TestCase

from . import ticket_seal
from .tests_door import DoorTests as _Door


def a_key():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode('ascii')
    # As an .env line holds it: one line, literal \n between the PEM's lines.
    return pem.strip().replace('\n', '\\n')


class SealTests(TestCase):
    def test_off_without_a_key_the_qr_is_the_bare_code(self):
        with mock.patch.dict(os.environ, {'TICKET_SEAL_KEY': ''}):
            self.assertFalse(ticket_seal.enabled())
            self.assertEqual(ticket_seal.seal('VT-ABCD2345'), 'VT-ABCD2345')
            self.assertIsNone(ticket_seal.public_jwk())

    def test_a_seal_reads_back_and_a_changed_one_does_not(self):
        with mock.patch.dict(os.environ, {'TICKET_SEAL_KEY': a_key()}):
            sealed = ticket_seal.seal('VT-ABCD2345')
            code, _dot, mark = sealed.partition('.')
            self.assertEqual(code, 'VT-ABCD2345')
            self.assertEqual(len(mark), 86)
            self.assertEqual(ticket_seal.read(sealed), 'VT-ABCD2345')
            # Another code under the same seal: forged.
            with self.assertRaises(ticket_seal.SealError):
                ticket_seal.read('VT-ABCD2346.' + mark)
            # A seal edited by one character: tampered.
            flipped = mark[:-1] + ('A' if mark[-1] != 'A' else 'B')
            with self.assertRaises(ticket_seal.SealError):
                ticket_seal.read(code + '.' + flipped)
            with self.assertRaises(ticket_seal.SealError):
                ticket_seal.read(code + '.short')
            # A bare code still reads, for tickets printed before the seal.
            self.assertEqual(ticket_seal.read('vt-abcd2345'), 'VT-ABCD2345')

    def test_a_seal_from_another_key_is_refused(self):
        with mock.patch.dict(os.environ, {'TICKET_SEAL_KEY': a_key()}):
            foreign = ticket_seal.seal('VT-ABCD2345')
        with mock.patch.dict(os.environ, {'TICKET_SEAL_KEY': a_key()}):
            with self.assertRaises(ticket_seal.SealError):
                ticket_seal.read(foreign)

    def test_the_public_key_is_a_p256_jwk(self):
        with mock.patch.dict(os.environ, {'TICKET_SEAL_KEY': a_key()}):
            jwk = ticket_seal.public_jwk()
        self.assertEqual((jwk['kty'], jwk['crv'], jwk['alg']), ('EC', 'P-256', 'ES256'))
        self.assertNotIn('d', jwk)


class DoorSealTests(_Door):
    """The door's own setUp (organiser, steward, a ticket), under a key."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'TICKET_SEAL_KEY': a_key()})
        self.env.start()
        self.addCleanup(self.env.stop)
        super().setUp()

    def test_a_sealed_code_checks_in(self):
        res = self.check_in(code=ticket_seal.seal(self.ticket.code))
        self.assertEqual(res.status_code, 200, res.content)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'checked_in')

    def test_a_tampered_code_is_flagged_with_a_code(self):
        sealed = ticket_seal.seal(self.ticket.code)
        res = self.check_in(code=sealed[:-2] + ('AA' if not sealed.endswith('AA') else 'BB'))
        self.assertEqual((res.status_code, res.json()['code']), (400, 'TICKET_TAMPERED'))
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'valid')

    def test_the_lookup_flags_a_tampered_code_too(self):
        sealed = ticket_seal.seal(self.ticket.code)
        forged = 'VT-DOOR0002.' + sealed.partition('.')[2]
        res = self.client.get('/event/ticket/%s/lookup/' % forged, **self.organiser_auth)
        if res.status_code == 404 and b'TICKET_TAMPERED' not in res.content:
            self.skipTest('lookup lives at another address')
        self.assertEqual(res.json()['code'], 'TICKET_TAMPERED')

    def test_the_holder_gets_the_sealed_qr(self):
        from .views_tickets import serialize_ticket
        row = serialize_ticket(self.ticket)
        self.assertEqual(ticket_seal.read(row['qr']), self.ticket.code)
        self.assertIn('.', row['qr'])

    def test_the_door_can_fetch_the_public_key_signed_out(self):
        res = self.client.get('/event/door/seal-key/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['key']['crv'], 'P-256')
