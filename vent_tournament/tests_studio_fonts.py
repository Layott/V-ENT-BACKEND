# -*- coding: utf-8 -*-
"""Fonts for an uploaded overlay, both ways.

CEO, 3 September 2026: "what of fonts when html files are uploaded, should
organizers be able to upload fonts in the studio also or should the fonts come
with the html file being uploaded also or both should be available."

Both, because they answer different problems:

  IN THE FILE      a base64 data URI. Always works, needs nothing from this
                   server, survives being opened anywhere, and is what the
                   standalone-HTML rule already requires. Needs no code, which
                   is exactly why it is the safe default.
  IN THE STUDIO    uploaded once, named by a slot, declared by the runtime as
                   a font-family. For the case the first one is bad at: a 400KB
                   font inlined into eight overlays is eight downloads, and an
                   organiser who wants to restyle should not need the designer
                   to re-export a file.

And the failure both share: a font that does not arrive substitutes SILENTLY.
The overlay goes on air in the wrong typeface with nothing to say so, which is
the same class as a missing image and more embarrassing.
"""

from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from vent_auth.models import Games, Users
from vent_tournament import overlay_binding
from vent_tournament.models import StudioAsset, Tournament


def a_user(name):
    user = Users.objects.create(
        username=name, email='%s@vent.test' % name, is_active=True,
        login_session_token=(name + 'f' * 16)[:16])
    return user, {'HTTP_AUTHORIZATION': 'Bearer ' + user.login_session_token}


@override_settings(FRONTEND_URL='https://v-ent.co')
class StudioFontTests(TestCase):

    def setUp(self):
        self.organiser, self.auth = a_user('font_org')
        self.stranger, self.stranger_auth = a_user('font_other')
        game = Games.objects.create(game_title='EA FC FONT')
        now = timezone.now()
        self.tournament = Tournament.objects.create(
            tournament_title='Font Cup', tournament_game=game,
            tournament_creator=self.organiser,
            start_date_and_time=now + timedelta(days=1),
            end_date_and_time=now + timedelta(days=2),
            tournament_visibility='public', tournament_type='online',
            prize_type='no_prize', tournament_access='team',
            entry_fee='Free', is_draft=False, bracket_type='round_robin')
        self.ref = self.tournament.slug or self.tournament.tournament_id
        self.url = '/tournament/%s/studio/assets/' % self.ref

    def upload(self, filename, slot='hero', content=b'a font'):
        return self.client.post(self.url, data={
            'file': SimpleUploadedFile(filename, content),
            'name': 'Headline face', 'slot': slot}, **self.auth)

    def feed(self):
        res = self.client.get('/tournament/%s/overlay-feed/' % self.ref)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']

    # ------------------------------------------------- uploading a font

    def test_a_font_can_be_uploaded_to_the_studio(self):
        res = self.upload('Head.woff2')
        self.assertEqual(res.status_code, 200, res.content[:300])
        self.assertEqual(res.json()['data']['added']['kind'], 'font')

    def test_every_font_format_a_client_might_send_is_taken(self):
        for i, name in enumerate(['A.woff2', 'B.woff', 'C.ttf', 'D.otf']):
            res = self.upload(name, slot='face%d' % i)
            self.assertEqual(res.status_code, 200,
                             '%s was refused: %s' % (name, res.content[:200]))

    def test_something_that_is_not_a_font_or_a_picture_is_still_refused(self):
        res = self.upload('notes.pdf')
        self.assertEqual(res.status_code, 400)

    def test_a_stranger_cannot_upload_a_font(self):
        res = self.client.post(self.url, data={
            'file': SimpleUploadedFile('Head.woff2', b'a font'),
            'name': 'Theirs', 'slot': 'hero'}, **self.stranger_auth)
        self.assertIn(res.status_code, (401, 403))

    # --------------------------------------------- reaching the overlay

    def test_the_feed_carries_the_font_under_its_slot(self):
        self.upload('Head.woff2', slot='hero')
        fonts = self.feed()['fonts']
        self.assertEqual(len(fonts), 1)
        self.assertEqual(fonts[0]['slot'], 'hero')
        self.assertTrue(fonts[0]['url'])

    def test_the_format_is_named_so_a_browser_will_use_it(self):
        """A `@font-face` with no format hint is a font a browser may skip."""
        wanted = {'Head.woff2': 'woff2', 'Head.woff': 'woff',
                  'Head.ttf': 'truetype', 'Head.otf': 'opentype'}
        for i, (filename, expected) in enumerate(wanted.items()):
            self.upload(filename, slot='face%d' % i)
        got = {f['slot']: f['format'] for f in self.feed()['fonts']}
        self.assertEqual(sorted(got.values()),
                         sorted(wanted.values()))

    def test_a_font_with_no_slot_is_not_offered(self):
        """A slot is what a designer writes. With none it cannot be named."""
        self.client.post(self.url, data={
            'file': SimpleUploadedFile('Nameless.woff2', b'a font'),
            'name': 'Nameless'}, **self.auth)
        self.assertEqual(self.feed()['fonts'], [])

    def test_the_newest_font_wins_a_slot(self):
        """Replacing the headline face at 8pm is an upload, not an edit."""
        self.upload('Old.woff2', slot='hero')
        self.upload('New.woff2', slot='hero')
        fonts = self.feed()['fonts']
        self.assertEqual(len(fonts), 1)
        newest = StudioAsset.objects.filter(kind='font').order_by('-created_at').first()
        self.assertTrue(fonts[0]['url'].endswith(newest.file.name.split('/')[-1]))

    def test_a_picture_is_not_offered_as_a_font(self):
        self.client.post(self.url, data={
            'file': SimpleUploadedFile('shot.png', b'a picture'),
            'name': 'A picture', 'slot': 'hero'}, **self.auth)
        self.assertEqual(self.feed()['fonts'], [])

    def test_a_tournament_with_no_fonts_says_so_rather_than_omitting_it(self):
        """An overlay reading `data.fonts` must not find undefined."""
        self.assertEqual(self.feed()['fonts'], [])

    def test_adding_a_font_moves_the_version(self):
        """Otherwise the overlay would never redraw and never pick it up."""
        before = self.feed()['version']
        self.upload('Head.woff2', slot='hero')
        self.assertNotEqual(self.feed()['version'], before)


class FontsInsideTheFileTests(TestCase):
    """The other half, which needs no code and must never be discouraged."""

    def test_a_font_pasted_into_the_file_is_not_a_problem(self):
        markup = ('@font-face{font-family:Head;'
                  'src:url(data:font/woff2;base64,AAAA) format("woff2");}')
        self.assertEqual(overlay_binding.font_problems(markup), [])

    def test_a_font_on_a_real_host_is_not_a_problem(self):
        markup = '@font-face{font-family:H;src:url(https://cdn.example/H.woff2);}'
        self.assertEqual(overlay_binding.font_problems(markup), [])
        self.assertEqual(overlay_binding.font_problems(
            '@font-face{src:url(//cdn.example/H.woff2);}'), [])

    def test_a_relative_path_is_reported_because_it_will_404(self):
        """There is no `fonts/` folder beside an uploaded overlay."""
        markup = '@font-face{font-family:Head;src:url(fonts/Head.woff2);}'
        self.assertEqual(overlay_binding.font_problems(markup),
                         ['fonts/Head.woff2'])

    def test_quoting_does_not_matter(self):
        for src in ('url("fonts/A.woff2")', "url('fonts/A.woff2')",
                    'url(fonts/A.woff2)'):
            markup = '@font-face{font-family:A;src:%s;}' % src
            self.assertEqual(overlay_binding.font_problems(markup),
                             ['fonts/A.woff2'], src)

    def test_a_url_outside_a_font_face_is_left_alone(self):
        """A background image is not a font, and is somebody else's problem."""
        markup = '.hero{background:url(pics/hero.png);}'
        self.assertEqual(overlay_binding.font_problems(markup), [])

    def test_a_file_with_no_fonts_at_all_is_quiet(self):
        self.assertEqual(overlay_binding.font_problems(
            '<div data-vent="team.name"></div>'), [])


class TheRuntimeDeclaresThemTests(TestCase):
    """The runtime is what turns a slot into a usable font-family."""

    def source(self):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, 'static', 'overlay-runtime.js'),
                  encoding='utf-8') as handle:
            return handle.read()

    def test_the_runtime_writes_a_font_face_per_slot(self):
        source = self.source()
        self.assertIn('@font-face', source)
        self.assertIn('writeFonts', source)

    def test_it_is_written_once_rather_than_every_poll(self):
        """A stylesheet rebuilt every four seconds re-evaluates the page."""
        self.assertIn('fontsWritten', self.source())

    def test_the_overlays_own_styles_still_win(self):
        """It is their design; this only supplies the file."""
        self.assertIn('insertBefore', self.source())


class ThePromptAnswersTheFontQuestionTests(TestCase):
    """CEO: "how will the site know what fonts to use and where inside the html
    files uploaded?"

    It does not, and that is the design: the uploaded file carries its own look
    and V-ENT only fills the values that are marked. Nothing restyles anybody's
    overlay.

    What the prompt has to do is tell the designer the two things that are NOT
    obvious: that a relative font path dies on upload, and that the studio's own
    fonts are already available by name.
    """

    def test_the_prompt_says_to_put_the_font_in_the_file(self):
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT as p
        self.assertIn('data:font/woff2;base64', p)
        self.assertIn('@font-face', p)

    def test_the_prompt_warns_that_a_relative_path_will_not_be_there(self):
        """The whole failure: it substitutes silently and goes on air wrong."""
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT as p
        self.assertIn('relative path', p)
        self.assertIn('SILENTLY', p)

    def test_the_prompt_covers_design_pictures_as_well_as_fonts(self):
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT as p
        self.assertIn('PICTURES that are part of the DESIGN', p)

    def test_the_prompt_still_says_to_change_nothing_about_the_design(self):
        """The answer to "will it match the design": V-ENT never restyles it."""
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT as p
        self.assertIn('change NOTHING else', p)
        self.assertIn('Keep every style', p)

    def test_both_prompts_carry_the_font_section(self):
        from vent_tournament.views_overlays import (
            DESIGNER_PROMPT_EVENT, DESIGNER_PROMPT_TOURNAMENT)
        for prompt in (DESIGNER_PROMPT_TOURNAMENT, DESIGNER_PROMPT_EVENT):
            self.assertIn('WHAT HAS TO TRAVEL INSIDE THE FILE', prompt)


@override_settings(FRONTEND_URL='https://v-ent.co')
class ThePromptNamesThisStudiosOwnFontsTests(StudioFontTests):
    """A prompt that describes a capability without naming what exists leaves
    the designer guessing. This one lists the actual slots."""

    def prompt(self):
        res = self.client.get('/tournament/%s/overlays/' % self.ref, **self.auth)
        self.assertEqual(res.status_code, 200, res.content[:300])
        return res.json()['data']['prompt']

    def test_an_empty_studio_says_so_rather_than_listing_nothing(self):
        self.assertIn('NO UPLOADED FONTS OR PICTURES YET', self.prompt())

    def test_an_uploaded_font_is_named_as_a_usable_family(self):
        self.upload('Head.woff2', slot='hero')
        prompt = self.prompt()
        self.assertIn('WHAT THIS STUDIO ALREADY HOLDS', prompt)
        self.assertIn("font-family: 'hero';", prompt)

    def test_an_uploaded_picture_is_named_as_a_usable_source(self):
        self.client.post(self.url, data={
            'file': SimpleUploadedFile('crowd.png', b'a picture'),
            'name': 'Crowd', 'slot': 'crowd'}, **self.auth)
        self.assertIn('data-vent-src="asset.crowd"', self.prompt())

    def test_a_font_with_no_slot_is_not_offered_in_the_prompt(self):
        """It cannot be named, so telling a designer about it would be a lie."""
        self.client.post(self.url, data={
            'file': SimpleUploadedFile('Nameless.woff2', b'a font'),
            'name': 'Nameless'}, **self.auth)
        self.assertIn('NO UPLOADED FONTS OR PICTURES YET', self.prompt())


class TheAnimatedAndStillQuestionTests(TestCase):
    """CEO: "remember some html files will come animated and some will be still
    for images."

    Both are ordinary. The prompt has to say so, because a tool converting a
    design will otherwise either strip the motion out of an animated one or
    invent motion for a still one, and both are wrong.
    """

    def test_the_prompt_says_both_are_fine(self):
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT as p
        self.assertIn('ANIMATED OR STILL, BOTH ARE FINE', p)

    def test_it_says_not_to_add_motion_to_a_still_design(self):
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT as p
        self.assertIn('Do not add an animation to a still design', p)

    def test_it_says_to_leave_an_existing_animation_alone(self):
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT as p
        self.assertIn('leave the animation exactly as it is', p)

    def test_it_warns_about_infinite_without_banning_it(self):
        """A ticker legitimately never stops. This is advice, not a rule."""
        from vent_tournament.views_overlays import DESIGNER_PROMPT_TOURNAMENT as p
        self.assertIn('infinite', p)
        self.assertIn('like a ticker', p)

    def test_both_prompts_carry_it(self):
        from vent_tournament.views_overlays import (
            DESIGNER_PROMPT_EVENT, DESIGNER_PROMPT_TOURNAMENT)
        for prompt in (DESIGNER_PROMPT_TOURNAMENT, DESIGNER_PROMPT_EVENT):
            self.assertIn('ANIMATED OR STILL', prompt)
