"""The four layers an operator pastes into OBS once.

CEO, 6 September 2026, sending the RIVALRY control room: V-ENT should do "this
kind of setup for production, except that this one will be online and people
can upload anything they want and use to run overlays that will be updating in
realtime based off the tournament data and results". Then on 7 September:
"BUILD IT PROPERLY."

The gap was never the graphics, the feed or the uploads: all three existed. It
was that V-ENT gave one URL per graphic, so a broadcast using twenty kinds
meant twenty browser sources added and removed by hand DURING a show. Nobody
does that in a gallery. Four fixed sources, stacked once, and everything after
is a press in the console.
"""
from .models import BroadcastSession, BroadcastSlot, TournamentOverlay
from .tests_studio import StudioTests


class SlotTests(StudioTests):
    """Inherits the tournament, the organiser and `start()` from StudioTests.

    Reused rather than rebuilt, because a hand-written fixture is how a test
    ends up passing against a request nothing makes.
    """

    def slot_url(self, session_id, role):
        return '/tournament/%s/studio/sessions/%s/slot/%s/' % (
            self.ref, session_id, role)

    def put(self, session_id, role, body, auth=None):
        return self.client.post(
            self.slot_url(session_id, role), data=body,
            content_type='application/json',
            **(auth if auth is not None else self.auth))


class FourLayers(SlotTests):

    def test_a_session_publishes_four_slot_urls_with_roles(self):
        s = self.start().json()['data']['session']
        self.assertEqual(sorted(s['slot_urls'].keys()),
                         ['bg', 'bug', 'full', 'lower'])
        for role, url in s['slot_urls'].items():
            self.assertIn('/studio/', url)
            self.assertIn('slot-%s' % role, url)

    def test_all_four_layers_are_reported_before_anything_is_cued(self):
        """An empty layer is empty, never missing.

        The console draws four controls and OBS holds four sources from the
        first minute of a broadcast. Making the reader tell "no row yet" from
        "nothing in it" would put a hole in the panel every time.
        """
        s = self.start().json()['data']['session']
        self.assertEqual(sorted(s['slots'].keys()), ['bg', 'bug', 'full', 'lower'])
        for role, state in s['slots'].items():
            self.assertEqual(state['holds'], '')
            self.assertFalse(state['active'])

    def test_every_role_has_a_human_label(self):
        s = self.start().json()['data']['session']
        self.assertEqual(s['slots']['lower']['label'], 'Lower third')
        self.assertEqual(s['slots']['bug']['label'], 'Corner bug')


class AddressIsStable(SlotTests):
    """The entire point: what a slot shows changes, its address never does."""

    def test_the_url_does_not_move_when_the_contents_do(self):
        s = self.start().json()['data']['session']
        before = dict(s['slot_urls'])

        self.put(s['id'], 'full', {'item_kind': 'standings', 'active': True})
        self.put(s['id'], 'full', {'item_kind': 'bracket'})
        self.put(s['id'], 'lower', {'item_kind': 'lower_third', 'active': True})

        after = self.client.get(
            '/tournament/%s/studio/sessions/' % self.ref, **self.auth
        ).json()['data']['sessions'][0]
        self.assertEqual(before, after['slot_urls'])

    def test_the_older_address_shape_is_published_too(self):
        """A source pasted into a machine at a venue keeps working."""
        s = self.start().json()['data']['session']
        for role in ('bg', 'full', 'lower', 'bug'):
            self.assertIn('slot-%s' % role, s['legacy_slot_urls'][role])


class AnythingGoesInASlot(SlotTests):

    def test_a_house_graphic_can_be_put_in_a_layer(self):
        s = self.start().json()['data']['session']
        res = self.put(s['id'], 'full', {'item_kind': 'standings', 'active': True})
        self.assertEqual(res.status_code, 200, res.content[:400])
        state = res.json()['data']['session']['slots']['full']
        self.assertEqual(state['holds'], 'element')
        self.assertEqual(state['item_kind'], 'standings')
        self.assertTrue(state['active'])

    def test_an_uploaded_overlay_can_be_put_in_a_layer(self):
        """"people can upload anything they want and use to run overlays"."""
        s = self.start().json()['data']['session']
        overlay = TournamentOverlay.objects.create(
            tournament=self.tournament, name='Sponsor bed', token='ov-slot-1')
        res = self.put(s['id'], 'bg', {'overlay_id': overlay.id, 'active': True})
        self.assertEqual(res.status_code, 200, res.content[:400])
        state = res.json()['data']['session']['slots']['bg']
        self.assertEqual(state['holds'], 'overlay')
        self.assertEqual(state['overlay_id'], overlay.id)
        self.assertEqual(state['overlay_name'], 'Sponsor bed')
        # The token, so the slot page can build the same address an
        # organiser would paste into OBS directly.
        self.assertEqual(state['overlay_token'], 'ov-slot-1')

    def test_putting_an_overlay_in_clears_the_graphic_that_was_there(self):
        """A layer showing two things is how a stale graphic ends up on air."""
        s = self.start().json()['data']['session']
        overlay = TournamentOverlay.objects.create(
            tournament=self.tournament, name='Bed', token='ov-slot-2')
        self.put(s['id'], 'full', {'item_kind': 'standings'})
        res = self.put(s['id'], 'full', {'overlay_id': overlay.id})
        state = res.json()['data']['session']['slots']['full']
        self.assertEqual(state['item_kind'], '')
        self.assertEqual(state['holds'], 'overlay')

    def test_putting_a_graphic_in_clears_the_overlay_that_was_there(self):
        s = self.start().json()['data']['session']
        overlay = TournamentOverlay.objects.create(
            tournament=self.tournament, name='Bed', token='ov-slot-3')
        self.put(s['id'], 'full', {'overlay_id': overlay.id})
        res = self.put(s['id'], 'full', {'item_kind': 'bracket'})
        state = res.json()['data']['session']['slots']['full']
        self.assertIsNone(state['overlay_id'])
        self.assertEqual(state['holds'], 'element')

    def test_a_layer_can_be_emptied(self):
        s = self.start().json()['data']['session']
        self.put(s['id'], 'full', {'item_kind': 'standings', 'active': True})
        res = self.put(s['id'], 'full', {'item_kind': '', 'active': False})
        state = res.json()['data']['session']['slots']['full']
        self.assertEqual(state['holds'], '')
        self.assertFalse(state['active'])

    def test_loading_a_layer_dark_and_taking_it_up_are_separate_presses(self):
        """How a gallery works: cue the next graphic, then take it."""
        s = self.start().json()['data']['session']
        loaded = self.put(s['id'], 'full', {'item_kind': 'bracket'})
        self.assertFalse(loaded.json()['data']['session']['slots']['full']['active'])
        taken = self.put(s['id'], 'full', {'active': True})
        up = taken.json()['data']['session']['slots']['full']
        self.assertTrue(up['active'])
        self.assertEqual(up['item_kind'], 'bracket')

    def test_a_graphic_this_kind_does_not_have_is_refused_by_name(self):
        s = self.start().json()['data']['session']
        res = self.put(s['id'], 'full', {'item_kind': 'programme'})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'UNKNOWN_ELEMENT')

    def test_an_unknown_layer_is_refused(self):
        s = self.start().json()['data']['session']
        res = self.put(s['id'], 'sidebar', {'item_kind': 'standings'})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'UNKNOWN_SLOT')

    def test_there_is_only_ever_one_row_per_role(self):
        s = self.start().json()['data']['session']
        for _ in range(3):
            self.put(s['id'], 'full', {'item_kind': 'standings'})
        self.assertEqual(
            BroadcastSlot.objects.filter(session_id=s['id'], role='full').count(), 1)


class TokenIsTheCredential(SlotTests):

    def test_the_feed_a_slot_page_reads_needs_only_the_token(self):
        s = self.start().json()['data']['session']
        self.put(s['id'], 'lower', {'item_kind': 'lower_third', 'active': True})
        # No Authorization header: a browser source has none.
        res = self.client.get('/studio/%s/feed/' % s['token'])
        self.assertEqual(res.status_code, 200)
        slots = res.json()['data']['slots']
        self.assertEqual(slots['lower']['item_kind'], 'lower_third')
        self.assertTrue(slots['lower']['active'])

    def test_a_stranger_cannot_cue_anything(self):
        s = self.start().json()['data']['session']
        res = self.put(s['id'], 'full', {'item_kind': 'standings'},
                       auth=self.other_auth)
        self.assertEqual(res.status_code, 403)

    def test_signed_out_cannot_cue_anything(self):
        s = self.start().json()['data']['session']
        res = self.client.post(self.slot_url(s['id'], 'full'),
                               data={'item_kind': 'standings'},
                               content_type='application/json')
        self.assertIn(res.status_code, (401, 403))

    def test_an_ended_broadcast_refuses_and_its_urls_retire(self):
        s = self.start().json()['data']['session']
        BroadcastSession.objects.filter(pk=s['id']).update(status='ended')
        res = self.put(s['id'], 'full', {'item_kind': 'standings'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'BROADCAST_ENDED')

    def test_an_overlay_from_another_broadcast_cannot_be_put_on_air(self):
        """Otherwise one token puts somebody else's uploaded file on screen."""
        from .models import Tournament
        from django.utils import timezone
        from datetime import timedelta
        from vent_auth.models import Games
        other = Tournament.objects.create(
            tournament_title='Not Mine',
            tournament_game=Games.objects.first() or Games.objects.create(game_title='X'),
            tournament_creator=self.other,
            start_date_and_time=timezone.now() + timedelta(days=1),
            end_date_and_time=timezone.now() + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False)
        theirs = TournamentOverlay.objects.create(
            tournament=other, name='Theirs', token='ov-slot-other')

        s = self.start().json()['data']['session']
        res = self.put(s['id'], 'bg', {'overlay_id': theirs.id})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'UNKNOWN_OVERLAY')


class TheVersionMovesWhenASlotDoes(SlotTests):
    """A slot page skips its redraw when the version has not moved.

    So if cueing a graphic did not move the stamp, the operator would press the
    button, the console would show the change, and nothing would happen on air
    until something ELSE happened to move it. That fault has already shipped
    twice here, on the look and on the text layers.
    """

    def version(self, token):
        return self.client.get('/studio/%s/feed/' % token).json()['data']['version']

    def test_cueing_a_graphic_moves_the_version(self):
        s = self.start().json()['data']['session']
        before = self.version(s['token'])
        self.put(s['id'], 'full', {'item_kind': 'standings', 'active': True})
        self.assertNotEqual(before, self.version(s['token']))

    def test_taking_a_layer_down_moves_the_version(self):
        s = self.start().json()['data']['session']
        self.put(s['id'], 'full', {'item_kind': 'standings', 'active': True})
        before = self.version(s['token'])
        self.put(s['id'], 'full', {'active': False})
        self.assertNotEqual(before, self.version(s['token']))

    def test_swapping_what_is_in_a_layer_moves_the_version(self):
        s = self.start().json()['data']['session']
        self.put(s['id'], 'full', {'item_kind': 'standings', 'active': True})
        before = self.version(s['token'])
        self.put(s['id'], 'full', {'item_kind': 'bracket'})
        self.assertNotEqual(before, self.version(s['token']))
