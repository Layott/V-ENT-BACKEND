# -*- coding: utf-8 -*-
"""Somewhere to say what is wrong.

CEO, 7 September 2026: "Let'salso have a place for feedbck."

Open to anybody on purpose: the most useful report comes from somebody who hit a
wall, and the wall is sometimes the sign-in page. Which means it needs a rate
limit, because an open write endpoint without one is a spam target.
"""
from django.test import TestCase
from django.utils import timezone

from .models import Feedback, Users


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('f-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class SendingFeedback(TestCase):
    URL = '/auth/feedback/'

    def send(self, message='The door list would not find my ticket at all.',
             auth=None, **extra):
        body = {'message': message}
        body.update(extra)
        return self.client.post(self.URL, data=body,
                                content_type='application/json',
                                **(auth or {}))

    def test_somebody_with_no_account_can_send(self):
        """The wall they hit is sometimes the sign-in page itself."""
        res = self.send()
        self.assertEqual(res.status_code, 200, res.content)
        row = Feedback.objects.get()
        self.assertIsNone(row.user)

    def test_a_signed_in_person_is_recorded_as_themselves(self):
        user, auth = a_user('fb1')
        res = self.send(auth=auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Feedback.objects.get().user, user)

    def test_the_area_and_kind_are_kept(self):
        self.send(area='door', kind='broken')
        row = Feedback.objects.get()
        self.assertEqual(row.area, 'door')
        self.assertEqual(row.kind, 'broken')

    def test_an_area_that_does_not_exist_falls_back_rather_than_failing(self):
        """Losing the report over a bad dropdown value would be the worse bug."""
        res = self.send(area='nonsense', kind='nonsense')
        self.assertEqual(res.status_code, 200)
        row = Feedback.objects.get()
        self.assertEqual(row.area, 'other')
        self.assertEqual(row.kind, 'broken')

    def test_the_page_they_were_on_is_kept(self):
        """Worth more than most of the message.

        "The button does nothing" cannot be acted on until somebody knows which
        page the button was on.
        """
        self.send(page='https://v-ent.co/events/rivalry/attendees')
        self.assertIn('/events/rivalry/attendees', Feedback.objects.get().page)

    def test_something_too_short_is_refused_by_code(self):
        res = self.send(message='broken')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'MESSAGE_TOO_SHORT')
        self.assertEqual(Feedback.objects.count(), 0)

    def test_something_enormous_is_refused_rather_than_stored(self):
        res = self.send(message='x' * 5000)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'MESSAGE_TOO_LONG')

    def test_a_second_report_in_the_same_minute_is_refused(self):
        self.assertEqual(self.send().status_code, 200)
        res = self.send(message='And another thing about the door list.')
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()['code'], 'TOO_MANY')

    def test_two_different_people_are_not_held_to_one_limit(self):
        """A shared connection at a venue must not silence the second person."""
        _one, auth_one = a_user('fb2')
        _two, auth_two = a_user('fb3')
        self.assertEqual(self.send(auth=auth_one).status_code, 200)
        self.assertEqual(self.send(auth=auth_two).status_code, 200)

    def test_the_form_reads_its_choices_from_the_server(self):
        """One list, not a second copy in the browser that can drift."""
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, 200)
        data = res.json()['data']
        values = [a['value'] for a in data['areas']]
        self.assertIn('door', values)
        self.assertIn('pricing', values)
        self.assertTrue(data['max_message'] > 0)

    def test_an_address_is_optional(self):
        res = self.send(email='')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Feedback.objects.get().email, '')
