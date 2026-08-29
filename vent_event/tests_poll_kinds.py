"""The five kinds of question a poll could not ask until today.

CEO, 29 August 2026: the poll mechanism should be "a lot more detailed with a
lot more options for polling, just like google forms". It could ask exactly one
thing - pick one of these - which answers "which day suits you" and nothing
else.

`tests_polls.py` still covers `single` and still passes untouched, which is the
point: this is five branches beside the original, not a rewrite of it.
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Users
from vent_event.models import (Event, EventPoll, EventPollChoice,
                               EventPollVote, Ticket, TicketTier)


def a_user(name, **extra):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, full_name=name.title(),
        login_session_token=('tk-%s' % name)[:16], **extra)
    user.login_session_created_at = timezone.now()
    user.save()
    return user


class PollKindTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.game = Games.objects.get_or_create(game_title='Poll Kinds')[0]
        self.organiser = a_user('pk_org')
        self.event = Event.objects.create(
            name='Poll Kinds Event', game=self.game, creator=self.organiser,
            event_type='physical', desc='probe', entry_fee=0,
            reg_start_date=timezone.now(),
            reg_end_date=timezone.now() + timezone.timedelta(days=5),
            event_date=(timezone.now() + timezone.timedelta(days=6)).date(),
            start_time='10:00', end_time='18:00', location='Lagos')
        self.tier = TicketTier.objects.create(
            event=self.event, name='General', price=0, quantity=100)
        self.ticket = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-PKONE', status='valid')

    # ------------------------------------------------------------- helpers

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

    def _read(self, as_organiser=False):
        if as_organiser:
            self._as_organiser()
        else:
            self.client.credentials()
        return self.client.get(
            '/event/%s/polls/?ticket_code=%s' % (self.event.slug, self.ticket.code))

    # ---------------------------------------------------------------- kinds

    def test_an_unknown_kind_is_refused(self):
        res = self._create(question='?', kind='telepathy', options=['a', 'b'])
        self.assertEqual(res.status_code, 400, res.data)

    def test_the_default_is_still_pick_one(self):
        res = self._create(question='Which day?', options=['Fri', 'Sat'])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['data']['poll']['kind'], EventPoll.SINGLE)

    # -------------------------------------------------------------- multiple

    def test_pick_several_records_every_choice(self):
        poll = self._create(question='Which days can you make?',
                            kind='multiple',
                            options=['Fri', 'Sat', 'Sun']).data['data']['poll']
        ids = [o['id'] for o in poll['options']][:2]
        res = self._answer(poll['id'], option_ids=ids)
        self.assertEqual(res.status_code, 200, res.data)
        vote = EventPollVote.objects.get(poll_id=poll['id'])
        self.assertEqual(vote.choices.count(), 2)

    def test_pick_several_respects_the_smallest_and_largest(self):
        poll = self._create(question='Pick two',
                            kind='multiple', min_choices=2, max_choices=2,
                            options=['a', 'b', 'c']).data['data']['poll']
        ids = [o['id'] for o in poll['options']]
        self.assertEqual(self._answer(poll['id'], option_ids=ids[:1]).status_code, 400)
        self.assertEqual(self._answer(poll['id'], option_ids=ids).status_code, 400)
        self.assertEqual(self._answer(poll['id'], option_ids=ids[:2]).status_code, 200)

    def test_asking_for_more_choices_than_there_are_options_is_refused(self):
        res = self._create(question='Pick five', kind='multiple',
                           max_choices=5, options=['a', 'b'])
        self.assertEqual(res.status_code, 400, res.data)

    def test_the_same_option_twice_is_refused(self):
        poll = self._create(question='Which?', kind='multiple',
                            options=['a', 'b']).data['data']['poll']
        first = poll['options'][0]['id']
        res = self._answer(poll['id'], option_ids=[first, first])
        self.assertEqual(res.status_code, 400, res.data)

    def test_answering_again_replaces_rather_than_adds(self):
        poll = self._create(question='Which?', kind='multiple',
                            options=['a', 'b', 'c']).data['data']['poll']
        ids = [o['id'] for o in poll['options']]
        self._answer(poll['id'], option_ids=ids[:3])
        self._answer(poll['id'], option_ids=ids[:1])
        self.assertEqual(EventPollVote.objects.filter(poll_id=poll['id']).count(), 1)
        self.assertEqual(EventPollChoice.objects.count(), 1)

    # --------------------------------------------------------------- ranking

    def test_a_ranking_keeps_the_order(self):
        poll = self._create(question='Put these in order', kind='ranking',
                            options=['a', 'b', 'c']).data['data']['poll']
        ids = [o['id'] for o in poll['options']]
        wanted = [ids[2], ids[0], ids[1]]
        res = self._answer(poll['id'], option_ids=wanted)
        self.assertEqual(res.status_code, 200, res.data)
        vote = EventPollVote.objects.get(poll_id=poll['id'])
        self.assertEqual([c.option_id for c in vote.choices.all()], wanted)

    def test_a_partial_ranking_is_refused(self):
        poll = self._create(question='Order these', kind='ranking',
                            options=['a', 'b', 'c']).data['data']['poll']
        ids = [o['id'] for o in poll['options']]
        self.assertEqual(self._answer(poll['id'], option_ids=ids[:2]).status_code, 400)

    def test_a_ranking_reports_where_things_landed_on_average(self):
        poll = self._create(question='Order these', kind='ranking',
                            options=['a', 'b']).data['data']['poll']
        ids = [o['id'] for o in poll['options']]
        self._answer(poll['id'], option_ids=[ids[1], ids[0]])
        row = self._read(as_organiser=True).data['data']['polls'][0]
        first = next(o for o in row['options'] if o['id'] == ids[1])
        self.assertEqual(first['average_place'], 1.0)

    # ----------------------------------------------------------------- scale

    def test_a_scale_takes_a_number_in_range(self):
        poll = self._create(question='How was it?', kind='scale',
                            scale_min=1, scale_max=5,
                            scale_min_label='Poor',
                            scale_max_label='Excellent').data['data']['poll']
        self.assertEqual(poll['scale_max_label'], 'Excellent')
        self.assertEqual(self._answer(poll['id'], number=4).status_code, 200)
        self.assertEqual(EventPollVote.objects.get(poll_id=poll['id']).number, 4)

    def test_a_number_off_the_scale_is_refused(self):
        poll = self._create(question='How was it?', kind='scale',
                            scale_min=1, scale_max=5).data['data']['poll']
        self.assertEqual(self._answer(poll['id'], number=9).status_code, 400)
        self.assertEqual(self._answer(poll['id'], number='four').status_code, 400)

    def test_a_scale_reports_an_average_and_a_distribution(self):
        poll = self._create(question='How was it?', kind='scale',
                            scale_min=1, scale_max=5).data['data']['poll']
        self._answer(poll['id'], number=4)
        row = self._read(as_organiser=True).data['data']['polls'][0]
        self.assertEqual(row['average'], 4.0)
        self.assertEqual(len(row['distribution']), 5)

    def test_a_one_point_scale_is_refused(self):
        res = self._create(question='?', kind='scale', scale_min=3, scale_max=3)
        self.assertEqual(res.status_code, 400, res.data)

    # ------------------------------------------------------------------ text

    def test_a_short_answer_is_stored(self):
        poll = self._create(question='What should we play next?',
                            kind='short_text').data['data']['poll']
        res = self._answer(poll['id'], text='Free Fire')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(EventPollVote.objects.get(poll_id=poll['id']).text,
                         'Free Fire')

    def test_an_empty_answer_is_refused(self):
        poll = self._create(question='Anything to say?',
                            kind='long_text').data['data']['poll']
        self.assertEqual(self._answer(poll['id'], text='   ').status_code, 400)

    def test_a_short_answer_has_a_shorter_limit_than_a_long_one(self):
        short = self._create(question='One line',
                             kind='short_text').data['data']['poll']
        long_one = self._create(question='Tell us more',
                                kind='long_text').data['data']['poll']
        self.assertEqual(self._answer(short['id'], text='x' * 200).status_code, 400)
        self.assertEqual(self._answer(long_one['id'], text='x' * 200).status_code, 200)

    def test_a_text_question_does_not_carry_an_option_list(self):
        poll = self._create(question='Say something', kind='short_text',
                            options=['a', 'b']).data['data']['poll']
        self.assertEqual(poll['options'], [])

    def test_the_room_never_reads_the_sentences_people_wrote(self):
        poll = self._create(question='How did we do?', kind='long_text',
                            show_results_before_voting=True).data['data']['poll']
        self._answer(poll['id'], text='The queue at gate B was awful')

        # Read as a DIFFERENT ticket holder. Reading as the same one echoes
        # their own sentence back, which is how the form shows what they wrote,
        # and is not the thing being guarded against here.
        other = Ticket.objects.create(
            event=self.event, tier=self.tier, code='VT-PKTWO', status='valid')
        self.client.credentials()
        seen = self.client.get(
            '/event/%s/polls/?ticket_code=%s'
            % (self.event.slug, other.code)).data['data']['polls'][0]
        self.assertIsNone(seen['answers'])
        self.assertEqual(seen['answers_visible_to'], 'organiser')
        self.assertNotIn('gate B', str(seen),
                         'one attendee could read what another wrote')

        private = self._read(as_organiser=True).data['data']['polls'][0]
        self.assertIn('The queue at gate B was awful', private['answers'])

    def test_a_reader_still_sees_their_own_answer(self):
        poll = self._create(question='How did we do?',
                            kind='long_text').data['data']['poll']
        self._answer(poll['id'], text='It was good')
        mine = self._read().data['data']['polls'][0]
        self.assertEqual(mine['my_text'], 'It was good')

    # -------------------------------------------------------------- refusals

    def test_a_refused_answer_leaves_the_previous_one_alone(self):
        poll = self._create(question='How was it?', kind='scale',
                            scale_min=1, scale_max=5).data['data']['poll']
        self._answer(poll['id'], number=5)
        self.assertEqual(self._answer(poll['id'], number=99).status_code, 400)
        self.assertEqual(EventPollVote.objects.get(poll_id=poll['id']).number, 5)

    def test_still_only_a_ticket_holder_can_answer(self):
        poll = self._create(question='How was it?', kind='scale').data['data']['poll']
        self.client.credentials()
        res = self.client.post(
            '/event/%s/polls/%d/vote/' % (self.event.slug, poll['id']),
            {'number': 3}, format='json')
        self.assertEqual(res.status_code, 403, res.data)

    def test_every_kind_can_be_created_and_answered(self):
        """The whole set, so a kind cannot be added to the model and forgotten
        in the view."""
        answers = {
            EventPoll.SINGLE: lambda p: {'option_id': p['options'][0]['id']},
            EventPoll.MULTIPLE: lambda p: {'option_ids': [p['options'][0]['id']]},
            EventPoll.RANKING: lambda p: {'option_ids': [o['id'] for o in p['options']]},
            EventPoll.SCALE: lambda p: {'number': 3},
            EventPoll.SHORT_TEXT: lambda p: {'text': 'yes'},
            EventPoll.LONG_TEXT: lambda p: {'text': 'yes, at length'},
        }
        self.assertEqual(set(answers), {k for k, _ in EventPoll.KIND_CHOICES},
                         'a kind exists on the model that this test does not answer')
        for kind, build in answers.items():
            created = self._create(question='Q for %s' % kind, kind=kind,
                                   options=['a', 'b'])
            self.assertEqual(created.status_code, 201, (kind, created.data))
            poll = created.data['data']['poll']
            res = self._answer(poll['id'], **build(poll))
            self.assertEqual(res.status_code, 200, (kind, res.data))
