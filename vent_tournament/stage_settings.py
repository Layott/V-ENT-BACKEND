"""What a stage's settings may be, and the one place they are decided.

A stage is a format plus how its matches are played. Both halves used to live
in places nothing read: the tournament options carried `best_of_mode`,
`best_of_final` and `group_stage`, the wizard saved them, and no bracket ever
looked at them. So the shape is decided here, once, and the generators, the
result rules and the screens all read the cleaned form.

Every refusal carries a code and the field it is about, never a sentence built
here, because a sentence built in Python cannot be translated.

Where the defaults come from (researched 27 September 2026):

  * EA FC Pro Mobile 2026: groups are round robin, best of two, draws count, no
    extra time; the playoff is best of five, best of seven in the final, extra
    time and penalties on. A no-show loses 0-3.
  * eFootball mobile community rules: 6 minute matches, extra time and
    penalties on in a knockout, one side hosts a Friend Match room and shares
    its id and password.
  * toornament: single elimination has a third-place decider; double
    elimination's grand final is single or with a reset.
  * Swiss stopping a side at a number of wins or losses is V-ENT's own, after
    the Counter-Strike Major Swiss. Toornament documents no such limit
    (docs/research/toornament-2026-10-16.md, section 18.4); an earlier version
    of this note said it did.
"""
from . import formats


class SettingsError(ValueError):
    def __init__(self, code, field, detail=None):
        super().__init__(code)
        self.code = code
        self.field = field
        # Where in a written schema the fault is: {'match': 'C'} or {'line': 3}.
        self.detail = detail or {}


# `none`: the upper and the lower bracket winners never meet and both go
# through (toornament's grand final "none", research section 4.3; P3 W1).
GRAND_FINALS = ('single', 'reset', 'none')
DRAW_RULES = ('allowed', 'penalties', 'winner_named')
PAIRINGS = ('circle', 'berger')
SWISS_PAIRINGS = ('monrad', 'dutch', 'valve')
BEST_OF_RANGE = (1, 9)


def _threshold(raw):
    """How many a knockout stops at, 0 for a knockout played to one winner.

    Toornament's threshold (research section 4.2): "removes the matches that
    would otherwise decide between the N best participants", and only a value
    that removes whole rounds is valid, so it is a power of two. Whether the
    bracket is big enough for it is known only at the draw, which refuses a
    threshold too big with THRESHOLD_TOO_BIG.
    """
    value = _int(raw, 'threshold', 0, 64, 0)
    if value <= 1:
        return 0
    if value & (value - 1):
        raise SettingsError('NOT_A_POWER_OF_TWO', 'threshold')
    return value


# What a knockout that stops at a threshold can send its qualifiers into:
# whatever a group or a Swiss stage feeds (P3 W1). Played to one winner, a
# knockout decides the tournament and feeds nothing.
QUALIFIER_FEEDS = ('single_elimination', 'double_elimination', 'round_robin', 'league',
                   'swiss', 'stepladder', 'page_playoff', 'gsl', 'bracket_groups',
                   'league_phase')


def feeds_into(format_key, settings=None):
    """The formats a stage of `format_key`, set up as `settings`, may send
    people into. A format's own list, except that a single or double
    elimination with a threshold has qualifiers to send (walk of P3, 17
    October 2026: its line was refused PROGRESSION_FORMAT_CANNOT_FOLLOW)."""
    fmt = formats.get(format_key)
    if fmt is None:
        return ()
    if fmt.key in ('single_elimination', 'double_elimination') and threshold_of(settings):
        return QUALIFIER_FEEDS
    return tuple(fmt.can_feed_into or ())


def threshold_of(settings):
    """How many a knockout stage sends on without playing for places between
    them: its threshold, 2 for a double elimination with no grand final, or 0
    when it is played to one winner."""
    settings = settings or {}
    value = int(settings.get('threshold') or 0)
    if value >= 2:
        return value
    if settings.get('grand_final') == 'none':
        return 2
    return 0


def _int(raw, field, low, high, default):
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise SettingsError('NOT_A_NUMBER', field)
    if value < low or value > high:
        raise SettingsError('OUT_OF_RANGE', field)
    return value


def _bool(raw, default):
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(raw)


def _num(raw, field, low, high, default):
    """A number that may carry decimals: points for 1000 damage is 0.5."""
    if raw in (None, ''):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SettingsError('NOT_A_NUMBER', field)
    if value != value or value < low or value > high:
        raise SettingsError('OUT_OF_RANGE', field)
    return int(value) if value == int(value) else round(value, 2)


# ---------------------------------------------------------------------------
# Battle royale
# ---------------------------------------------------------------------------
# AFC's scoring, read on 28 September 2026 (`afc_tournament_and_scrims/
# scoring.py`) and written again here rather than copied: placement points from
# the organiser's table, plus points a kill, plus optional points an assist and
# a thousand damage, plus a bonus, minus a penalty. The lobby size is the
# organiser's to choose (CEO: "you don't hardcode what number of squads can be
# in a lobby"); the game only suggests a starting number.

BR_PLACEMENT_PRESETS = {
    'free_fire': {1: 12, 2: 9, 3: 8, 4: 7, 5: 6, 6: 5, 7: 4, 8: 3, 9: 2, 10: 1},
    'pubg_mobile': {1: 10, 2: 6, 3: 5, 4: 4, 5: 3, 6: 2, 7: 1, 8: 1},
}

# The tiebreakers a battle royale table can be ordered by. `placement_count` is
# booyahs, `total_kills` is kills: the catalogue's names, so the rules screen
# and this stage use one vocabulary.
BR_TIEBREAKERS = ('placement_count', 'total_kills', 'last_map_placement',
                  'placement_points', 'kill_points', 'best_placement',
                  'mvp_count', 'bonus', 'fewest_penalties', 'maps_played')
BR_DEFAULT_TIEBREAKERS = ['placement_count', 'total_kills', 'last_map_placement']

BR_MVP_CRITERIA = ('kills', 'damage', 'assists')
BR_MVP_SCOPES = ('overall', 'winning_team')
BR_ADVANCE_BY = ('overall', 'lobby')

# Where a game starts, when the organiser has said nothing. Only a start.
BR_GAME_DEFAULTS = (
    ('free fire', {'placement_preset': 'free_fire', 'lobby_size': 12}),
    ('pubg', {'placement_preset': 'pubg_mobile', 'lobby_size': 16}),
)


def br_game_defaults(game_title):
    needle = str(game_title or '').strip().lower()
    for key, defaults in BR_GAME_DEFAULTS:
        if key in needle:
            return dict(defaults)
    return {'placement_preset': 'free_fire', 'lobby_size': 12}


def _placement_table(raw, preset):
    """{"1": 12, "2": 9, ...} from a table the organiser typed, or a preset."""
    if raw in (None, '', {}):
        raw = BR_PLACEMENT_PRESETS.get(preset) or BR_PLACEMENT_PRESETS['free_fire']
    if not isinstance(raw, dict):
        raise SettingsError('NOT_A_MAP', 'placement_points')
    out = {}
    for place, points in raw.items():
        place_no = _int(place, 'placement_points', 1, 100, None)
        out[str(place_no)] = _num(points, 'placement_points', 0, 1000, 0)
    if not out:
        raise SettingsError('EMPTY_TABLE', 'placement_points')
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def _ordered_choice(raw, allowed, field, default):
    if raw in (None, ''):
        return list(default)
    if not isinstance(raw, list):
        raise SettingsError('NOT_A_LIST', field)
    out = []
    for item in raw:
        key = str(item or '').strip().lower()
        if key not in allowed:
            raise SettingsError('UNKNOWN_CHOICE', field)
        if key in out:
            raise SettingsError('DUPLICATE', field)
        out.append(key)
    return out


def clean_battle_royale(raw):
    raw = raw if isinstance(raw, dict) else {}
    preset = str(raw.get('placement_preset') or 'free_fire').strip().lower()
    if preset not in BR_PLACEMENT_PRESETS and preset != 'custom':
        raise SettingsError('UNKNOWN_CHOICE', 'placement_preset')
    table_raw = raw.get('placement_points') if preset == 'custom' else None
    out = {
        # How many squads drop together. The organiser's number, any number a
        # game can hold.
        'lobby_size': _int(raw.get('lobby_size'), 'lobby_size', 2, 100, 12),
        # How many matches each lobby plays. More can be added on the day.
        'maps': _int(raw.get('maps'), 'maps', 1, 30, 6),
        'placement_preset': preset,
        'placement_points': _placement_table(table_raw, preset),
        'per_kill': _num(raw.get('per_kill'), 'per_kill', 0, 100, 1),
        'per_assist': _num(raw.get('per_assist'), 'per_assist', 0, 100, 0),
        'per_1000_damage': _num(raw.get('per_1000_damage'), 'per_1000_damage',
                                0, 100, 0),
        'tiebreakers': _ordered_choice(raw.get('tiebreakers'), BR_TIEBREAKERS,
                                       'tiebreakers', BR_DEFAULT_TIEBREAKERS),
        'mvp_criteria': _ordered_choice(raw.get('mvp_criteria'), BR_MVP_CRITERIA,
                                        'mvp_criteria', BR_MVP_CRITERIA),
        'mvp_scope': str(raw.get('mvp_scope') or 'overall').strip().lower(),
        # Match point: once a squad is at or over this total, the first match
        # it wins takes the stage. 0 is off.
        'match_point': _int(raw.get('match_point'), 'match_point', 0, 10000, 0),
        'advance_by': str(raw.get('advance_by') or 'overall').strip().lower(),
        # A head start for the next stage by finishing place (AFC's Point
        # Rush): {"1": 10, "2": 6}. Read per lobby or overall, as advance_by.
        'carry_over': {},
        'check_in_minutes': _int(raw.get('check_in_minutes'), 'check_in_minutes',
                                 0, 120, 0),
        'room_settings': str(raw.get('room_settings') or '').strip()[:400],
    }
    if out['mvp_scope'] not in BR_MVP_SCOPES:
        raise SettingsError('UNKNOWN_CHOICE', 'mvp_scope')
    if out['advance_by'] not in BR_ADVANCE_BY:
        raise SettingsError('UNKNOWN_CHOICE', 'advance_by')
    carry = raw.get('carry_over') or {}
    if not isinstance(carry, dict):
        raise SettingsError('NOT_A_MAP', 'carry_over')
    for place, points in carry.items():
        place_no = _int(place, 'carry_over', 1, 100, None)
        value = _num(points, 'carry_over', 0, 1000, 0)
        if value:
            out['carry_over'][str(place_no)] = value
    out['carry_over'] = dict(sorted(out['carry_over'].items(), key=lambda kv: int(kv[0])))
    return out


def _clean_custom(raw):
    """A custom bracket (P3 W2): a preset by name, or a schema the organiser
    wrote in the line language, read and checked here so a plan that cannot be
    drawn is refused when it is saved, with the match or line it is about."""
    from . import bracket_presets, bracket_schema
    preset = str(raw.get('preset') or '').strip().lower()
    if preset and preset not in bracket_presets.PRESETS:
        raise SettingsError('UNKNOWN_CHOICE', 'preset')
    text = str(raw.get('schema_text') or '')[:20000]
    if preset:
        return {'preset': preset, 'schema_text': '', 'schema': None}
    try:
        schema = bracket_schema.parse(text) if text.strip() else raw.get('schema') or {'matches': []}
        bracket_schema.check(schema)
    except bracket_schema.SchemaError as exc:
        detail = {k: v for k, v in (('match', exc.match), ('line', exc.line)) if v is not None}
        raise SettingsError(exc.code, 'schema', detail)
    return {'preset': '', 'schema_text': text, 'schema': schema}


def is_table(format_key):
    fmt = formats.get(format_key)
    return bool(fmt and fmt.advancement in ('table', 'swiss'))


def clean(format_key, raw):
    """The stored settings for one stage of `format_key`.

    Unknown keys are dropped rather than refused: a screen that sends one
    extra field should not lose the organiser's whole plan over it.
    """
    raw = raw if isinstance(raw, dict) else {}
    fmt = formats.get(format_key)
    key = fmt.key if fmt else str(format_key or '')
    table = is_table(key)

    if key == 'battle_royale':
        return clean_battle_royale(raw)

    out = {}
    if key == 'custom_bracket':
        out.update(_clean_custom(raw))

    # ---- the format's own switches ---------------------------------------
    if key == 'single_elimination':
        out['third_place'] = _bool(raw.get('third_place'), False)
        # Losers play on for fifth, seventh and every place below, so every
        # entrant finishes somewhere distinct. Supersedes the third-place match,
        # which is the smallest case of it.
        out['every_place'] = _bool(raw.get('every_place'), False)
        if out['every_place']:
            out['third_place'] = False
        # Stop when this many are left (P3 W1). The places between them are
        # not played, so neither is a match for third or for every place.
        out['threshold'] = _threshold(raw.get('threshold'))
        if out['threshold']:
            out['third_place'] = False
            out['every_place'] = False
        # Pair again by seed each round, the best remaining against the worst
        # remaining, as the NFL does (research catalogue 2.1). A bracket that
        # plays for every place has fixed lines for each place, so it cannot.
        out['reseed'] = _bool(raw.get('reseed'), False) and not out['every_place']
    if key == 'winner_stays_on':
        # 0: one pass, every challenger gets one go at whoever holds the spot.
        # Above 0: a loser goes to the back of the queue and it runs until
        # somebody wins that many in a row (or `max_matches` is reached).
        out['streak_target'] = _int(raw.get('streak_target'), 'streak_target', 0, 20, 0)
        out['max_matches'] = _int(raw.get('max_matches'), 'max_matches', 0, 200, 0)
    if key == 'double_elimination':
        gf = str(raw.get('grand_final') or 'reset').strip().lower()
        if gf not in GRAND_FINALS:
            raise SettingsError('UNKNOWN_CHOICE', 'grand_final')
        out['threshold'] = _threshold(raw.get('threshold'))
        # A threshold stops before the brackets meet, so there is no final.
        out['grand_final'] = 'none' if out['threshold'] else gf
    if key in ('round_robin', 'league', 'ladder', 'aggregate_2v2'):
        # Home and away: everybody plays everybody twice. A League is played
        # that way unless the organiser says once (inbox 440); 0 or nothing
        # sent means "the format's own answer".
        default_legs = 2 if key == 'league' else 1
        out['legs'] = _int(raw.get('legs') or None, 'legs', 1, 2, default_legs)
        # The order of the fixtures: the rotation V-ENT has always drawn, or
        # FIDE's published Berger tables (P3 W1, research catalogue 3.1).
        out['pairing'] = str(raw.get('pairing') or 'circle').strip().lower()
        if out['pairing'] not in PAIRINGS:
            raise SettingsError('UNKNOWN_CHOICE', 'pairing')
    if key == 'challenge_ladder':
        # How far up a challenge may reach, and whether the places are drawn
        # as a ladder or a pyramid (P3 W3d).
        out['reach'] = _int(raw.get('reach'), 'reach', 1, 10, 3)
        out['shape'] = str(raw.get('shape') or 'ladder').strip().lower()
        if out['shape'] not in ('ladder', 'pyramid'):
            raise SettingsError('UNKNOWN_CHOICE', 'shape')
    if key == 'league_phase':
        # The pots the field is dealt into by seed, and how many opponents
        # everybody meets from each (P3 W3a). Played once, never home and away.
        out['pots'] = _int(raw.get('pots'), 'pots', 2, 8, 4)
        out['per_pot'] = _int(raw.get('per_pot'), 'per_pot', 1, 6, 2)
        out['legs'] = 1
    if key == 'bracket_groups':
        # Each group a knockout of its own (P3 W1). The default is the GSL
        # shape: double elimination, two through, groups of four.
        out['bracket'] = str(raw.get('bracket') or 'double').strip().lower()
        if out['bracket'] not in ('single', 'double'):
            raise SettingsError('UNKNOWN_CHOICE', 'bracket')
        # How big a group is when the stage does not say how many groups.
        out['group_size'] = _int(raw.get('group_size'), 'group_size', 2, 64, 4)
        sent = raw.get('threshold')
        out['threshold'] = _threshold(2 if sent in (None, '') else sent)
        if out['bracket'] == 'double':
            gf = str(raw.get('grand_final') or 'single').strip().lower()
            if gf not in GRAND_FINALS:
                raise SettingsError('UNKNOWN_CHOICE', 'grand_final')
            out['grand_final'] = 'none' if out['threshold'] else gf
    if key == 'swiss':
        out['rounds'] = _int(raw.get('rounds'), 'rounds', 0, 15, 0)
        out['win_target'] = _int(raw.get('win_target'), 'win_target', 0, 10, 0)
        out['loss_limit'] = _int(raw.get('loss_limit'), 'loss_limit', 0, 10, 0)
        # How rounds after the first are paired (P3 W1): monrad is what V-ENT
        # has always drawn, dutch is FIDE's, valve the Counter-Strike Major's.
        out['pairing'] = str(raw.get('pairing') or 'monrad').strip().lower()
        if out['pairing'] not in SWISS_PAIRINGS:
            raise SettingsError('UNKNOWN_CHOICE', 'pairing')
        # A match that can send a side through or out is played over this
        # many games (0: like any other).
        out['decisive_best_of'] = _int(raw.get('decisive_best_of'), 'decisive_best_of',
                                       0, BEST_OF_RANGE[1], 0)

    # ---- how a match is played -------------------------------------------
    out['best_of'] = _int(raw.get('best_of'), 'best_of', *BEST_OF_RANGE, 1)
    out['final_best_of'] = _int(raw.get('final_best_of'), 'final_best_of',
                                0, BEST_OF_RANGE[1], 0)
    per_round = raw.get('round_best_of') or {}
    if not isinstance(per_round, dict):
        raise SettingsError('NOT_A_MAP', 'round_best_of')
    cleaned_rounds = {}
    for rnd, value in per_round.items():
        rnd_no = _int(rnd, 'round_best_of', 1, 64, None)
        cleaned_rounds[str(rnd_no)] = _int(value, 'round_best_of',
                                           *BEST_OF_RANGE, 1)
    out['round_best_of'] = cleaned_rounds

    # A knockout match played over two legs on aggregate. Not offered on a
    # table: there, home and away is `legs` above, two separate fixtures.
    if not table:
        out['knockout_legs'] = _int(raw.get('knockout_legs'), 'knockout_legs',
                                    1, 2, 1)

    # What happens when a match ends level. A table takes the draw; a knockout
    # needs a decider, and in football that is penalties.
    default_draws = 'allowed' if table else 'penalties'
    draws = str(raw.get('draws') or default_draws).strip().lower()
    if draws not in DRAW_RULES:
        raise SettingsError('UNKNOWN_CHOICE', 'draws')
    if draws == 'allowed' and not table:
        raise SettingsError('KNOCKOUT_NEEDS_A_WINNER', 'draws')
    out['draws'] = draws

    # ---- match day -------------------------------------------------------
    out['check_in_minutes'] = _int(raw.get('check_in_minutes'),
                                   'check_in_minutes', 0, 120, 0)
    out['room_host'] = str(raw.get('room_host') or 'p1').strip().lower()
    if out['room_host'] not in ('p1', 'p2', 'organiser'):
        raise SettingsError('UNKNOWN_CHOICE', 'room_host')
    # What the players set up in the game before they start. Free text the
    # organiser writes once ("6 minutes, extra time and penalties on,
    # authentic teams"), shown on every match.
    out['room_settings'] = str(raw.get('room_settings') or '').strip()[:400]

    return out


def best_of_for(settings, round_number, rounds_total, is_final=False):
    """How many games this round is played over."""
    settings = settings or {}
    per_round = settings.get('round_best_of') or {}
    if str(round_number) in per_round:
        return int(per_round[str(round_number)])
    if is_final and settings.get('final_best_of'):
        return int(settings['final_best_of'])
    return int(settings.get('best_of') or 1)


def for_tournament(tournament):
    """Match settings for a tournament that runs as one format.

    Built from the options the wizard has always saved and nothing read:
    `best_of`, `best_of_mode`, `best_of_final`, `third_place_match`. So a
    tournament created before stages existed gets the format it asked for.
    """
    from . import options as tournament_options
    from .services.bracket import normalize_bracket_type

    opts = tournament_options.clean(getattr(tournament, 'options', None))
    key = normalize_bracket_type(tournament.bracket_type)
    raw = {
        'best_of': opts.get('best_of') or 1,
        'third_place': opts.get('third_place_match'),
        # A single-format double elimination keeps the decisive grand final it
        # has always had; a reset is chosen on a stage, where it is offered.
        'grand_final': opts.get('grand_final') or 'single',
        'every_place': opts.get('every_place'),
        'streak_target': opts.get('streak_target'),
        'legs': opts.get('legs'),
    }
    if opts.get('best_of_mode') in ('escalating', 'custom') and opts.get('best_of_final'):
        raw['final_best_of'] = opts['best_of_final']
    try:
        return clean(key, raw)
    except SettingsError:
        return clean(key, {})


def of_match(match):
    """The settings that govern this match: its stage's, or the tournament's."""
    stage = getattr(match, 'stage', None)
    if stage is not None:
        return stage.settings or clean(stage.format, {})
    return for_tournament(match.tournament)


# --------------------------------------------------------------------------
# Presets. A preset fills a stage's settings; the organiser can change any of
# them afterwards. Keyed by game title substring, longest first, like the
# tiebreaker catalogue.
# --------------------------------------------------------------------------

PRESETS = {
    'fc mobile': {
        'modes': ['h2h', 'vs_attack'],
        'id_label': 'fc_mobile_user_id',
        'group': {'best_of': 2, 'draws': 'allowed', 'check_in_minutes': 10,
                  'room_settings': 'room.fc_mobile.group'},
        'knockout': {'best_of': 1, 'final_best_of': 3, 'draws': 'penalties',
                     'check_in_minutes': 10,
                     'room_settings': 'room.fc_mobile.knockout'},
    },
    'efootball': {
        'modes': ['friend_match'],
        'id_label': 'efootball_owner_id',
        'group': {'best_of': 1, 'draws': 'allowed', 'check_in_minutes': 10,
                  'room_settings': 'room.efootball.group'},
        'knockout': {'best_of': 1, 'final_best_of': 3, 'draws': 'penalties',
                     'check_in_minutes': 10,
                     'room_settings': 'room.efootball.knockout'},
    },
    'ea fc': {
        'modes': ['ultimate_team'],
        'id_label': 'ea_id',
        'group': {'best_of': 1, 'draws': 'allowed', 'check_in_minutes': 10},
        'knockout': {'best_of': 1, 'knockout_legs': 2, 'draws': 'penalties',
                     'check_in_minutes': 10},
    },
}


# Whole setups an organiser can start a stage from (P3 W1): the settings and
# the scoring together, changeable afterwards like anything else.
FORMAT_PRESETS = {
    # The Counter-Strike Major Swiss (Valve's rulebook, research catalogue
    # 4.4): three wins go through, three losses go out, highest against lowest
    # inside a record, Buchholz as opponents' wins minus losses, and every
    # match that can send somebody through or out best of three.
    'valve_major': {
        'format': 'swiss',
        'settings': {'pairing': 'valve', 'win_target': 3, 'loss_limit': 3, 'rounds': 5,
                     'best_of': 1, 'decisive_best_of': 3},
        'rules': {'buchholz': 'wins_minus_losses', 'tiebreakers': ['buchholz', 'seed']},
    },
}


def preset_for(game_title):
    needle = str(game_title or '').strip().lower()
    for key in sorted(PRESETS, key=len, reverse=True):
        if key in needle:
            return key, PRESETS[key]
    return None, None


def preset_settings(game_title, format_key):
    """The preset's settings for one stage, cleaned, or None."""
    _key, preset = preset_for(game_title)
    if preset is None:
        return None
    half = preset['group'] if is_table(format_key) else preset['knockout']
    return clean(format_key, dict(half))
