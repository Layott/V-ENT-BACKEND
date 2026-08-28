"""Asking a partner to confirm one of their own usernames.

CEO: "if maybe there is a way to se things like this automated, so people
hosting events on the website can sutomatically verify users"

Almost every test here is about a failure, because the whole design rests on one
rule: nothing we cannot get an answer to becomes a refusal. A timeout, a 500, a
login page served with a 200 - each of those leaves the submission waiting for a
person, and none of them tells an entrant they are not who they say they are.
"""
from datetime import timedelta
from unittest.mock import patch

import requests
from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_partners.models import Partner

from . import partner_check
from .models import EntryRequirement, EntrySubmission, Tournament


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text_body=None):
        self.status_code = status_code
        self._payload = payload
        self._text = text_body

    def json(self):
        if self._text is not None:
            raise ValueError('not json')
        return self._payload


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('p-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class AskTests(TestCase):
    def setUp(self):
        owner, _ = a_user('pc_owner')
        self.partner = Partner.objects.create(
            name='AFC', slug='afc', owner=owner, status='approved',
            contact_name='AFC', contact_email='p@afc.test',
            verification_url='https://afc.test/verify/',
            verification_secret='shared-secret',
        )

    def ask(self):
        return partner_check.ask(self.partner, 'Free Fire UID', '12345')

    def test_a_yes_is_a_yes(self):
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, {'verified': True})):
            out = self.ask()
        self.assertTrue(out.checked)
        self.assertTrue(out.verified)

    def test_a_no_is_a_no_and_carries_the_partners_words(self):
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, {'verified': False,
                                                   'message': 'No such UID.'})):
            out = self.ask()
        self.assertTrue(out.checked)
        self.assertFalse(out.verified)
        self.assertEqual(out.detail, 'No such UID.')

    def test_a_timeout_is_not_a_no(self):
        """The distinction the whole design rests on."""
        with patch('vent_tournament.partner_check.requests.post',
                   side_effect=requests.Timeout('too slow')):
            out = self.ask()
        self.assertFalse(out.checked)
        self.assertFalse(out.verified)
        self.assertEqual(out.reason, 'unreachable')

    def test_a_500_is_not_a_no(self):
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(503)):
            out = self.ask()
        self.assertFalse(out.checked)

    def test_a_login_page_served_with_200_is_not_a_yes(self):
        """The case that must never read as approval."""
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, text_body='<html>Sign in</html>')):
            out = self.ask()
        self.assertFalse(out.checked)
        self.assertEqual(out.reason, 'not_json')

    def test_a_json_body_without_a_verdict_is_not_a_yes(self):
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, {'ok': True})):
            out = self.ask()
        self.assertFalse(out.checked)
        self.assertEqual(out.reason, 'unrecognised')

    def test_our_own_credential_being_refused_is_reported_as_such(self):
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(401)):
            out = self.ask()
        self.assertFalse(out.checked)
        self.assertEqual(out.reason, 'refused_us')

    def test_no_partner_at_all_is_handled_without_a_request(self):
        with patch('vent_tournament.partner_check.requests.post') as post:
            out = partner_check.ask(None, 'UID', '1')
        self.assertFalse(out.checked)
        self.assertEqual(out.reason, 'no_partner')
        post.assert_not_called()

    def test_we_send_the_field_and_the_value_and_nothing_else(self):
        """A partner confirming a username does not need to know who is asking
        about whom."""
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, {'verified': True})) as post:
            self.ask()
        body = post.call_args.kwargs['json']
        self.assertEqual(set(body), {'field', 'value', 'asked_at'})
        self.assertEqual(body['field'], 'Free Fire UID')

    def test_a_partner_with_no_url_is_never_called(self):
        self.partner.verification_url = ''
        self.partner.save(update_fields=['verification_url'])
        self.assertIsNone(partner_check.partner_for('afc'))

    def test_a_partner_that_is_not_approved_is_never_called(self):
        self.partner.status = 'suspended'
        self.partner.save(update_fields=['status'])
        self.assertIsNone(partner_check.partner_for('afc'))


class SubmissionTests(TestCase):
    """What the entrant's submission becomes."""

    def setUp(self):
        self.owner, self.owner_auth = a_user('pv_owner')
        self.player, self.player_auth = a_user('pv_player')
        self.partner = Partner.objects.create(
            name='AFC', slug='afc', owner=self.owner, status='approved',
            contact_name='AFC', contact_email='p@afc.test',
            verification_url='https://afc.test/verify/',
            verification_secret='shared-secret',
        )
        game = Games.objects.get_or_create(game_title='PV Probe')[0]
        now = timezone.now()
        self.t = Tournament.objects.create(
            tournament_title='PV Probe Cup', tournament_creator=self.owner,
            tournament_game=game, tournament_type='online',
            tournament_access='individual', tournament_visibility='public',
            entry_fee='Free', entry_fee_price=0, prize_type='no_prize',
            bracket_type='single_elimination',
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=3), is_draft=False,
        )
        self.req = EntryRequirement.objects.create(
            tournament=self.t, kind='partner_verified', order=0,
            config={'partner': 'afc', 'field_label': 'Free Fire UID'},
        )

    def submit(self, value='12345'):
        return self.client.post(
            '/tournament/%s/requirements/%s/submit/' % (
                self.t.tournament_id, self.req.id),
            data={'value': value}, content_type='application/json',
            **self.player_auth)

    def test_a_confirmed_username_needs_nobody(self):
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, {'verified': True})):
            res = self.submit()
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(EntrySubmission.objects.get().status, 'approved')

    def test_a_rejected_username_says_what_the_partner_said(self):
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, {'verified': False,
                                                   'message': 'No such UID.'})):
            self.submit()
        submission = EntrySubmission.objects.get()
        self.assertEqual(submission.status, 'refused')
        self.assertEqual(submission.note, 'No such UID.')

    def test_a_partner_that_is_down_leaves_it_for_a_person(self):
        """The fallback, and the reason this is safe to turn on at all."""
        with patch('vent_tournament.partner_check.requests.post',
                   side_effect=requests.ConnectionError('refused')):
            res = self.submit()
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(EntrySubmission.objects.get().status, 'pending')

    def test_no_partner_configured_leaves_it_for_a_person(self):
        self.partner.verification_url = ''
        self.partner.save(update_fields=['verification_url'])
        with patch('vent_tournament.partner_check.requests.post') as post:
            self.submit()
        self.assertEqual(EntrySubmission.objects.get().status, 'pending')
        post.assert_not_called()

    def test_no_person_is_recorded_as_having_reviewed_it(self):
        """A partner is not a person, and putting a name against a decision
        nobody made is worse than leaving it blank."""
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, {'verified': True})):
            self.submit()
        self.assertIsNone(EntrySubmission.objects.get().reviewed_by)

    def test_a_non_partner_requirement_never_calls_out(self):
        other = EntryRequirement.objects.create(
            tournament=self.t, kind='custom_field', order=1,
            config={'field_label': 'Riot ID'})
        with patch('vent_tournament.partner_check.requests.post') as post:
            self.client.post(
                '/tournament/%s/requirements/%s/submit/' % (
                    self.t.tournament_id, other.id),
                data={'value': 'x'}, content_type='application/json',
                **self.player_auth)
        post.assert_not_called()

    def test_sending_again_after_a_partner_refusal_asks_again(self):
        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, {'verified': False})):
            self.submit('wrong')
        self.assertEqual(EntrySubmission.objects.get().status, 'refused')

        with patch('vent_tournament.partner_check.requests.post',
                   return_value=FakeResponse(200, {'verified': True})):
            self.submit('right')
        self.assertEqual(EntrySubmission.objects.get().status, 'approved')
