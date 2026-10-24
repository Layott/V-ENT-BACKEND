"""Every format the bracket engine draws is drawn and played to one champion.

CEO, 23 October 2026: "i need the brackets and formats fully functional before
tomorrow". Each drawable format is drawn at the sizes its catalogue entry accepts,
every match is played with the better seed winning, and the stage is checked: no
match is left open with both sides filled, the stage reads finished, and exactly
one entrant finishes first. Formats scored as a table of points or as a streak
are left out of the bracket checks; see the comment on `BRACKET_FORMATS`.
"""
from django.test import TestCase

from . import bracket_presets, stage_engine
from .tests_p3_schema import preset_stage
from .tests_stage_engine import add_stage, draw, field, play_out


# (format, sizes it accepts, groups per size). Each size is the catalogue minimum
# or a field that shows the format at work; page_playoff takes exactly four.
#
# Left out on purpose, because they are scored as tables or lobbies rather than
# drawn as a bracket of matches:
#   battle_royale  lobbies scored on placement points and kills, not head to head
#   aggregate_2v2  per-player fixtures decided on total goals across a tie
#   ladder         a points table played over weeks, with no bracket to finish
#   challenge_ladder  every match made by a challenge, open until the stage ends
#                  (tests_p3_challenge_ladder)
# Sizes with byes are in on purpose (6 and 12 in a knockout, odd counts where
# the format takes them): a bye with nobody in it is a dead match, and a dead
# match that never told the match it feeds is how a compass draw of 12 stalled.
BRACKET_FORMATS = (
    ('single_elimination', (4, 6, 8, 12), 0),
    ('double_elimination', (4, 6, 8, 12), 0),
    ('round_robin', (4, 5, 8), 0),
    ('league', (4, 5, 8), 0),
    ('swiss', (4, 5, 8), 0),
    # P3 W3a: four pots of four, two opponents from each.
    ('league_phase', (16,), 0),
    ('gsl', (8, 16), 0),
    # Groups of four, so 8 is two groups and 16 is four.
    ('bracket_groups', (8, 16), 'groups_of_four'),
    ('stepladder', (4, 5, 8), 0),
    ('page_playoff', (4,), 0),
    ('winner_stays_on', (4, 5, 8), 0),
)


class EveryFormatFinishesTests(TestCase):
    def assert_champion(self, stage):
        open_ = stage.matches.filter(status='scheduled', participant_1__isnull=False,
                                     participant_2__isnull=False)
        self.assertFalse(open_.exists(), 'a match is still open with both sides filled')
        left = [(m.round_number, m.match_number, m.status, m.participant_1_id, m.participant_2_id)
                for m in stage.matches.exclude(status__in=stage_engine.TERMINAL)]
        self.assertTrue(stage_engine.stage_finished(stage),
                        'the stage does not read finished; open %s' % left)
        # A group stage has a first in every group, so one first per group.
        firsts = {}
        for r in stage_engine.standings(stage):
            if r['rank'] == 1:
                firsts.setdefault(r.get('group'), []).append(r['seed'])
        self.assertTrue(firsts, 'nobody finishes first')
        for group, seeds in firsts.items():
            self.assertEqual(len(seeds), 1, 'group %s: first place is shared by seeds %s'
                             % (group, seeds))

    def test_every_bracket_format_at_every_size_it_accepts(self):
        for fmt, sizes, groups in BRACKET_FORMATS:
            for n in sizes:
                with self.subTest(fmt=fmt, n=n):
                    t, creator, _regs = field(n)
                    stage = add_stage(t, 0, fmt, groups=n // 4 if groups else 0)
                    draw(stage, creator)
                    stage.refresh_from_db()
                    play_out(stage)
                    stage.refresh_from_db()
                    self.assert_champion(stage)

    def test_every_custom_bracket_preset_at_every_size_it_accepts(self):
        for preset, build in bracket_presets.PRESETS.items():
            for n in bracket_presets.SIZES[preset]:
                with self.subTest(fmt='custom_bracket', preset=preset, n=n):
                    stage, _regs = preset_stage(n, preset)
                    play_out(stage)
                    stage.refresh_from_db()
                    self.assert_champion(stage)
