"""How a match is played, and where each rule comes from (P1c).

CEO, 16 October 2026: "i aprove the design". Section 10 of the bracket
structure design: "Best of, legs, draws, report mode, check-in window and timers
are set on the tournament and overridden on a stage, group, round or match. The
nearest setting wins and the screen says where a value comes from. One resolver
function answers it for every reader."

The values a match is played under are fixed on it when it is drawn (so a rule
changed later cannot rewrite a match in progress). This says where each one
came from, nearest first:

  * set for this match, or for its whole round, after the draw
    (`BracketMatch.settings`, written by `bracket_edit.set_rules`);
  * the round, through the stage's own per round store (`round_best_of`, and
    `final_best_of` for the final), which is the store the planner writes too;
  * the stage;
  * the tournament, for a tournament run as one format.

Groups get their own level in P4, with the first group field that has a writer.
"""

KEYS = ('best_of', 'legs', 'draw_allowed', 'check_in_minutes')


def _settings_of(match):
    from . import stage_settings
    if match.stage_id:
        return match.stage.settings or stage_settings.clean(match.stage.format, {})
    return stage_settings.for_tournament(match.tournament)


def rules_of(match):
    """{key: {'value', 'from', 'stage'}} for every rule a match is played under."""
    own = match.settings or {}
    stage = match.stage if match.stage_id else None
    settings = _settings_of(match)
    label = stage.label if stage else None
    level = 'stage' if stage else 'tournament'
    values = {
        'best_of': match.best_of,
        'legs': match.legs,
        'draw_allowed': match.draw_allowed,
        'check_in_minutes': int(settings.get('check_in_minutes') or 0),
    }
    out = {}
    for key in KEYS:
        source = own.get(key) if own.get(key) in ('match', 'round') else None
        if source is None and key == 'best_of' and stage is not None:
            per_round = settings.get('round_best_of') or {}
            set_for_round = per_round.get(str(match.round_number))
            if set_for_round is not None and int(set_for_round) == match.best_of:
                source = 'round'
            elif match.is_final and settings.get('final_best_of') \
                    and int(settings['final_best_of']) == match.best_of:
                source = 'round'
        out[key] = {'value': values[key], 'from': source or level, 'stage': label}
    return out
