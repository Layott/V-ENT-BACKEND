# -*- coding: utf-8 -*-
"""Every element, and every presentation option, exhaustively.

CEO, 3 September 2026: "make sure all overlay options and features work as they
should in as much detail as you can check... dont just assume, test everything
and make sure they work as they should."

Everything before this checked a handful of elements and a couple of animation
settings by hand, which proves those and says nothing about the other forty.
This walks the whole matrix:

  13 element kinds across tournaments and events
   5 entrances x 5 exits x hold on or off = 50 combinations
   the three-way precedence: element over broadcast over house style

The faults this shape catches are the ones nobody finds by clicking: an option
accepted and then dropped, a kind that cannot be put on air, a default that
silently wins over an explicit choice. All of them look fine on the one element
somebody happened to try.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_event.models import Event
from vent_tournament import presentation
from vent_tournament.models import BroadcastElement, BroadcastSession, Tournament


def an_organiser(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=(name + 'z' * 16)[:16])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


@override_settings(FRONTEND_URL='https://v-ent.co')
class TournamentStudioMatrixTests(TestCase):
    """Every tournament graphic, and every way it can arrive and leave."""

    def setUp(self):
        self.organiser, self.auth = an_organiser('mx_org')
        game = Games.objects.create(game_title='EA FC MATRIX')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Matrix Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False, bracket_type='round_robin')
        self.ref = self.tournament.slug or self.tournament.tournament_id
        self.session = self.start()

    def start(self):
        res = self.client.post('/tournament/%s/studio/sessions/' % self.ref,
                               data={'name': 'Matrix'}, **self.auth)
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        return res.json()['data']['session']

    def element_url(self, kind):
        return ('/tournament/%s/studio/sessions/%s/element/%s/'
                % (self.ref, self.session['id'], kind))

    def push(self, kind, body):
        return self.client.post(self.element_url(kind), data=body,
                                content_type='application/json', **self.auth)

    def feed(self):
        res = self.client.get('/studio/%s/feed/' % self.session['token'])
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    # ------------------------------------------------------- every kind

    def test_every_tournament_kind_goes_on_air_and_reaches_the_feed(self):
        kinds = [k for k, _ in BroadcastElement.TOURNAMENT_KINDS]
        self.assertEqual(len(kinds), 11, 'the catalogue changed; update this')

        for kind in kinds:
            res = self.push(kind, {'active': True, 'payload': {'title': 'X'}})
            self.assertEqual(res.status_code, 200,
                             '%s could not go on air: %s' % (kind, res.content[:200]))

        data = self.feed()
        for kind in kinds:
            self.assertIn(kind, data['elements'], '%s is missing from the feed' % kind)
            self.assertTrue(data['elements'][kind]['active'],
                            '%s went on air and the feed says otherwise' % kind)

    def test_every_tournament_kind_has_its_own_address(self):
        for kind, _ in BroadcastElement.TOURNAMENT_KINDS:
            self.push(kind, {'active': True})
        # The session payload is where the console reads these from, so it is
        # the shape that has to be right. This session, not a fresh one: a new
        # one mints a new token.
        urls = self.session['urls']
        for kind, _ in BroadcastElement.TOURNAMENT_KINDS:
            self.assertIn(kind, urls, '%s has no URL to paste into OBS' % kind)
            # The address a person reads: the owner, the graphic, the token.
            self.assertIn('/studio/%s/%s/' % (self.tournament.slug, kind), urls[kind])
            self.assertIn(self.session['token'], urls[kind])

    def test_every_kind_can_be_taken_off_air_again(self):
        for kind, _ in BroadcastElement.TOURNAMENT_KINDS:
            self.push(kind, {'active': True})
            res = self.push(kind, {'active': False})
            self.assertEqual(res.status_code, 200, kind)
        data = self.feed()
        for kind, _ in BroadcastElement.TOURNAMENT_KINDS:
            self.assertFalse(data['elements'][kind]['active'], kind)

    # ------------------------------------------- every presentation option

    def test_every_entry_and_exit_and_hold_is_accepted_and_resolved(self):
        """Fifty combinations. One of them being dropped is invisible by hand."""
        checked = 0
        for entry in presentation.ENTRANCES:
            for exit_ in presentation.EXITS:
                for hold in (True, False):
                    res = self.push('scorebar', {
                        'active': True,
                        'payload': {'options': {
                            'entry': entry, 'exit': exit_, 'hold': hold}},
                    })
                    self.assertEqual(res.status_code, 200,
                                     '%s/%s/%s refused: %s'
                                     % (entry, exit_, hold, res.content[:160]))
                    look = self.feed()['elements']['scorebar']['presentation']
                    self.assertEqual(look['entry'], entry,
                                     'entry %s came back as %s' % (entry, look['entry']))
                    self.assertEqual(look['exit'], exit_,
                                     'exit %s came back as %s' % (exit_, look['exit']))
                    self.assertIs(look['hold'], hold,
                                  'hold %s came back as %s' % (hold, look['hold']))
                    checked += 1
        self.assertEqual(checked, 50)

    def test_an_option_nobody_offers_is_refused_on_every_field(self):
        for bad in ({'entry': 'explode'}, {'exit': 'implode'},
                    {'entrance': 'rise'}, {'duration_ms': 'soon'}):
            res = self.push('scorebar', {'active': True,
                                         'payload': {'options': bad}})
            self.assertEqual(res.status_code, 400, '%s was accepted' % bad)

    # ------------------------------------------------------- precedence

    def test_the_house_style_applies_when_nobody_says_otherwise(self):
        self.push('ticker', {'active': True})
        look = self.feed()['elements']['ticker']['presentation']
        self.assertEqual(look['entry'], presentation.DEFAULTS['entry'])
        self.assertEqual(look['exit'], presentation.DEFAULTS['exit'])

    def test_a_broadcast_default_beats_the_house_style(self):
        res = self.client.post(
            '/tournament/%s/studio/sessions/%s/' % (self.ref, self.session['id']),
            data={'defaults': {'entry': 'fade', 'hold': True}},
            content_type='application/json', **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])

        self.push('ticker', {'active': True})
        look = self.feed()['elements']['ticker']['presentation']
        self.assertEqual(look['entry'], 'fade')
        self.assertIs(look['hold'], True)
        # Untouched by the default, so still the house style.
        self.assertEqual(look['exit'], presentation.DEFAULTS['exit'])

    def test_a_graphic_beats_the_broadcast_default(self):
        self.client.post(
            '/tournament/%s/studio/sessions/%s/' % (self.ref, self.session['id']),
            data={'defaults': {'entry': 'fade'}},
            content_type='application/json', **self.auth)
        self.push('ticker', {'active': True,
                             'payload': {'options': {'entry': 'slide_right'}}})
        self.assertEqual(
            self.feed()['elements']['ticker']['presentation']['entry'], 'slide_right')

    def test_each_graphic_keeps_its_own_look(self):
        """One element's animation must not leak onto another."""
        self.push('scorebar', {'active': True,
                               'payload': {'options': {'entry': 'slide_left'}}})
        self.push('ticker', {'active': True,
                             'payload': {'options': {'entry': 'fade'}}})
        elements = self.feed()['elements']
        self.assertEqual(elements['scorebar']['presentation']['entry'], 'slide_left')
        self.assertEqual(elements['ticker']['presentation']['entry'], 'fade')

    def test_changing_the_look_does_not_take_a_graphic_off_air(self):
        self.push('scorebar', {'active': True, 'payload': {'home': 'Nigeria'}})
        self.push('scorebar', {'payload': {'options': {'entry': 'fade'}}})
        element = self.feed()['elements']['scorebar']
        self.assertTrue(element['active'], 'setting an animation took it off air')
        self.assertEqual(element['payload'].get('home'), 'Nigeria',
                         'setting an animation discarded the payload')

    # -------------------------------------------------------- a stranger

    def test_a_stranger_cannot_drive_any_of_it(self):
        _, theirs = an_organiser('mx_stranger')
        for kind, _ in BroadcastElement.TOURNAMENT_KINDS:
            res = self.client.post(self.element_url(kind),
                                   data={'active': True},
                                   content_type='application/json', **theirs)
            self.assertIn(res.status_code, (401, 403), kind)


@override_settings(FRONTEND_URL='https://v-ent.co')
class EventStudioMatrixTests(TestCase):
    """The same sweep on an event, because it is the half that gets forgotten."""

    def setUp(self):
        self.organiser, self.auth = an_organiser('mxe_org')
        game = Games.objects.create(game_title='EA FC MATRIX EV')
        now = timezone.now()
        self.event = Event.objects.create(
            name='Matrix Con', game=game, creator=self.organiser,
            event_type='physical', desc='A test',
            entry_fee=0, reg_start_date=now, reg_end_date=now + timedelta(days=1),
            event_date=(now + timedelta(days=2)).date(),
            start_time=now.time(), end_time=now.time(), is_active=True)
        self.ref = self.event.slug or self.event.event_id
        res = self.client.post('/event/%s/studio/sessions/' % self.ref,
                               data={'name': 'Matrix'}, **self.auth)
        self.assertIn(res.status_code, (200, 201), res.content[:300])
        self.session = res.json()['data']['session']

    def push(self, kind, body):
        return self.client.post(
            '/event/%s/studio/sessions/%s/element/%s/'
            % (self.ref, self.session['id'], kind),
            data=body, content_type='application/json', **self.auth)

    def feed(self):
        res = self.client.get('/studio/%s/feed/' % self.session['token'])
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    def test_every_event_kind_goes_on_air_and_reaches_the_feed(self):
        kinds = [k for k, _ in BroadcastElement.EVENT_KINDS]
        self.assertEqual(len(kinds), 9, 'the catalogue changed; update this')
        for kind in kinds:
            res = self.push(kind, {'active': True, 'payload': {'title': 'X'}})
            self.assertEqual(res.status_code, 200,
                             '%s could not go on air: %s' % (kind, res.content[:200]))
        data = self.feed()
        for kind in kinds:
            self.assertIn(kind, data['elements'], kind)
            self.assertTrue(data['elements'][kind]['active'], kind)

    def test_every_event_kind_has_its_own_address(self):
        for kind, _ in BroadcastElement.EVENT_KINDS:
            self.push(kind, {'active': True})
        urls = self.session['urls']
        for kind, _ in BroadcastElement.EVENT_KINDS:
            self.assertIn(kind, urls, '%s has no URL to paste into OBS' % kind)
            self.assertIn(self.session['token'], urls[kind])

    def test_the_presentation_options_work_the_same_on_an_event(self):
        for entry in presentation.ENTRANCES:
            res = self.push('now_next', {
                'active': True,
                'payload': {'options': {'entry': entry}}})
            self.assertEqual(res.status_code, 200, '%s refused' % entry)
            self.assertEqual(
                self.feed()['elements']['now_next']['presentation']['entry'], entry)

    def test_a_tournament_only_graphic_is_refused_on_an_event(self):
        """A bracket on an event would draw nothing and say nothing."""
        res = self.push('bracket', {'active': True})
        self.assertIn(res.status_code, (400, 404), res.content[:200])
        self.assertEqual(res.json()['code'], 'UNKNOWN_ELEMENT')
