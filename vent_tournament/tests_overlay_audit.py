# -*- coding: utf-8 -*-
"""What an uploaded overlay has to get right, and what nothing else would catch.

CEO, 4 September 2026: "with this issue for the fonts, please check the rest of
the other things that are absolutely needed for the site to get what it wants
from the generated html file of the video or the image. remember some html files
will come animated and some will be still for images."

The fonts question was one instance of a class: things a file gets wrong that
NOTHING ERRORS ON. Every case below is discovered on air unless it is caught
here, because an overlay is not run in front of anybody until it is live.
"""

from django.test import TestCase

from vent_tournament import overlay_audit


def only(markup):
    """The problems found, so a test can assert on the count and the words."""
    return overlay_audit.problems(markup)


class FontsTests(TestCase):
    def test_a_relative_font_is_reported(self):
        found = only('@font-face{font-family:H;src:url(fonts/H.woff2);}')
        self.assertEqual(len(found), 1)
        self.assertIn('wrong typeface', found[0])

    def test_a_font_inside_the_file_is_fine(self):
        self.assertEqual(
            only('@font-face{font-family:H;src:url(data:font/woff2;base64,AA);}'),
            [])

    def test_a_font_on_a_real_host_is_fine(self):
        self.assertEqual(
            only('@font-face{font-family:H;src:url(https://cdn.x/H.woff2);}'), [])


class PicturesTests(TestCase):
    def test_a_relative_picture_is_reported(self):
        found = only('<img src="pics/crowd.png">')
        self.assertEqual(len(found), 1)
        self.assertIn('no folder beside an uploaded overlay', found[0])

    def test_a_relative_css_background_is_reported(self):
        found = only('.hero{background:url(pics/hero.png);}')
        self.assertEqual(len(found), 1)

    def test_a_picture_inside_the_file_is_fine(self):
        self.assertEqual(only('<img src="data:image/png;base64,AA">'), [])

    def test_a_picture_the_tournament_fills_is_fine(self):
        """Its placeholder never has to resolve: the runtime replaces the src."""
        self.assertEqual(
            only('<img src="pics/x.png" data-vent-src="team.logo">'), [])

    def test_the_same_missing_picture_is_only_mentioned_once(self):
        """Ten uses of one file is one thing to fix, not ten."""
        markup = '<img src="pics/a.png"><img src="pics/a.png"><img src="pics/a.png">'
        self.assertEqual(len(only(markup)), 1)

    def test_an_anchor_is_not_a_picture(self):
        self.assertEqual(only('<a href="#top">top</a>'), [])


class BackgroundTests(TestCase):
    """The worst one: it does not look broken, it looks like the stream died."""

    def test_an_opaque_body_background_is_reported(self):
        found = only('body{background:#000;width:1920px;}')
        self.assertEqual(len(found), 1)
        self.assertIn('covers the stream', found[0])

    def test_a_white_body_is_reported_too(self):
        self.assertEqual(len(only('body{background-color:#ffffff;width:1920px;}')), 1)

    def test_transparent_is_the_point_and_is_fine(self):
        self.assertEqual(only('body{background:transparent;width:1920px;}'), [])

    def test_a_zero_alpha_is_transparent(self):
        self.assertEqual(only('body{background:rgba(0,0,0,0);width:1920px;}'), [])

    def test_no_background_at_all_is_fine(self):
        self.assertEqual(only('body{width:1920px;height:1080px;}'), [])

    def test_a_background_on_an_inner_element_is_fine(self):
        """The design is allowed a panel. It is the PAGE that must stay clear."""
        self.assertEqual(
            only('.panel{background:#111;}body{width:1920px;}'), [])

    def test_html_counts_as_the_page_too(self):
        self.assertEqual(len(only('html{background:#000;width:1920px;}')), 1)


class StageSizeTests(TestCase):
    def test_a_stage_that_is_not_a_browser_source_is_reported(self):
        found = only('.stage{width:800px;height:600px;}')
        self.assertEqual(len(found), 1)
        self.assertIn('1920x1080', found[0])

    def test_the_right_size_is_quiet(self):
        self.assertEqual(only('.stage{width:1920px;height:1080px;}'), [])

    def test_either_dimension_alone_is_enough_to_be_quiet(self):
        self.assertEqual(only('.stage{height:1080px;}'), [])

    def test_the_warning_says_a_vertical_stream_is_fine(self):
        """An organiser knows things this does not. It tells, never refuses."""
        found = only('.stage{width:1080px;}')
        self.assertEqual(found, [], 'a vertical stage carries 1080 and is fine')

    def test_a_file_with_no_pixel_sizes_is_quiet(self):
        self.assertEqual(only('.stage{width:100%;height:100%;}'), [])


class AnimationTests(TestCase):
    """The CEO's own point: some files arrive animated and some are still."""

    def test_something_that_animates_for_ever_is_mentioned(self):
        found = only('.dot{animation:pulse 2s infinite;width:1920px;}')
        self.assertEqual(len(found), 1)
        self.assertIn('animates for ever', found[0])

    def test_a_one_shot_animation_is_exactly_what_an_animated_overlay_is(self):
        """An animated file is the normal case, not a problem to be warned at."""
        self.assertEqual(
            only('.in{animation:rise 400ms ease-out;width:1920px;}'), [])

    def test_a_still_file_is_equally_fine(self):
        self.assertEqual(only('<div class="card" style="width:1920px">x</div>'), [])

    def test_the_endless_warning_says_a_ticker_is_legitimate(self):
        found = only('.ticker{animation:scroll 20s linear infinite;width:1920px;}')
        self.assertIn('Fine for a ticker', found[0])


class TogetherTests(TestCase):
    def test_a_file_with_several_faults_reports_all_of_them(self):
        markup = ('@font-face{font-family:H;src:url(fonts/H.woff2);}'
                  'body{background:#000;}'
                  '.stage{width:800px;}'
                  '<img src="pics/a.png">')
        found = only(markup)
        self.assertEqual(len(found), 4, found)

    def test_a_clean_file_says_nothing(self):
        markup = ('@font-face{font-family:H;src:url(data:font/woff2;base64,AA);}'
                  'body{background:transparent;}'
                  '.stage{width:1920px;height:1080px;}'
                  '<img src="data:image/png;base64,AA">'
                  '<div data-vent="team.name">Team</div>')
        self.assertEqual(only(markup), [])

    def test_bytes_are_accepted_as_well_as_text(self):
        """An upload arrives as bytes, and this must not care."""
        self.assertEqual(len(only(b'body{background:#000;width:1920px;}')), 1)


class ItReachesTheUploaderTests(TestCase):
    """A check nobody is shown is a check that does not exist."""

    def test_the_upload_path_runs_the_audit(self):
        import inspect
        from vent_tournament import views_overlays
        source = inspect.getsource(views_overlays._create_overlay)
        self.assertIn('overlay_audit.problems', source)
        self.assertIn('warnings.extend', source)
