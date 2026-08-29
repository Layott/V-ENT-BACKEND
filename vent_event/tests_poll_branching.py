"""One question shown because of how an earlier one was answered.

CEO, 29 August 2026: "for the polls, should also be able to link questions
together, based off like their answers in one question and then it shows then
another question."

The tests worth having here are not the happy path. They are:

- the second question is not merely hidden in the page, it cannot be answered,
  because the address of a hidden control arrives in the same response that hid
  it;
- its results are not readable either, for the same reason;
- a chain that loops back is refused at creation, because it would produce a
  question nobody can ever reach and nothing would say why.
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Users
from vent_event.models import Event, EventPoll, Ticket, TicketTier


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, full_name=name.title(),
        login_session_token=('tk-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class PollBranchingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.game = Games.objects.get_or_create(game_title='Branching')[0]
        self.organiser = a_user('br_org')
        self.event = Event.objects.create(
            name='Branching Event', game=self.game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            event_date=(timezone.now() + timezone.timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00', location='Lagos')
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)
        self.ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-BRONE', status='valid')

    def _as_organiser(self):
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.organiser.login_session_token)

    def _create(self, **payload):
        self._as_organiser()
        return self.client.post('/event/%s/polls/' % self.event.slug,
                                payload, format='json')

    def _answer(self, poll_id, **payload):
        self.client.credentials()
        body = dict(payload)
        body['ticket_code'] = self.ticket.code
        return self.client.post(
            '/event/%s/polls/%d/vote/' % (self.event.slug, poll_id),
            body, format='json')

    def _read(self):
        self.client.credentials()
        return self.client.get(
            '/event/%s/polls/?ticket_code=%s'
            % (self.event.slug, self.ticket.code)).data['data']['polls']

    def _pair(self):
        """A first question, and a second revealed by picking its first option."""
        first = self._create(question='Which day?', kind='single',
                             options=['Friday', 'Saturday']).data['data']['poll']
        second = self._create(
            question='Which session on Friday?', kind='single',
            options=['Morning', 'Evening'],
            depends_on=first['id'],
            depends_on_option=first['options'][0]['id']).data['data']['poll']
        return first, second

    # ------------------------------------------------------------------ shown

    def test_the_second_question_is_hidden_until_the_first_is_answered(self):
        first, second = self._pair()
        rows = {p['id']: p for p in self._read()}
        self.assertTrue(rows[first['id']]['visible'])
        self.assertFalse(rows[second['id']]['visible'])

    def test_answering_the_right_way_reveals_it(self):
        first, second = self._pair()
        self._answer(first['id'], option_id=first['options'][0]['id'])
        rows = {p['id']: p for p in self._read()}
        self.assertTrue(rows[second['id']]['visible'])

    def test_answering_the_other_way_does_not(self):
        first, second = self._pair()
        self._answer(first['id'], option_id=first['options'][1]['id'])
        rows = {p['id']: p for p in self._read()}
        self.assertFalse(rows[second['id']]['visible'])

    def test_changing_the_answer_hides_it_again(self):
        first, second = self._pair()
        self._answer(first['id'], option_id=first['options'][0]['id'])
        self._answer(first['id'], option_id=first['options'][1]['id'])
        rows = {p['id']: p for p in self._read()}
        self.assertFalse(rows[second['id']]['visible'])

    def test_the_organiser_sees_every_question_whatever_the_conditions(self):
        first, second = self._pair()
        self._as_organiser()
        rows = self.client.get(
            '/event/%s/polls/' % self.event.slug).data['data']['polls']
        self.assertTrue(all(p['visible'] for p in rows))
        self.assertEqual(len(rows), 2)

    # ----------------------------------------------------------- not just hidden

    def test_a_hidden_question_cannot_be_answered(self):
        first, second = self._pair()
        res = self._answer(second['id'], option_id=second['options'][0]['id'])
        self.assertEqual(res.status_code, 403, res.data)
        self.assertEqual(res.data['code'], 'POLL_NOT_VISIBLE')

    def test_it_can_be_answered_once_it_is_revealed(self):
        first, second = self._pair()
        self._answer(first['id'], option_id=first['options'][0]['id'])
        res = self._answer(second['id'], option_id=second['options'][0]['id'])
        self.assertEqual(res.status_code, 200, res.data)

    def test_a_hidden_question_does_not_leak_its_results(self):
        first, second = self._pair()
        # Somebody else answers it, so there is a count to leak.
        other = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-BRTWO', status='valid')
        self.client.credentials()
        self.client.post(
            '/event/%s/polls/%d/vote/' % (self.event.slug, first['id']),
            {'option_id': first['options'][0]['id'], 'ticket_code': other.code},
            format='json')
        self.client.post(
            '/event/%s/polls/%d/vote/' % (self.event.slug, second['id']),
            {'option_id': second['options'][0]['id'], 'ticket_code': other.code},
            format='json')

        rows = {p['id']: p for p in self._read()}
        hidden = rows[second['id']]
        self.assertFalse(hidden['visible'])
        self.assertIsNone(hidden['total_votes'])
        self.assertFalse(hidden['results_visible'])

    # --------------------------------------------------------------- on a scale

    def test_a_question_can_depend_on_a_range_of_a_scale(self):
        rating = self._create(question='How was it?', kind='scale',
                              scale_min=1, scale_max=5).data['data']['poll']
        follow = self._create(question='What went wrong?', kind='short_text',
                              depends_on=rating['id'],
                              depends_on_max=2).data['data']['poll']

        self._answer(rating['id'], number=5)
        rows = {p['id']: p for p in self._read()}
        self.assertFalse(rows[follow['id']]['visible'])

        self._answer(rating['id'], number=1)
        rows = {p['id']: p for p in self._read()}
        self.assertTrue(rows[follow['id']]['visible'])

    def test_depending_on_a_range_of_something_that_is_not_a_scale_is_refused(self):
        first = self._create(question='Which day?',
                             options=['Fri', 'Sat']).data['data']['poll']
        res = self._create(question='Why?', kind='short_text',
                           depends_on=first['id'], depends_on_min=3)
        self.assertEqual(res.status_code, 400, res.data)

    def test_depending_on_an_option_of_a_scale_is_refused(self):
        rating = self._create(question='How was it?',
                              kind='scale').data['data']['poll']
        res = self._create(question='Why?', kind='short_text',
                           depends_on=rating['id'], depends_on_option=999)
        self.assertEqual(res.status_code, 400, res.data)

    # ------------------------------------------------------------------ refusals

    def test_depending_on_a_question_from_another_event_is_refused(self):
        other_event = Event.objects.create(
            name='Elsewhere', game=self.game, creator=self.organiser,
            event_type='physical', desc='x', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            event_date=(timezone.now() + timezone.timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00', location='Abuja')
        stranger = EventPoll.objects.create(
            event=other_event, question='Not yours', created_by=self.organiser)
        res = self._create(question='Follow up', kind='short_text',
                           depends_on=stranger.id)
        self.assertEqual(res.status_code, 400, res.data)

    def test_a_loop_is_refused(self):
        first, second = self._pair()
        # Point the first at the second, which would close the circle.
        EventPoll.objects.filter(pk=first['id']).update(depends_on_id=second['id'])
        res = self._create(question='Third', kind='short_text',
                           depends_on=second['id'])
        self.assertEqual(res.status_code, 400, res.data)

    def test_depending_on_an_answer_that_is_not_on_that_question_is_refused(self):
        first, _ = self._pair()
        elsewhere = self._create(question='Other', kind='single',
                                 options=['x', 'y']).data['data']['poll']
        res = self._create(question='Follow up', kind='short_text',
                           depends_on=first['id'],
                           depends_on_option=elsewhere['options'][0]['id'])
        self.assertEqual(res.status_code, 400, res.data)

    def test_both_an_answer_and_a_range_is_refused(self):
        first, _ = self._pair()
        res = self._create(question='Follow up', kind='short_text',
                           depends_on=first['id'],
                           depends_on_option=first['options'][0]['id'],
                           depends_on_min=1)
        self.assertEqual(res.status_code, 400, res.data)

    def test_with_no_condition_a_question_is_always_shown(self):
        plain = self._create(question='Anything else?',
                             kind='short_text').data['data']['poll']
        rows = {p['id']: p for p in self._read()}
        self.assertTrue(rows[plain['id']]['visible'])

    def test_depending_only_on_having_answered_at_all(self):
        first = self._create(question='How was it?', kind='scale').data['data']['poll']
        follow = self._create(question='Anything to add?', kind='long_text',
                              depends_on=first['id']).data['data']['poll']
        rows = {p['id']: p for p in self._read()}
        self.assertFalse(rows[follow['id']]['visible'])
        self._answer(first['id'], number=3)
        rows = {p['id']: p for p in self._read()}
        self.assertTrue(rows[follow['id']]['visible'])
