"""Asking the room.

The decisions worth pinning: a vote belongs to a ticket rather than an account,
results stay hidden until somebody has answered, and a poll people have already
answered is closed rather than deleted.
"""
from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from .models import (Event, EventManager, EventPoll, EventPollOption,
                     EventPollVote, Ticket, TicketTier)


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=('p-%s' % name)[:16])
    user.login_session_created_at = timezone.now()
    user.save()
    return user, {'HTTP_AUTHORIZATION': 'Bearer %s' % user.login_session_token}


class PollBase(TestCase):
    def setUp(self):
        self.organiser, self.auth = a_user('po_org')
        self.stranger, self.stranger_auth = a_user('po_other')
        game = Games.objects.create(game_title='EA FC PO')
        now = timezone.localtime(timezone.now())
        self.event = Event.objects.create(
            name='Poll Probe', game=game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timedelta(days=3),
            event_date=(now + timedelta(days=3)).date(),
            start_time=time(18, 0), end_time=time(22, 0), location='Lagos')
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)
        self.guest = Ticket.objects.create(
            event=self.event, tier=self.tier, code='PO000001',
            attendee_email='guest@example.com')
        self.member, self.member_auth = a_user('po_member')
        self.member_ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, code='PO000002',
            user=self.member, attendee_email=self.member.email)

        self.poll = EventPoll.objects.create(
            event=self.event, question='Which day for the finals?',
            created_by=self.organiser)
        self.saturday = EventPollOption.objects.create(
            poll=self.poll, text='Saturday', position=0)
        self.sunday = EventPollOption.objects.create(
            poll=self.poll, text='Sunday', position=1)

    def create(self, payload, auth=None):
        return self.client.post(
            '/event/%s/polls/' % self.event.event_id, payload,
            content_type='application/json', **(auth or self.auth))

    def answer(self, option, code=None, auth=None):
        body = {'option_id': option.id}
        if code:
            body['ticket_code'] = code
        return self.client.post(
            '/event/%s/polls/%s/vote/' % (self.event.event_id, self.poll.id),
            body, content_type='application/json', **(auth or {}))

    def read(self, code=None, auth=None):
        params = {'ticket_code': code} if code else {}
        return self.client.get('/event/%s/polls/' % self.event.event_id,
                               params, **(auth or {}))


class PollVotingTests(PollBase):
    def test_a_guest_answers_with_their_ticket_code(self):
        # A poll only members could answer would be a poll of the wrong room.
        res = self.answer(self.saturday, code='PO000001')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(EventPollVote.objects.count(), 1)

    def test_a_signed_in_holder_needs_no_code(self):
        res = self.answer(self.sunday, auth=self.member_auth)
        self.assertEqual(res.status_code, 200, res.data)
        vote = EventPollVote.objects.get(ticket=self.member_ticket)
        self.assertEqual(vote.option_id, self.sunday.id)

    def test_somebody_with_no_ticket_is_told_before_they_pick(self):
        res = self.answer(self.saturday, auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'TICKET_REQUIRED')

    def test_one_ticket_is_one_vote(self):
        self.answer(self.saturday, code='PO000001')
        self.answer(self.saturday, code='PO000001')
        self.assertEqual(EventPollVote.objects.filter(poll=self.poll).count(), 1)

    def test_changing_your_mind_moves_the_vote_rather_than_adding_one(self):
        self.answer(self.saturday, code='PO000001')
        self.answer(self.sunday, code='PO000001')
        votes = EventPollVote.objects.filter(poll=self.poll)
        self.assertEqual(votes.count(), 1)
        self.assertEqual(votes.first().option_id, self.sunday.id)

    def test_a_refunded_ticket_answers_nothing(self):
        self.guest.status = 'refunded'
        self.guest.save()
        res = self.answer(self.saturday, code='PO000001')
        self.assertEqual(res.status_code, 403)

    def test_an_option_from_another_poll_is_refused(self):
        other = EventPoll.objects.create(event=self.event, question='Other?')
        stray = EventPollOption.objects.create(poll=other, text='Stray')
        res = self.answer(stray, code='PO000001')
        self.assertEqual(res.status_code, 400)

    def test_a_closed_poll_takes_no_answers(self):
        self.poll.is_open = False
        self.poll.save()
        res = self.answer(self.saturday, code='PO000001')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'POLL_CLOSED')

    def test_a_poll_past_its_deadline_takes_no_answers(self):
        self.poll.closes_at = timezone.now() - timedelta(minutes=1)
        self.poll.save()
        res = self.answer(self.saturday, code='PO000001')
        self.assertEqual(res.status_code, 409)


class PollResultsTests(PollBase):
    def test_the_count_is_hidden_until_you_answer(self):
        # A visible tally moves later answers toward whatever is winning.
        rows = self.read().data['data']['polls']
        self.assertFalse(rows[0]['results_visible'])
        self.assertNotIn('votes', rows[0]['options'][0])
        self.assertIsNone(rows[0]['total_votes'])

    def test_answering_reveals_it(self):
        self.answer(self.saturday, code='PO000001')
        rows = self.read(code='PO000001').data['data']['polls']
        self.assertTrue(rows[0]['results_visible'])
        counts = {o['text']: o['votes'] for o in rows[0]['options']}
        self.assertEqual(counts['Saturday'], 1)
        self.assertEqual(counts['Sunday'], 0)

    def test_the_organiser_always_sees_it(self):
        rows = self.read(auth=self.auth).data['data']['polls']
        self.assertTrue(rows[0]['results_visible'])

    def test_a_closed_poll_shows_everybody(self):
        self.poll.is_open = False
        self.poll.save()
        rows = self.read().data['data']['polls']
        self.assertTrue(rows[0]['results_visible'])

    def test_the_organiser_can_choose_to_show_it_from_the_start(self):
        self.poll.show_results_before_voting = True
        self.poll.save()
        rows = self.read().data['data']['polls']
        self.assertTrue(rows[0]['results_visible'])

    def test_a_share_of_nothing_is_unanswerable_not_zero(self):
        # A bar drawn from a made-up zero reads as a real result.
        rows = self.read(auth=self.auth).data['data']['polls']
        self.assertIsNone(rows[0]['options'][0]['share'])

    def test_shares_add_up_once_people_answer(self):
        self.answer(self.saturday, code='PO000001')
        self.answer(self.sunday, auth=self.member_auth)
        rows = self.read(auth=self.auth).data['data']['polls']
        shares = sorted(o['share'] for o in rows[0]['options'])
        self.assertEqual(shares, [50.0, 50.0])

    def test_your_own_answer_comes_back_with_the_poll(self):
        self.answer(self.sunday, code='PO000001')
        rows = self.read(code='PO000001').data['data']['polls']
        self.assertEqual(rows[0]['my_option_id'], self.sunday.id)


class PollManagementTests(PollBase):
    def test_the_organiser_creates_one(self):
        res = self.create({'question': 'What should we play next?',
                           'options': ['Tekken', 'EA FC', 'Mortal Kombat']})
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(len(res.data['data']['poll']['options']), 3)

    def test_the_order_the_organiser_typed_is_the_order_shown(self):
        res = self.create({'question': 'Best time?',
                           'options': ['Morning', 'Afternoon', 'Evening']})
        texts = [o['text'] for o in res.data['data']['poll']['options']]
        self.assertEqual(texts, ['Morning', 'Afternoon', 'Evening'])

    def test_one_option_is_not_a_poll(self):
        res = self.create({'question': 'Agree?', 'options': ['Yes']})
        self.assertEqual(res.status_code, 400)

    def test_duplicate_options_are_collapsed_and_then_refused(self):
        res = self.create({'question': 'Pick', 'options': ['Yes', 'yes']})
        self.assertEqual(res.status_code, 400)

    def test_too_many_options_are_refused(self):
        res = self.create({'question': 'Pick',
                           'options': ['o%d' % i for i in range(11)]})
        self.assertEqual(res.status_code, 400)

    def test_a_deadline_in_the_past_is_refused(self):
        res = self.create({
            'question': 'Pick', 'options': ['a', 'b'],
            'closes_at': (timezone.now() - timedelta(hours=1)).isoformat()})
        self.assertEqual(res.status_code, 400)

    def test_a_stranger_creates_nothing(self):
        res = self.create({'question': 'Pick', 'options': ['a', 'b']},
                          auth=self.stranger_auth)
        self.assertEqual(res.status_code, 403)

    def test_a_manager_may_create_one(self):
        manager, manager_auth = a_user('po_mgr')
        EventManager.objects.create(event=self.event, user=manager,
                                    role='manager')
        res = self.create({'question': 'Pick', 'options': ['a', 'b']},
                          auth=manager_auth)
        self.assertEqual(res.status_code, 201)

    def test_the_organiser_closes_it(self):
        res = self.client.patch(
            '/event/%s/polls/%s/' % (self.event.event_id, self.poll.id),
            {'is_open': False}, content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.data)
        self.poll.refresh_from_db()
        self.assertTrue(self.poll.closed())

    def test_reopening_clears_a_deadline_that_has_passed(self):
        # Otherwise it closes again on the next read, which reads as the button
        # not working.
        self.poll.closes_at = timezone.now() - timedelta(minutes=5)
        self.poll.is_open = False
        self.poll.save()
        self.client.patch(
            '/event/%s/polls/%s/' % (self.event.event_id, self.poll.id),
            {'is_open': True}, content_type='application/json', **self.auth)
        self.poll.refresh_from_db()
        self.assertFalse(self.poll.closed())

    def test_a_poll_nobody_answered_can_be_deleted(self):
        res = self.client.delete(
            '/event/%s/polls/%s/' % (self.event.event_id, self.poll.id),
            **self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(EventPoll.objects.count(), 0)

    def test_deleting_one_with_answers_is_refused(self):
        # The answers are the point. Deleting the question throws them away.
        self.answer(self.saturday, code='PO000001')
        res = self.client.delete(
            '/event/%s/polls/%s/' % (self.event.event_id, self.poll.id),
            **self.auth)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'POLL_HAS_VOTES')

    def test_an_unknown_event_is_a_404(self):
        res = self.client.get('/event/999999/polls/')
        self.assertEqual(res.status_code, 404)


class PollAnswerableTests(PollBase):
    """Whether the reader may answer, said before they press anything.

    Signing in is not the same as holding a ticket. Somebody with an account and
    no ticket, and somebody who bought as a guest under another address, both
    reach this page. A live button that answers 403 tells them only after they
    have chosen, which is the fault the community compose box shipped with.
    """

    def test_a_signed_in_holder_may_answer(self):
        res = self.read(auth=self.member_auth)
        self.assertTrue(res.data['data']['can_answer'])

    def test_a_signed_in_stranger_may_not(self):
        res = self.read(auth=self.stranger_auth)
        self.assertFalse(res.data['data']['can_answer'])

    def test_the_organiser_without_a_ticket_may_not_answer(self):
        # They see the counts. That is a different permission from voting.
        res = self.read(auth=self.auth)
        self.assertFalse(res.data['data']['can_answer'])
        self.assertTrue(res.data['data']['polls'][0]['results_visible'])

    def test_a_guest_with_a_code_may_answer(self):
        res = self.read(code='PO000001')
        self.assertTrue(res.data['data']['can_answer'])

    def test_a_guest_with_no_code_may_not(self):
        res = self.read()
        self.assertFalse(res.data['data']['can_answer'])

    def test_a_wrong_code_may_not(self):
        res = self.read(code='NOTATICKET')
        self.assertFalse(res.data['data']['can_answer'])

    def test_a_refunded_holder_may_not(self):
        self.guest.status = 'refunded'
        self.guest.save()
        res = self.read(code='PO000001')
        self.assertFalse(res.data['data']['can_answer'])
