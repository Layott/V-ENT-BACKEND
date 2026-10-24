"""Choosing the format later (P4c, inbox 478, design section 14a).

CEO: "some tournament organizers, might not know the format they want to use for
an event, either the people who register choose the format, or based off the
amount of people who register, they can then pick what they want."

A stage may be saved with its format `undecided`. That is a state of the stage,
not a format: it is not in `formats.FORMATS` and never in the catalogue, so no
screen can offer it as something to play. Its settings say how it is settled:

  vote       the entrants choose among 2 to 5 offered formats
  bands      the confirmed count picks a format ("under 8: round robin ...")
  organiser  the organiser picks before the draw

Settling rewrites the stage as the chosen format, with that format's default
settings, and keeps what happened in `format_decision` so the page can still say
how it was decided. Until then `stage_engine.draw` refuses the stage with
STAGE_FORMAT_UNDECIDED.

Nothing here runs on a timer. Registration closing is a time and a status, so
`sweep` is called from the doors that read or draw a stage, the way the no-show
sweep is, and settling is idempotent.
"""
from django.utils import timezone

from . import formats, stage_settings

UNDECIDED = 'undecided'
DECIDE_BY = ('vote', 'bands', 'organiser')
APPLY = ('auto', 'confirm')
OPTION_RANGE = (2, 5)
DEFAULT_LABEL = 'Format to be announced'

# Once the tournament is in one of these, entering is over.
CLOSED_STATUSES = ('registration_closed', 'live', 'completed', 'cancelled')


class FormatLaterError(ValueError):
    def __init__(self, code, field=None, **detail):
        super().__init__(code)
        self.code = code
        self.field = field
        self.detail = detail


def is_undecided(stage):
    return stage.format == UNDECIDED


def _real(key):
    return formats.get(key)


def clean_rule(raw):
    """The stored settings of an undecided stage. Raises
    `stage_settings.SettingsError`, which stage saving already turns into a
    coded refusal with the field."""
    SettingsError = stage_settings.SettingsError
    raw = raw if isinstance(raw, dict) else {}

    decide_by = str(raw.get('decide_by') or 'organiser').strip().lower()
    if decide_by not in DECIDE_BY:
        raise SettingsError('DECIDE_BY_UNKNOWN', 'decide_by')

    options = raw.get('options') or []
    if not isinstance(options, list):
        raise SettingsError('UNDECIDED_OPTIONS_COUNT', 'options')
    kept = []
    for index, item in enumerate(options):
        fmt = _real(item)
        if fmt is None:
            raise SettingsError('UNDECIDED_OPTION_UNKNOWN', 'options', {'index': index})
        if fmt.key in kept:
            raise SettingsError('UNDECIDED_OPTION_REPEATED', 'options', {'index': index})
        kept.append(fmt.key)
    low, high = OPTION_RANGE
    if (decide_by == 'vote' or kept) and not low <= len(kept) <= high:
        raise SettingsError('UNDECIDED_OPTIONS_COUNT', 'options', {'min': low, 'max': high})

    apply = str(raw.get('apply') or 'auto').strip().lower()
    if apply not in APPLY:
        raise SettingsError('APPLY_UNKNOWN', 'apply')

    bands = _clean_bands(raw.get('bands'), required=decide_by == 'bands')
    return {'decide_by': decide_by, 'options': kept, 'bands': bands, 'apply': apply}


def _clean_bands(raw, required):
    SettingsError = stage_settings.SettingsError
    raw = raw or []
    if not isinstance(raw, list):
        raise SettingsError('BANDS_REQUIRED', 'bands')
    if required and not raw:
        raise SettingsError('BANDS_REQUIRED', 'bands')
    out = []
    for index, band in enumerate(raw):
        if not isinstance(band, dict):
            raise SettingsError('BAND_RANGE_INVALID', 'bands', {'index': index})
        try:
            low = int(band.get('min') if band.get('min') not in ('', None) else 0)
            high = band.get('max')
            high = None if high in ('', None) else int(high)
        except (TypeError, ValueError):
            raise SettingsError('BAND_RANGE_INVALID', 'bands', {'index': index})
        if low < 0 or (high is not None and high < low):
            raise SettingsError('BAND_RANGE_INVALID', 'bands', {'index': index})
        fmt = _real(band.get('format'))
        if fmt is None:
            raise SettingsError('BAND_FORMAT_UNKNOWN', 'bands', {'index': index})
        out.append({'min': low, 'max': high, 'format': fmt.key})
    out.sort(key=lambda b: b['min'])
    for before, after in zip(out, out[1:]):
        if before['max'] is None or after['min'] <= before['max']:
            raise SettingsError('BANDS_OVERLAP', 'bands')
    return out


def fits(fmt, tournament):
    """Whether a format can run inside the tournament's own size limits."""
    most, least = tournament.max_number_of_teams, tournament.min_number_of_teams
    if most and fmt.min_participants > most:
        return False
    if least and fmt.max_participants and fmt.max_participants < least:
        return False
    return True


def check_fit(tournament, rule):
    """Every format the rule can end in must fit the tournament's size limits."""
    for field, keys in (('options', rule.get('options') or []),
                        ('bands', [b['format'] for b in rule.get('bands') or []])):
        for key in keys:
            fmt = _real(key)
            if fmt is None or not fits(fmt, tournament):
                raise FormatLaterError('FORMAT_DOES_NOT_FIT', field, format=key)


# ---------------------------------------------------------------------------
# When it is decided
# ---------------------------------------------------------------------------

def registration_closed(tournament, now=None):
    now = now or timezone.now()
    if tournament.status in CLOSED_STATUSES:
        return True
    closes = tournament.registration_closes_at
    return bool(closes and closes <= now)


def confirmed_count(tournament):
    return tournament.registrations.filter(status='confirmed').count()


def rule_of(stage):
    return clean_rule(stage.settings)


def band_for(rule, count):
    for band in rule.get('bands') or []:
        if band['min'] <= count and (band['max'] is None or count <= band['max']):
            return band
    return None


def _active_registrations(tournament):
    return tournament.registrations.filter(status__in=('pending', 'confirmed')) \
        .select_related('user', 'team', 'squad')


def voter_registration(tournament, user):
    """The registration this person acts for, or None. Only the one who acts
    for a side votes for it: a lone entrant, a club's owner, a squad's captain."""
    if user is None:
        return None
    for reg in _active_registrations(tournament):
        acting = reg.acting_user
        if acting is not None and acting.pk == user.pk:
            return reg
    return None


def counts(stage):
    rule = rule_of(stage)
    tally = {key: 0 for key in rule['options']}
    for vote in stage.format_votes.all():
        if vote.choice in tally:
            tally[vote.choice] += 1
    return tally


def tally(stage):
    """('winner', key) | ('tie', [keys]) | ('none', [])."""
    votes = counts(stage)
    top = max(votes.values(), default=0)
    if top == 0:
        return 'none', []
    leaders = [k for k, n in votes.items() if n == top]
    return ('winner', leaders[0]) if len(leaders) == 1 else ('tie', leaders)


def cast_vote(stage, user, choice, now=None):
    from .models import TournamentFormatVote
    if not is_undecided(stage):
        raise FormatLaterError('STAGE_NOT_UNDECIDED')
    rule = rule_of(stage)
    if rule['decide_by'] != 'vote':
        raise FormatLaterError('NOT_A_VOTE')
    tournament = stage.tournament
    if registration_closed(tournament, now):
        raise FormatLaterError('VOTE_CLOSED')
    reg = voter_registration(tournament, user)
    if reg is None:
        raise FormatLaterError('NOT_AN_ENTRANT')
    fmt = _real(choice)
    if fmt is None or fmt.key not in rule['options']:
        raise FormatLaterError('FORMAT_NOT_OFFERED', 'format')
    TournamentFormatVote.objects.update_or_create(
        stage=stage, registration=reg, defaults={'choice': fmt.key})
    return fmt.key


# ---------------------------------------------------------------------------
# Settling
# ---------------------------------------------------------------------------

def settle(stage, key, by, *, detail=None):
    """Make the stage the format `key`. The caller has already decided WHO may."""
    fmt = _real(key)
    if fmt is None:
        raise FormatLaterError('FORMAT_UNKNOWN', 'format')
    tournament = stage.tournament
    if not fits(fmt, tournament):
        raise FormatLaterError('FORMAT_DOES_NOT_FIT', 'format', format=fmt.key)
    _check_neighbours(stage, fmt)

    rule = rule_of(stage)
    decision = {'by': by, 'decide_by': rule['decide_by'], 'format': fmt.key,
                'options': rule['options'], 'bands': rule['bands'],
                'at': timezone.now().isoformat()}
    decision.update(detail or {})
    stage.format = fmt.key
    stage.settings = stage_settings.clean(fmt.key, {})
    stage.format_decision = decision
    if stage.label == DEFAULT_LABEL:
        stage.label = fmt.label
    stage.save(update_fields=['format', 'settings', 'format_decision', 'label'])
    return decision


def _check_neighbours(stage, fmt):
    """The stage before must be able to feed it and it the one after, when
    those are decided; an undecided neighbour was checked against its options."""
    rows = list(stage.tournament.stages.all().order_by('order'))
    index = next((i for i, s in enumerate(rows) if s.id == stage.id), None)
    if index is None:
        return
    if index > 0 and not is_undecided(rows[index - 1]):
        before = rows[index - 1]
        feeds = stage_settings.feeds_into(before.format, before.settings)
        if feeds and fmt.key not in feeds:
            raise FormatLaterError('FORMAT_CANNOT_FOLLOW', 'format', format=fmt.key)
    if index + 1 < len(rows) and not is_undecided(rows[index + 1]):
        feeds = stage_settings.feeds_into(fmt.key, stage_settings.clean(fmt.key, {}))
        if rows[index + 1].format not in (feeds or ()):
            raise FormatLaterError('FORMAT_CANNOT_FOLLOW', 'format', format=fmt.key)


def sweep(tournament, now=None):
    """Settle every undecided stage whose moment has come. Safe to call as often
    as you like: a settled stage is not touched again."""
    pending = [s for s in tournament.stages.all() if is_undecided(s)]
    if not pending or not registration_closed(tournament, now):
        return []
    done = []
    for stage in pending:
        rule = rule_of(stage)
        if rule['decide_by'] == 'vote':
            kind, picked = tally(stage)
            if kind == 'winner':
                try:
                    settle(stage, picked, 'vote', detail={'counts': counts(stage)})
                    done.append(stage.id)
                except FormatLaterError:
                    pass    # left for the organiser, who sees why on the page
        elif rule['decide_by'] == 'bands' and rule['apply'] == 'auto':
            band = band_for(rule, confirmed_count(tournament))
            if band is not None:
                try:
                    settle(stage, band['format'], 'bands',
                           detail={'count': confirmed_count(tournament), 'band': band})
                    done.append(stage.id)
                except FormatLaterError:
                    pass    # left for the organiser, who sees why on the page
    return done


def organiser_pick(stage, key, now=None):
    """The organiser settles it by hand. A vote that is still open, or that has
    a clear winner, is not the organiser's to override."""
    if not is_undecided(stage):
        raise FormatLaterError('STAGE_NOT_UNDECIDED')
    rule = rule_of(stage)
    tournament = stage.tournament
    if rule['decide_by'] == 'vote':
        if not registration_closed(tournament, now):
            raise FormatLaterError('VOTE_STILL_OPEN')
        if key not in (f.key for f in (_real(k) for k in rule['options']) if f):
            raise FormatLaterError('FORMAT_NOT_OFFERED', 'format')
        return settle(stage, _real(key).key, 'organiser', detail={'counts': counts(stage)})
    if rule['decide_by'] == 'bands' and key in (None, ''):
        band = band_for(rule, confirmed_count(tournament))
        if band is None:
            raise FormatLaterError('NO_BAND_APPLIES')
        return settle(stage, band['format'], 'bands',
                      detail={'count': confirmed_count(tournament), 'band': band,
                              'confirmed_by_organiser': True})
    return settle(stage, key, 'organiser')


# ---------------------------------------------------------------------------
# What a screen is told
# ---------------------------------------------------------------------------

def _option(key):
    fmt = _real(key)
    return {'key': key, 'label': fmt.label if fmt else key}


def view(stage, *, viewer=None, manager=False):
    """How this stage is (or was) decided, for the stages payload. Votes while
    running go to the entrants and the organiser; the result goes to everybody."""
    if not is_undecided(stage):
        decision = stage.format_decision or {}
        if not decision:
            return None
        return dict(decision, undecided=False)
    tournament = stage.tournament
    rule = rule_of(stage)
    closed = registration_closed(tournament)
    count = confirmed_count(tournament)
    band = band_for(rule, count) if rule['bands'] else None
    out = {
        'undecided': True,
        'decide_by': rule['decide_by'],
        'apply': rule['apply'],
        'options': [_option(k) for k in rule['options']],
        'bands': [dict(b, label=_option(b['format'])['label']) for b in rule['bands']],
        'band_now': dict(band, label=_option(band['format'])['label']) if band else None,
        'confirmed_count': count,
        'registration_closed': closed,
        'votes': None, 'my_vote': None, 'needs_organiser': None, 'tied': None,
        'proposed': None,
    }
    if rule['decide_by'] == 'vote':
        mine = voter_registration(tournament, viewer)
        if manager or mine is not None:
            out['votes'] = counts(stage)
        if mine is not None:
            vote = stage.format_votes.filter(registration=mine).first()
            out['my_vote'] = vote.choice if vote else None
        out['voting_open'] = not closed
        if closed:
            kind, picked = tally(stage)
            if kind == 'tie':
                out['needs_organiser'], out['tied'] = 'tie', picked
            elif kind == 'none':
                out['needs_organiser'] = 'no_votes'
    elif rule['decide_by'] == 'bands':
        if band is not None and rule['apply'] == 'confirm' and closed:
            out['proposed'] = band['format']
        elif band is None and closed:
            out['needs_organiser'] = 'no_band'
    return out
