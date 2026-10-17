# -*- coding: utf-8 -*-
"""The studio's explainer plays the tournament's own format (inbox 476).

CEO, 16 October 2026: "we'll need a video explainer for all these
bracket/format systems, so users can easily understand." The design puts one
on stream as a studio element an organiser plays before the event starts. The
element page draws the explainer for whatever the feed says the tournament is
played as, so the feed has to say it: the catalogue key, however the format was
stored, and the formats of the stages in order, because groups into a playoff
is one explainer and only the stages say so.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from vent_auth.models import Games, Users

from .models import Tournament, TournamentStage


def an_organiser(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        full_name=name.replace('_', ' ').title(),
        login_session_token=('%s_tok' % name)[:16])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


class ExplainerFormatInTheFeedTests(TestCase):

    def setUp(self):
        self.organiser, self.auth = an_organiser('explainer_org')
        self.game = Games.objects.create(game_title='EXPLAINER GAME')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Explainer Test', tournament_game=self.game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=3),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team', entry_fee='Free',
            is_draft=False, bracket_type='Swiss System')
        self.ref = self.tournament.slug or self.tournament.tournament_id

    def session(self):
        res = self.client.post('/tournament/%s/studio/sessions/' % self.ref,
                               data={'name': 'Explainer'}, **self.auth)
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        return res.json()['data']['session']

    def studio_feed(self):
        res = self.client.get('/studio/%s/feed/' % self.session()['token'])
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']['tournament']

    def test_the_feed_names_the_format_by_its_catalogue_key(self):
        # Stored the way an old row stored it; the explainer is keyed by the
        # catalogue's own name, and an alias reaching the page would draw
        # nothing.
        block = self.studio_feed()
        self.assertEqual(block['format'], 'swiss')
        self.assertEqual(block['stage_formats'], [])

    def test_the_public_overlay_feed_says_the_same(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['tournament']['format'], 'swiss')

    def test_stages_are_listed_in_the_order_they_are_played(self):
        TournamentStage.objects.create(tournament=self.tournament, order=2,
                                       label='Playoff', format='single_elimination')
        TournamentStage.objects.create(tournament=self.tournament, order=1,
                                       label='Groups', format='round_robin')
        block = self.studio_feed()
        self.assertEqual(block['stage_formats'], ['round_robin', 'single_elimination'])

    def test_a_format_the_catalogue_does_not_know_is_empty_not_an_error(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(bracket_type='')
        self.assertEqual(self.studio_feed()['format'], '')
