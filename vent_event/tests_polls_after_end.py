"""Polls after the event ends: the event's owners and editors decide (inbox 590).

CEO, 12 November 2026: "Whether polls on an event that has ended should still
take answers. - this should be left to the owners or editors of the event to
decide." Pinned here: on by default, as every poll behaved before; off refuses
answers once the event has ended, said in words and by code; the page is told
the poll is closed and why before anybody presses; and only somebody who may
edit the event may change it.
"""
from datetime import time, timedelta

from django.utils import timezone

from .models import Event, EventManager
from .tests_polls import PollBase, a_user


class PollsAfterEndTests(PollBase):
    def end_the_event(self):
        # Ended yesterday evening.
        yesterday = timezone.localtime(timezone.now()) - timedelta(days=1)
        Event.objects.filter(pk=self.event.pk).update(
            event_date=yesterday.date(), start_time=time(9, 0), end_time=time(10, 0),
            end_date=None)
        self.event.refresh_from_db()

    def edit(self, payload, auth):
        return self.client.put('/event/edit-event/%s/' % self.event.event_id, payload,
                               content_type='application/json', **auth)

    def test_on_by_default_so_an_ended_event_still_takes_answers(self):
        self.assertTrue(self.event.polls_after_end)
        self.end_the_event()
        res = self.answer(self.saturday, code='PO000001')
        self.assertEqual(res.status_code, 200, res.content)

    def test_off_refuses_an_answer_once_the_event_has_ended(self):
        Event.objects.filter(pk=self.event.pk).update(polls_after_end=False)
        self.end_the_event()
        res = self.answer(self.saturday, code='PO000001')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'POLL_EVENT_ENDED')

    def test_off_still_takes_answers_before_the_event_ends(self):
        Event.objects.filter(pk=self.event.pk).update(polls_after_end=False)
        res = self.answer(self.saturday, code='PO000001')
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_page_is_told_the_poll_is_closed_and_why(self):
        Event.objects.filter(pk=self.event.pk).update(polls_after_end=False)
        self.end_the_event()
        polls = self.read(code='PO000001').json()['data']['polls']
        mine = next(p for p in polls if p['id'] == self.poll.id)
        self.assertFalse(mine['is_open'])
        self.assertEqual(mine['closed_reason'], 'event_ended')

    def test_a_poll_closed_by_hand_says_so_whatever_the_setting(self):
        self.poll.is_open = False
        self.poll.save()
        polls = self.read(code='PO000001').json()['data']['polls']
        mine = next(p for p in polls if p['id'] == self.poll.id)
        self.assertEqual(mine['closed_reason'], 'closed')
        self.assertEqual(self.answer(self.saturday, code='PO000001').json()['code'], 'POLL_CLOSED')

    def test_the_organiser_turns_it_off_and_on_again(self):
        res = self.edit({'polls_after_end': False}, self.auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.event.refresh_from_db()
        self.assertFalse(self.event.polls_after_end)
        self.edit({'polls_after_end': True}, self.auth)
        self.event.refresh_from_db()
        self.assertTrue(self.event.polls_after_end)

    def test_a_named_manager_may_change_it(self):
        manager, manager_auth = a_user('po_mgr')
        EventManager.objects.create(event=self.event, user=manager)
        res = self.edit({'polls_after_end': False}, manager_auth)
        self.assertEqual(res.status_code, 200, res.content)
        self.event.refresh_from_db()
        self.assertFalse(self.event.polls_after_end)

    def test_a_stranger_may_not(self):
        res = self.edit({'polls_after_end': False}, self.stranger_auth)
        self.assertEqual(res.status_code, 403)
        self.event.refresh_from_db()
        self.assertTrue(self.event.polls_after_end)

    def test_the_event_page_carries_the_setting(self):
        res = self.client.get('/event/view-event/%s/' % self.event.event_id)
        body = res.json()['data']
        event = body.get('event', body)
        self.assertIn('polls_after_end', event)
        self.assertTrue(event['polls_after_end'])
