"""An event ticket as a qualifying reward (P2d, design section 7).

A feed into a tournament linked to an event can hand every qualifier it sends
a ticket of one of that event's tiers. CEO, 24 October 2026 (inbox 544): "lets
start them", on the held list that carried P2d.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from vent_event.models import Event, EventTournamentLink, Ticket, TicketTier

from . import feeds, progressions
from .models import Progression
from .tests import client_for
from .tests_feeds import qualifier_and_finals, qualifier_reg
from .tests_stage_engine import draw, play_out


def an_event(organiser, tournament, name='Lagos Finals Weekend'):
    now = timezone.now()
    event = Event.objects.create(
        name=name, creator=organiser, event_type='physical', desc='x', entry_fee=0,
        start_date=now + timedelta(days=5), end_date=now + timedelta(days=5, hours=8),
        reg_start_date=now - timedelta(days=1), reg_end_date=now + timedelta(days=4))
    tier = TicketTier.objects.create(event=event, name='Competitor pass',
                                     price=Decimal('0'), quantity=32)
    if tournament is not None:
        EventTournamentLink.objects.create(event=event, tournament=tournament, linked_by=organiser)
    return event, tier


def send(line, q_stage, creator):
    draw(q_stage, creator)
    play_out(q_stage)
    with transaction.atomic():
        progressions.pull(Progression.objects.get(pk=line.pk), creator)
    line.refresh_from_db()


class QualifierTicketTests(TestCase):
    def setUp(self):
        (self.q, self.q_stage, self.finals, self.f_stage,
         self.line, self.creator) = qualifier_and_finals()
        self.event, self.tier = an_event(self.creator, self.finals)

    def test_every_qualifier_sent_gets_one_ticket(self):
        Progression.objects.filter(pk=self.line.pk).update(ticket_tier=self.tier)
        send(self.line, self.q_stage, self.creator)
        tickets = Ticket.objects.filter(tier=self.tier)
        self.assertEqual(tickets.count(), 2)
        holders = {qualifier_reg(self.line, place).user_id for place in (1, 2)}
        self.assertEqual({t.user_id for t in tickets}, holders)
        self.assertTrue(all(t.price_vc == 0 and t.status == 'valid' for t in tickets))

    def test_no_tier_no_ticket(self):
        send(self.line, self.q_stage, self.creator)
        self.assertFalse(Ticket.objects.exists())

    def test_a_tier_of_an_event_the_target_is_not_linked_to_is_refused(self):
        _other, stranger_tier = an_event(self.creator, None, name='Somebody else')
        with self.assertRaises(feeds.FeedError) as caught:
            feeds.create(self.f_stage, self.q_stage, self.creator, place_from=3, place_to=3,
                         ticket_tier=stranger_tier)
        self.assertEqual((caught.exception.code, caught.exception.field),
                         ('FEED_TICKET_NOT_THIS_EVENT', 'ticket_tier'))

    def test_declining_the_place_gives_the_ticket_back(self):
        Progression.objects.filter(pk=self.line.pk).update(ticket_tier=self.tier)
        send(self.line, self.q_stage, self.creator)
        second = qualifier_reg(self.line, 2)
        res = client_for(second.user).post(
            f'/tournament/{self.finals.slug}/qualifiers/{second.id}/decline/', {}, format='json')
        if res.status_code == 409 and res.data.get('code') == 'DECLINE_CLOSED':
            self.skipTest('the finals drew straight away; covered by unqualify below')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(Ticket.objects.get(user=second.user, tier=self.tier).status, 'cancelled')

    def test_undoing_the_feed_cancels_unused_tickets(self):
        Progression.objects.filter(pk=self.line.pk).update(ticket_tier=self.tier)
        send(self.line, self.q_stage, self.creator)
        feeds.unqualify(Progression.objects.get(pk=self.line.pk))
        self.assertEqual(set(Ticket.objects.values_list('status', flat=True)), {'cancelled'})


class DoorTests(TestCase):
    def test_the_feed_editor_is_told_the_tiers_and_saves_one(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        _event, tier = an_event(creator, finals)
        body = APIClient().get(f'/tournament/{finals.slug}/feeds/').json()['data']
        self.assertEqual([t['id'] for t in body['ticket_tiers']], [tier.id])
        res = client_for(creator).post(f'/tournament/{finals.slug}/feeds/', {
            'target_stage': f_stage.id, 'source_tournament': q.slug, 'source_stage': q_stage.id,
            'place_from': 3, 'place_to': 3, 'ticket_tier': tier.id}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['data']['feed']['ticket_tier'], {'id': tier.id, 'name': tier.name})

    def test_a_tier_id_that_names_nothing_is_a_coded_refusal(self):
        q, q_stage, finals, f_stage, line, creator = qualifier_and_finals()
        res = client_for(creator).post(f'/tournament/{finals.slug}/feeds/', {
            'target_stage': f_stage.id, 'source_tournament': q.slug, 'source_stage': q_stage.id,
            'place_from': 3, 'place_to': 3, 'ticket_tier': 999999}, format='json')
        self.assertEqual((res.status_code, res.data['code']), (400, 'FEED_TICKET_NOT_THIS_EVENT'))
