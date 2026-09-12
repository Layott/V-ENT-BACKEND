"""Text on top of any overlay: both kinds, both prefixes, and the safety rule.

CEO, 4 September 2026, inbox row 52: "also should be able to add text, change
the font size, color, position, animation of that text also on any overlay".

The rules these tests exist to hold:

* **"Any overlay" means both kinds.** A studio graphic V-ENT draws and an HTML
  file an organiser uploaded. Built for one and forgotten on the other is the
  fault `tools/check-parity.py` exists for, and it has happened five times in a
  day on this platform.
* **And both prefixes.** An event broadcast has captions exactly as a
  tournament does.
* **A refusal carries a code and the field.** A sentence built in Python cannot
  be translated, so the console is told which box is wrong and what would have
  been right.
* **The feed version moves when a layer moves.** An element page skips its
  redraw when the version has not, so a layer edited under a stale stamp is a
  change nobody on air ever sees.
* **A file with no layers is served exactly as it was.** No container, no
  stylesheet, no class, no attribute. Asserted on the served HTML, because the
  model having no rows is not the same statement.
"""
import json
import os
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from vent_auth.models import Games, Users, UserWallet
from vent_event.models import Event

from .models import (BroadcastElement, BroadcastSession, OverlayLayer,
                     Tournament, TournamentOverlay)

MARKED = """<!doctype html><html><head><title>Scoreboard</title></head><body>
  <h1 data-vent="tournament.title"></h1>
</body></html>"""


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name,
        login_session_token=('l-%s' % name)[:16], is_active=True)
    user.login_session_created_at = timezone.now()
    user.login_session_2fa_at = timezone.now()
    user.save()
    UserWallet.objects.create(
        user_wallet_id=('lw%s' % name)[:10], user=user, wallet_balance=0,
        pin_hash=make_password('1234'))
    return user


class LayerCase(TestCase):
    """The fixture every one of these needs: a tournament, an event, two people."""

    def setUp(self):
        self.client = APIClient()
        self.organiser = a_user('layA')
        self.other = a_user('layB')
        game = Games.objects.create(game_title='EA FC 26')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Rivalry Series', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=2),
            end_date_and_time=now + timedelta(days=4),
            tournament_visibility='public', tournament_type='physical',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False)
        self.event = Event.objects.create(
            name='Lagos Anime Con', creator=self.organiser,
            event_type='physical', desc='x', entry_fee=Decimal('0'),
            reg_start_date=now, reg_end_date=now, event_date=now.date(),
            start_time=now.time(), end_time=now.time(),
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=8), venue_name='Landmark Centre')
        self.t_ref = self.tournament.slug or self.tournament.tournament_id
        self.e_ref = self.event.slug or self.event.event_id
        self.as_organiser()

    # ----------------------------------------------------------------- who

    def as_organiser(self):
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.organiser.login_session_token)

    def as_stranger(self):
        self.client.credentials(
            HTTP_AUTHORIZATION='Bearer %s' % self.other.login_session_token)

    def signed_out(self):
        self.client.credentials()

    # -------------------------------------------------------------- addresses

    def studio(self, kind='tournament'):
        return '/%s/%s/studio/sessions/' % (
            kind, self.e_ref if kind == 'event' else self.t_ref)

    def start(self, kind='tournament'):
        res = self.client.post(self.studio(kind), {'name': 'Day 1'},
                               format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.data['data']['session']

    def element_layers(self, kind, session_id, element_kind, layer_id=None):
        base = '%s%d/element/%s/layers/' % (self.studio(kind), session_id,
                                            element_kind)
        return base if layer_id is None else '%s%d/' % (base, layer_id)

    def overlay_layers(self, kind, overlay_id, layer_id=None):
        base = '/%s/%s/overlays/%d/layers/' % (
            kind, self.e_ref if kind == 'event' else self.t_ref, overlay_id)
        return base if layer_id is None else '%s%d/' % (base, layer_id)

    def upload(self, kind='tournament'):
        ref = self.e_ref if kind == 'event' else self.t_ref
        res = self.client.post(
            '/%s/%s/overlays/' % (kind, ref),
            {'file': SimpleUploadedFile('board.html', MARKED.encode('utf-8'),
                                        content_type='text/html')},
            format='multipart')
        self.assertEqual(res.status_code, 201, res.content[:300])
        return res.data['data']['overlay']


class StudioGraphicLayerTests(LayerCase):
    """A layer on a graphic V-ENT draws, under the tournament prefix."""

    def setUp(self):
        super().setUp()
        self.session = self.start()
        self.url = self.element_layers('tournament', self.session['id'],
                                       'scorebar')

    def add(self, **body):
        payload = {'text': 'GRAND FINAL'}
        payload.update(body)
        return self.client.post(self.url, payload, format='json')

    def test_a_layer_can_be_put_on_a_graphic(self):
        res = self.add(font_size=64, colour='#f2d024', position='bottom_centre',
                       entry='rise', exit='fade')
        self.assertEqual(res.status_code, 200, res.content[:400])
        layers = res.data['data']['layers']
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0]['text'], 'GRAND FINAL')
        self.assertEqual(layers[0]['font_size'], 64)
        # Upper cased and otherwise exactly what was sent. Case is the only
        # thing normalised anywhere in this module.
        self.assertEqual(layers[0]['colour'], '#F2D024')

    def test_the_shape_is_the_one_both_halves_agreed_on(self):
        layer = self.add().data['data']['layers'][0]
        for key in ('id', 'text', 'field', 'font_size', 'colour', 'family',
                    'font_slot', 'weight', 'align', 'position', 'offset_x',
                    'offset_y', 'entry', 'exit', 'delay_ms', 'duration_ms',
                    'order'):
            self.assertIn(key, layer, key)

    def test_reading_them_back_does_not_create_the_graphic(self):
        """The console opens every panel as the operator scrolls."""
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['layers'], [])
        self.assertEqual(BroadcastElement.objects.filter(
            session_id=self.session['id'], kind='scorebar').count(), 0)

    def test_a_layer_can_be_corrected_and_the_list_comes_back_changed(self):
        layer = self.add().data['data']['layers'][0]
        res = self.client.post(
            self.element_layers('tournament', self.session['id'], 'scorebar',
                                layer['id']),
            {'text': 'SEMI FINAL', 'font_size': 90}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        # Read back from the database, not patched in memory. A response built
        # from the list as it was is how a save appears to have done nothing.
        self.assertEqual(res.data['data']['layers'][0]['text'], 'SEMI FINAL')
        self.assertEqual(res.data['data']['layers'][0]['font_size'], 90)

    def test_a_layer_can_be_removed(self):
        layer = self.add().data['data']['layers'][0]
        res = self.client.delete(
            self.element_layers('tournament', self.session['id'], 'scorebar',
                                layer['id']))
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.data['data']['layers'], [])
        self.assertEqual(OverlayLayer.objects.count(), 0)

    def test_switched_off_rather_than_deleted_is_kept(self):
        """What an operator does mid show. A deleted layer has to be retyped."""
        layer = self.add().data['data']['layers'][0]
        res = self.client.post(
            self.element_layers('tournament', self.session['id'], 'scorebar',
                                layer['id']),
            {'is_active': False}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['data']['layers']), 1)
        self.assertFalse(res.data['data']['layers'][0]['is_active'])

    def test_the_console_is_given_the_lists_so_it_keeps_none_of_its_own(self):
        options = self.client.get(self.url).data['data']['text_options']
        from . import presentation
        self.assertEqual(options['positions'], list(presentation.POSITIONS))
        self.assertEqual(options['entrances'], list(presentation.ENTRANCES))
        self.assertEqual(options['exits'], list(presentation.EXITS))
        self.assertEqual(options['offset_limit'], presentation.OFFSET_LIMIT)

    # --------------------------------------------------------------- the feed

    def test_the_feed_carries_the_layers_on_each_element(self):
        self.add(text='ON AIR')
        self.signed_out()                       # OBS has no session at all
        feed = self.client.get('/studio/%s/feed/' % self.session['token'])
        self.assertEqual(feed.status_code, 200, feed.content[:300])
        layers = feed.data['data']['elements']['scorebar']['layers']
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0]['text'], 'ON AIR')

    def test_the_feed_sends_only_the_layers_that_are_on(self):
        first = self.add(text='ONE').data['data']['layers'][0]
        self.add(text='TWO')
        self.client.post(
            self.element_layers('tournament', self.session['id'], 'scorebar',
                                first['id']),
            {'is_active': False}, format='json')
        self.signed_out()
        feed = self.client.get('/studio/%s/feed/' % self.session['token'])
        layers = feed.data['data']['elements']['scorebar']['layers']
        self.assertEqual([row['text'] for row in layers], ['TWO'])

    def test_the_feed_sends_them_in_paint_order(self):
        self.add(text='UNDER', order=5)
        self.add(text='OVER', order=1)
        self.signed_out()
        feed = self.client.get('/studio/%s/feed/' % self.session['token'])
        layers = feed.data['data']['elements']['scorebar']['layers']
        self.assertEqual([row['text'] for row in layers], ['OVER', 'UNDER'])

    def test_an_element_with_no_layers_carries_an_empty_list(self):
        """Present and empty rather than absent: a page reading a name that is
        not there throws on the way to drawing nothing."""
        self.signed_out()
        feed = self.client.get('/studio/%s/feed/' % self.session['token'])
        self.assertEqual(feed.data['data']['elements']['standings']['layers'],
                         [])


class FeedVersionTests(LayerCase):
    """The stamp an element page compares before it redraws.

    A layer edited under a stale version is a change nobody on air ever sees.
    It has happened twice here already, to squad depth and to the broadcast
    look, which is why this is asserted rather than assumed.
    """

    def setUp(self):
        super().setUp()
        self.session = self.start()
        self.url = self.element_layers('tournament', self.session['id'],
                                       'scorebar')
        # The graphic first, so what moves below is the LAYER stamp and not the
        # element's own `updated_at` being touched for the first time.
        self.client.post('%s%d/element/scorebar/' % (self.studio(),
                                                     self.session['id']),
                         {'active': True}, format='json')

    def version(self):
        res = self.client.get('%s%d/' % (self.studio(), self.session['id']))
        return res.data['data']['session']['version']

    def test_the_version_moves_when_a_layer_is_added(self):
        before = self.version()
        self.client.post(self.url, {'text': 'ONE'}, format='json')
        self.assertNotEqual(before, self.version())

    def test_the_version_moves_when_a_layer_is_edited(self):
        layer = self.client.post(self.url, {'text': 'ONE'},
                                 format='json').data['data']['layers'][0]
        before = self.version()
        self.client.post(self.element_layers('tournament', self.session['id'],
                                             'scorebar', layer['id']),
                         {'colour': '#FF0000'}, format='json')
        self.assertNotEqual(before, self.version())

    def test_the_version_moves_when_a_layer_is_reordered(self):
        self.client.post(self.url, {'text': 'ONE'}, format='json')
        second = self.client.post(self.url, {'text': 'TWO'},
                                  format='json').data['data']['layers'][1]
        before = self.version()
        self.client.post(self.element_layers('tournament', self.session['id'],
                                             'scorebar', second['id']),
                         {'order': 0}, format='json')
        self.assertNotEqual(before, self.version())

    def test_the_version_moves_when_a_layer_is_removed(self):
        layer = self.client.post(self.url, {'text': 'ONE'},
                                 format='json').data['data']['layers'][0]
        before = self.version()
        self.client.delete(self.element_layers('tournament',
                                               self.session['id'], 'scorebar',
                                               layer['id']))
        self.assertNotEqual(before, self.version())


class EventLayerTests(LayerCase):
    """Every address again, under the event prefix. Nothing else may differ."""

    def test_a_layer_on_an_event_graphic(self):
        session = self.start('event')
        url = self.element_layers('event', session['id'], 'now_next')
        res = self.client.post(url, {'text': 'UP NEXT'}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.assertEqual(res.data['data']['layers'][0]['text'], 'UP NEXT')

    def test_a_graphic_an_event_does_not_have_is_named_not_a_bare_404(self):
        session = self.start('event')
        res = self.client.post(self.element_layers('event', session['id'],
                                                   'bracket'),
                               {'text': 'x'}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.data['code'], 'UNKNOWN_ELEMENT')

    def test_a_layer_on_an_event_uploaded_overlay(self):
        overlay = self.upload('event')
        res = self.client.post(self.overlay_layers('event', overlay['id']),
                               {'text': 'LAGOS'}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:400])
        self.assertEqual(res.data['data']['layers'][0]['text'], 'LAGOS')

    def test_an_event_layer_can_be_edited_and_removed(self):
        overlay = self.upload('event')
        layer = self.client.post(self.overlay_layers('event', overlay['id']),
                                 {'text': 'LAGOS'},
                                 format='json').data['data']['layers'][0]
        edited = self.client.post(
            self.overlay_layers('event', overlay['id'], layer['id']),
            {'text': 'LAGOS 2026'}, format='json')
        self.assertEqual(edited.data['data']['layers'][0]['text'], 'LAGOS 2026')
        gone = self.client.delete(
            self.overlay_layers('event', overlay['id'], layer['id']))
        self.assertEqual(gone.data['data']['layers'], [])


@override_settings(FRONTEND_URL='https://v-ent.co')
class UploadedFileLayerTests(LayerCase):
    """Layers on somebody else's design, and the rule about not touching it."""

    def test_a_file_with_no_layers_is_served_exactly_as_it_was(self):
        """The safety rule, asserted on the HTML rather than on the model.

        Nothing injected: no container, no stylesheet, no class, not even the
        attribute that would make the runtime look for one.
        """
        overlay = self.upload()
        self.signed_out()
        body = self.client.get(
            overlay['url'].split('testserver', 1)[-1]).content.decode('utf-8')
        self.assertNotIn('data-layers', body)
        self.assertNotIn('vent-layers', body)
        # And the file itself, untouched, with only the runtime in front of it.
        self.assertIn('data-vent="tournament.title"', body)

    def test_a_layer_reaches_the_served_page(self):
        overlay = self.upload()
        self.client.post(self.overlay_layers('tournament', overlay['id']),
                         {'text': 'GRAND FINAL', 'colour': '#F2D024',
                          'position': 'top_right'}, format='json')
        self.signed_out()
        body = self.client.get(
            overlay['url'].split('testserver', 1)[-1]).content.decode('utf-8')
        self.assertIn('data-layers', body)
        self.assertIn('GRAND FINAL', body)
        # Carried on the runtime's own tag, so the uploader's markup is still
        # the file they debugged against.
        self.assertLess(body.index('data-layers'),
                        body.index('data-vent="tournament.title"'))

    def test_the_layers_on_the_page_are_the_ones_that_are_on(self):
        overlay = self.upload()
        url = self.overlay_layers('tournament', overlay['id'])
        first = self.client.post(url, {'text': 'ONE'},
                                 format='json').data['data']['layers'][0]
        self.client.post(url, {'text': 'TWO'}, format='json')
        self.client.post(self.overlay_layers('tournament', overlay['id'],
                                             first['id']),
                         {'is_active': False}, format='json')
        self.signed_out()
        body = self.client.get(
            overlay['url'].split('testserver', 1)[-1]).content.decode('utf-8')
        self.assertIn('TWO', body)
        self.assertNotIn('ONE', body)

    def test_what_is_injected_is_readable_json_in_the_agreed_shape(self):
        overlay = self.upload()
        self.client.post(self.overlay_layers('tournament', overlay['id']),
                         {'text': 'A "quoted" caption & more'}, format='json')
        self.signed_out()
        body = self.client.get(
            overlay['url'].split('testserver', 1)[-1]).content.decode('utf-8')
        # The attribute is escaped, so a caption with a quote in it cannot end
        # the attribute and rewrite the tag.
        self.assertIn('&quot;', body)
        raw = body.split('data-layers="', 1)[1].split('"', 1)[0]
        parsed = json.loads(raw.replace('&quot;', '"').replace('&#x27;', "'")
                            .replace('&lt;', '<').replace('&gt;', '>')
                            .replace('&amp;', '&'))
        self.assertEqual(parsed[0]['text'], 'A "quoted" caption & more')

    def test_the_runtime_does_nothing_at_all_when_there_are_no_layers(self):
        """The other half of the safety rule, in the file that draws them.

        The server can only promise not to write the attribute. This is the
        guard that makes an absent attribute mean nothing is built.
        """
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'static', 'overlay-runtime.js')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        self.assertIn("var raw = tag && tag.getAttribute('data-layers');",
                      source)
        self.assertIn('if (!raw) return [];', source)
        self.assertIn('if (!LAYERS.length || layerNodes) return;', source)

    def test_an_overlay_belonging_to_somebody_else_is_not_found(self):
        """Filtered by owner, or one organiser writes onto another's broadcast."""
        overlay = self.upload()
        other_tournament = Tournament.objects.create(
            tournament_title='Other', tournament_game=self.tournament.tournament_game,
            tournament_creator=self.organiser,
            start_date_and_time=timezone.now() + timedelta(days=2),
            end_date_and_time=timezone.now() + timedelta(days=3),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False)
        res = self.client.post(
            '/tournament/%s/overlays/%d/layers/' % (
                other_tournament.slug or other_tournament.tournament_id,
                overlay['id']),
            {'text': 'x'}, format='json')
        self.assertEqual(res.status_code, 404)


class RefusalTests(LayerCase):
    """Who is refused, what is refused, and the code that says which."""

    def setUp(self):
        super().setUp()
        self.session = self.start()
        self.url = self.element_layers('tournament', self.session['id'],
                                       'scorebar')
        self.overlay = self.upload()
        self.overlay_url = self.overlay_layers('tournament',
                                               self.overlay['id'])

    # ------------------------------------------------------------------ who

    def test_signed_out_cannot_add_to_a_graphic(self):
        self.signed_out()
        res = self.client.post(self.url, {'text': 'x'}, format='json')
        self.assertIn(res.status_code, (401, 403), res.content[:200])
        self.assertEqual(res.data['code'], 'NOT_TOURNAMENT_ORGANIZER')

    def test_signed_out_cannot_add_to_an_uploaded_file(self):
        self.signed_out()
        res = self.client.post(self.overlay_url, {'text': 'x'}, format='json')
        self.assertIn(res.status_code, (401, 403))

    def test_signed_out_cannot_even_read_them(self):
        self.signed_out()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
        self.assertIn(self.client.get(self.overlay_url).status_code, (401, 403))

    def test_a_signed_in_stranger_is_refused_on_both_kinds(self):
        """The role nobody walks: signed in, not the organiser."""
        self.as_stranger()
        self.assertEqual(
            self.client.post(self.url, {'text': 'x'}, format='json').status_code,
            403)
        self.assertEqual(
            self.client.post(self.overlay_url, {'text': 'x'},
                             format='json').status_code, 403)

    def test_a_stranger_cannot_edit_a_layer_somebody_else_made(self):
        layer = self.client.post(self.url, {'text': 'ONE'},
                                 format='json').data['data']['layers'][0]
        self.as_stranger()
        res = self.client.post(
            self.element_layers('tournament', self.session['id'], 'scorebar',
                                layer['id']),
            {'text': 'HACKED'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(OverlayLayer.objects.get(pk=layer['id']).text,
                         'ONE')

    # ---------------------------------------------------------------- what

    def test_a_colour_that_is_not_a_colour_is_refused_with_a_code(self):
        res = self.client.post(self.url, {'text': 'x', 'colour': 'red'},
                               format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'INVALID_COLOUR')
        self.assertEqual(res.data['field'], 'colour')

    def test_an_empty_colour_is_refused_rather_than_defaulted(self):
        res = self.client.post(self.url, {'text': 'x', 'colour': ''},
                               format='json')
        self.assertEqual(res.data['code'], 'INVALID_COLOUR')

    def test_a_three_digit_colour_is_refused_rather_than_expanded(self):
        """Silent correction is the one thing this must never do."""
        res = self.client.post(self.url, {'text': 'x', 'colour': '#fff'},
                               format='json')
        self.assertEqual(res.data['code'], 'INVALID_COLOUR')

    def test_an_alpha_colour_is_accepted(self):
        res = self.client.post(self.url, {'text': 'x', 'colour': '#00000080'},
                               format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])

    def test_a_position_that_is_not_one_of_the_nine_is_refused_with_a_code(self):
        res = self.client.post(self.url, {'text': 'x', 'position': 'middle'},
                               format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'INVALID_CHOICE')
        self.assertEqual(res.data['field'], 'position')
        # The list the console needs to say what would have been right.
        from . import presentation
        self.assertEqual(res.data['data']['allowed'],
                         list(presentation.POSITIONS))

    def test_an_offset_off_the_frame_is_refused_with_the_limits(self):
        res = self.client.post(self.url, {'text': 'x', 'offset_x': 5000},
                               format='json')
        self.assertEqual(res.data['code'], 'INVALID_NUMBER')
        self.assertEqual(res.data['field'], 'offset_x')
        self.assertEqual(res.data['data']['max'], 800)

    def test_a_font_size_out_of_range_is_refused(self):
        res = self.client.post(self.url, {'text': 'x', 'font_size': 900},
                               format='json')
        self.assertEqual(res.data['code'], 'INVALID_NUMBER')
        self.assertEqual(res.data['data'], {'min': 8, 'max': 400})

    def test_a_weight_the_font_does_not_have_is_refused(self):
        res = self.client.post(self.url, {'text': 'x', 'weight': 550},
                               format='json')
        self.assertEqual(res.data['code'], 'INVALID_CHOICE')
        self.assertEqual(res.data['field'], 'weight')

    def test_an_entry_that_does_not_exist_is_refused(self):
        res = self.client.post(self.url, {'text': 'x', 'entry': 'explode'},
                               format='json')
        self.assertEqual(res.data['code'], 'INVALID_CHOICE')
        self.assertEqual(res.data['field'], 'entry')

    def test_a_setting_that_does_not_exist_is_refused_not_dropped(self):
        """An operator who set `color` and saw it ignored would set it again."""
        res = self.client.post(self.url, {'text': 'x', 'color': '#fff'},
                               format='json')
        self.assertEqual(res.data['code'], 'UNKNOWN_FIELD')
        self.assertEqual(res.data['field'], 'color')

    def test_a_font_slot_that_could_carry_a_quote_into_css_is_refused(self):
        res = self.client.post(self.url, {'text': 'x', 'font_slot': "a';x"},
                               format='json')
        self.assertEqual(res.data['code'], 'INVALID_SLOT')

    def test_a_layer_with_nothing_to_say_is_refused(self):
        res = self.client.post(self.url, {'text': '   '}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'VALIDATION_FAILED')
        self.assertEqual(res.data['field'], 'text')

    def test_a_layer_bound_to_a_feed_path_needs_no_words(self):
        res = self.client.post(self.url, {'field': 'tournament.title'},
                               format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.data['data']['layers'][0]['field'],
                         'tournament.title')

    def test_an_edit_cannot_empty_a_layer_either(self):
        layer = self.client.post(self.url, {'text': 'ONE'},
                                 format='json').data['data']['layers'][0]
        res = self.client.post(
            self.element_layers('tournament', self.session['id'], 'scorebar',
                                layer['id']), {'text': ''}, format='json')
        self.assertEqual(res.data['code'], 'VALIDATION_FAILED')
        self.assertEqual(OverlayLayer.objects.get(pk=layer['id']).text,
                         'ONE')

    def test_an_ended_broadcast_refuses_a_new_layer(self):
        self.client.post('%s%d/' % (self.studio(), self.session['id']),
                         {'end': True}, format='json')
        res = self.client.post(self.url, {'text': 'x'}, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'BROADCAST_ENDED')

    def test_a_broadcast_that_does_not_exist_is_not_found(self):
        res = self.client.get(self.element_layers('tournament', 999999,
                                                  'scorebar'))
        self.assertEqual(res.status_code, 404)


class OwnerTests(TestCase):
    """Exactly one owner, held where it can actually bite.

    No route can express "both" or "neither": the address names the owner. So
    the invariant lives on the model, and this is what proves it does.
    """

    def setUp(self):
        organiser = a_user('ownA')
        game = Games.objects.create(game_title='EA FC 26')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Owners', tournament_game=game,
            tournament_creator=organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False)
        session = BroadcastSession.objects.create(tournament=self.tournament,
                                                  started_by=organiser)
        self.element = BroadcastElement.objects.create(session=session,
                                                       kind='scorebar')
        self.overlay = TournamentOverlay.objects.create(
            tournament=self.tournament, name='board.html', file='x.html')

    def test_a_layer_with_neither_owner_is_refused(self):
        with self.assertRaises(ValueError):
            OverlayLayer(text='x').save()

    def test_a_layer_with_both_owners_is_refused(self):
        with self.assertRaises(ValueError):
            OverlayLayer(text='x', element=self.element,
                             overlay=self.overlay).save()

    def test_a_layer_with_one_owner_saves(self):
        OverlayLayer(text='x', element=self.element).save()
        OverlayLayer(text='x', overlay=self.overlay).save()
        self.assertEqual(OverlayLayer.objects.count(), 2)
