# -*- coding: utf-8 -*-
"""A layer that draws a picture or a clip rather than words.

CEO, 4 September 2026, inbox row 51: "there should be elements you can add or
ways to add certan uploaded things like images, sponsor logos, player images or
videos as like elements that will then be movable inside an element once they
are loaded".

The same model as a text layer, with `kind` deciding what is painted. That is
the assertion running under all of these: everything around the media, where it
sits, its nudge, its order, its entrance, its delay, is the code a caption
already used, so a fix to one reaches both. A second table would have been the
same feature built twice and the second copy would be the one missing whatever
the first grew a week later.
"""
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import OverlayLayer, StudioAsset
from .tests_overlay_layers import LayerCase

PIXEL = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
         b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
         b'\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


class AssetLayerTests(LayerCase):

    def setUp(self):
        super().setUp()
        self.session = self.start()
        self.url = self.element_layers('tournament', self.session['id'],
                                       'scorebar')
        self.asset = StudioAsset.objects.create(
            tournament=self.tournament, kind='image', name='Sponsor',
            file=SimpleUploadedFile('sponsor.png', PIXEL,
                                    content_type='image/png'))

    def add(self, **payload):
        body = {'kind': 'asset', 'asset_id': self.asset.id}
        body.update(payload)
        return self.client.post(self.url, body, format='json')

    # ------------------------------------------------------------- adding it

    def test_an_operator_can_put_a_sponsor_logo_on_a_graphic(self):
        res = self.add(width_px=320, position='bottom_right')
        self.assertEqual(res.status_code, 200, res.content[:300])

        row = res.data['data']['layers'][0]
        self.assertEqual(row['kind'], 'asset')
        self.assertEqual(row['asset_id'], self.asset.id)
        self.assertEqual(row['width_px'], 320)
        self.assertEqual(row['position'], 'bottom_right')
        # The page draws it without a second request.
        self.assertTrue(row['asset_url'])
        self.assertEqual(row['asset_kind'], 'image')
        self.assertEqual(row['asset_name'], 'Sponsor')

    def test_it_keeps_everything_a_caption_has(self):
        """The point of one model: the same controls, not a reduced set."""
        res = self.add(order=3, entry='fade', exit='drop', delay_ms=500,
                       duration_ms=8000, offset_x=-40, offset_y=20)
        self.assertEqual(res.status_code, 200, res.content[:300])
        row = res.data['data']['layers'][0]
        self.assertEqual(row['order'], 3)
        self.assertEqual(row['entry'], 'fade')
        self.assertEqual(row['exit'], 'drop')
        self.assertEqual(row['delay_ms'], 500)
        self.assertEqual(row['duration_ms'], 8000)
        self.assertEqual(row['offset_x'], -40)
        self.assertEqual(row['offset_y'], 20)

    def test_words_and_media_sit_in_one_list_in_order(self):
        self.add(order=2)
        res = self.client.post(self.url, {'text': 'GRAND FINAL', 'order': 1},
                               format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])

        listed = self.client.get(self.url).data['data']['layers']
        self.assertEqual([r['kind'] for r in listed], ['text', 'asset'])

    # -------------------------------------------------------------- refusals

    def test_a_layer_pointing_at_nothing_is_refused(self):
        """An asset layer with no media draws nothing, like a caption with no words."""
        res = self.client.post(self.url, {'kind': 'asset'}, format='json')
        self.assertEqual(res.status_code, 400, res.content[:300])

    def test_media_belonging_to_something_else_is_refused(self):
        """Otherwise a number guessed in a box reaches another organiser's library."""
        theirs = StudioAsset.objects.create(
            event=self.event, kind='image', name='Not yours',
            file=SimpleUploadedFile('x.png', PIXEL, content_type='image/png'))
        res = self.client.post(
            self.url, {'kind': 'asset', 'asset_id': theirs.id}, format='json')
        self.assertEqual(res.status_code, 400, res.content[:300])
        self.assertEqual(res.data['code'], 'ASSET_NOT_YOURS')

    def test_media_that_does_not_exist_is_refused(self):
        res = self.client.post(
            self.url, {'kind': 'asset', 'asset_id': 9999999}, format='json')
        self.assertEqual(res.status_code, 400, res.content[:300])
        self.assertEqual(res.data['code'], 'ASSET_NOT_FOUND')

    def test_a_width_wider_than_the_frame_is_refused(self):
        res = self.add(width_px=4000)
        self.assertEqual(res.status_code, 400, res.content[:300])

    def test_a_kind_that_does_not_exist_is_refused(self):
        res = self.client.post(self.url, {'kind': 'hologram'}, format='json')
        self.assertEqual(res.status_code, 400, res.content[:300])

    # ------------------------------------------------------ the media going

    def test_deleting_the_media_leaves_the_layer_pointing_at_nothing(self):
        """And the page then draws nothing, rather than a broken image on air."""
        res = self.add()
        layer_id = res.data['data']['layers'][0]['id']
        self.asset.delete()

        row = OverlayLayer.objects.get(pk=layer_id)
        self.assertIsNone(row.asset_id)

        listed = self.client.get(self.url).data['data']['layers']
        self.assertEqual(listed[0]['asset_url'], '')

    # -------------------------------------------------- the other two owners

    def test_the_same_works_on_an_uploaded_overlay(self):
        overlay = self.upload()
        res = self.client.post(
            self.overlay_layers('tournament', overlay['id']),
            {'kind': 'asset', 'asset_id': self.asset.id, 'width_px': 200},
            format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.data['data']['layers'][0]['kind'], 'asset')

    def test_an_event_broadcast_can_do_it_too(self):
        """Built for one of the two is the fault the parity checker exists for."""
        session = self.start('event')
        theirs = StudioAsset.objects.create(
            event=self.event, kind='image', name='Event sponsor',
            file=SimpleUploadedFile('e.png', PIXEL, content_type='image/png'))
        res = self.client.post(
            self.element_layers('event', session['id'], 'now_next'),
            {'kind': 'asset', 'asset_id': theirs.id}, format='json')
        self.assertEqual(res.status_code, 200, res.content[:300])
