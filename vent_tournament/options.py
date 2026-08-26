"""The settings a real tournament needs, beyond a name and a date.

Drawn from what organisers on Battlefy, Toornament, start.gg and Challengermode
actually configure, and from the tournament module spec. Everything here either
changes how the bracket is built, decides who may enter, or decides who forfeits
- nothing is stored for decoration.

Kept in one JSON column rather than fifteen new columns: these are read together,
written together, and the set grows. The ones that need to be queried across
tournaments stay as real columns on the model.
"""

# The same four words the bracket service already speaks, so the choice an
# organiser makes at creation can be handed to generate() unchanged.
SEEDING_METHODS = {
    'registration': 'In the order people registered',
    'random': 'Drawn at random',
    'ranked': 'By platform ranking, strongest first',
    'manual_order': 'Set by the organiser before the bracket is generated',
}

CHECK_IN_WINDOWS = [0, 10, 15, 30, 60]      # minutes before start; 0 means none

BEST_OF_ROUNDS = {
    'fixed': 'The same for every round',
    'escalating': 'Longer as the bracket progresses',
    'custom': 'Set per round',
}

DEFAULTS = {
    # Who may enter
    'restrict_region': '',            # empty means anywhere
    'restrict_country': '',
    'min_age': 0,
    'require_verified_email': True,
    'require_kyc': False,

    # Rosters
    'roster_lock': 'start',           # 'none' | 'registration_close' | 'start'
    'max_substitutes': 0,
    'allow_roster_changes_between_rounds': True,

    # Check-in
    'check_in_minutes': 15,
    'forfeit_without_check_in': True,

    # Bracket shape
    'seeding_method': 'registration',
    'third_place_match': False,
    'group_stage': False,
    'group_size': 4,
    'advance_per_group': 2,

    # Matches
    'best_of_mode': 'fixed',
    'best_of': 1,
    'best_of_final': 3,
    'match_interval_minutes': 30,

    # Conduct
    'require_screenshot': False,
    'dispute_window_minutes': 30,
    'rules_acknowledgement': True,
}

BOOLEANS = {
    'require_verified_email', 'require_kyc', 'allow_roster_changes_between_rounds',
    'forfeit_without_check_in', 'third_place_match', 'group_stage',
    'require_screenshot', 'rules_acknowledgement',
}

INTEGERS = {
    'min_age': (0, 99),
    'max_substitutes': (0, 10),
    'check_in_minutes': (0, 240),
    'group_size': (2, 16),
    'advance_per_group': (1, 8),
    'best_of': (1, 9),
    'best_of_final': (1, 9),
    'match_interval_minutes': (5, 600),
    'dispute_window_minutes': (5, 1440),
}


def clean(raw):
    """Take what the wizard sent and return something safe to store.

    Anything unrecognised is dropped, anything out of range is clamped, and the
    result always carries every key - so a reader never has to guess whether a
    missing value means "off" or "not asked".
    """
    incoming = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)

    for key, value in incoming.items():
        if key not in DEFAULTS:
            continue

        if key in BOOLEANS:
            out[key] = bool(value)
        elif key in INTEGERS:
            low, high = INTEGERS[key]
            try:
                out[key] = max(low, min(high, int(value)))
            except (TypeError, ValueError):
                pass
        elif key == 'seeding_method':
            out[key] = value if value in SEEDING_METHODS else DEFAULTS[key]
        elif key == 'best_of_mode':
            out[key] = value if value in BEST_OF_ROUNDS else DEFAULTS[key]
        elif key == 'roster_lock':
            out[key] = value if value in ('none', 'registration_close', 'start') else DEFAULTS[key]
        else:
            out[key] = str(value or '')[:80]

    # A group stage that advances more players than it holds is not a group
    # stage. Clamp rather than refuse: the organiser meant "everybody goes
    # through", which is a legitimate, if unusual, shape.
    if out['advance_per_group'] > out['group_size']:
        out['advance_per_group'] = out['group_size']

    # An escalating bracket that ends shorter than it starts is a typo.
    if out['best_of_mode'] == 'escalating' and out['best_of_final'] < out['best_of']:
        out['best_of_final'] = out['best_of']

    return out


def check_in_state(tournament, now):
    """Whether check-in is open, and when it closes.

    Returns None when the tournament does not use check-in at all, so callers
    can tell "not required" from "required and closed".
    """
    from datetime import timedelta

    options = clean(getattr(tournament, 'options', None))
    minutes = options['check_in_minutes']
    if not minutes or not tournament.start_date_and_time:
        return None

    opens = tournament.start_date_and_time - timedelta(minutes=minutes)
    closes = tournament.start_date_and_time

    # An organiser who has closed check-in has closed it. The clock does not
    # get to reopen a window somebody already drew a line under.
    closed_by_organiser = getattr(tournament, 'check_in_closed_at', None)
    if closed_by_organiser:
        closes = min(closes, closed_by_organiser)

    return {
        'required': True,
        'opens_at': opens,
        'closes_at': closes,
        'open_now': opens <= now <= closes and not closed_by_organiser,
        'closed': bool(closed_by_organiser) or now > closes,
        'closed_by_organiser': bool(closed_by_organiser),
        'forfeit_without_check_in': options['forfeit_without_check_in'],
    }


def entry_refusal(tournament, user):
    """Why this person may not enter, or None if they may.

    Reads the restrictions an organiser set. Returning the sentence rather than
    a code because every caller ends up showing it to somebody.
    """
    options = clean(getattr(tournament, 'options', None))

    if options['require_verified_email'] and not getattr(user, 'is_active', False):
        return 'This tournament is only open to accounts with a verified email address.'

    if options['require_kyc']:
        wallet = getattr(user, 'wallet', None)
        if not getattr(wallet, 'kyc_verified', False):
            return 'This tournament requires a verified identity before you can enter.'

    country = (options['restrict_country'] or '').strip().lower()
    if country and (user.country or '').strip().lower() != country:
        return f'This tournament is open to players in {options["restrict_country"]} only.'

    if options['min_age']:
        # UserProfile is a plain FK rather than a one-to-one, so there is no
        # `user.userprofile` accessor - reading one would quietly return None
        # and refuse every entrant for want of a birthday they had filled in.
        profile = user.userprofile_set.order_by('profile_id').first()
        birthday = getattr(profile, 'date_of_birth', None)
        if birthday is None:
            return (
                f'This tournament is {options["min_age"]}+, so it needs your date of birth '
                'on your profile first.'
            )
        from datetime import date
        today = date.today()
        age = today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))
        if age < options['min_age']:
            return f'This tournament is open to players aged {options["min_age"]} and over.'

    return None


def best_of_for_round(tournament, round_number, total_rounds):
    """How many maps this round is played over."""
    options = clean(getattr(tournament, 'options', None))
    if options['best_of_mode'] == 'fixed':
        return options['best_of']
    if options['best_of_mode'] == 'escalating':
        if total_rounds <= 1 or round_number >= total_rounds:
            return options['best_of_final']
        # Step from best_of up to best_of_final across the rounds, in odd
        # numbers, because a best-of has to be able to end.
        span = options['best_of_final'] - options['best_of']
        step = span * (round_number - 1) / max(1, total_rounds - 1)
        value = int(options['best_of'] + step)
        return value if value % 2 else value + 1
    return options['best_of']
