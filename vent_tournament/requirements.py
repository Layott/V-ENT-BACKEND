"""What an organiser can demand before somebody registers.

There were four booleans - a country, a minimum age, a verified email, a
verified identity - and the CEO asked for something a good deal larger:

  "if each player that is being registered fr the event has an esport image, if
   each player has connected a particualr account in their profile, or we can
   set things each player should do (maybe follow a partiular set of people on
   socials and yu give them the links and then input their usernames for
   verification for the admin to verify, or you put download links for them to
   download a software or app and input their username or ID or whatever field
   is required and what the name of the field should be... or if the team has a
   team logo uploaded, or if all player has filled in all details for the game
   like their uids or in game names)"

Four booleans cannot express "follow these three accounts and give me your Riot
ID". So a requirement is a **row**, not a column, and requirements fall into
three kinds - the difference being who does the checking.

**Checked here, instantly, from what we already hold.** Country, age, a verified
email, a verified identity, a profile picture, a connected game account for THIS
game, the game details filled in, a team logo. Nobody waits and nobody reviews.

**Checked by a person, from something the entrant submits.** Follow these
accounts and tell us your username on each. Download this and give us the field
the organiser named - the organiser writes the label, because "Riot ID" and
"Epic username" are not the same question.

**Checked by a partner's own system.** The structure is here so a partner can
answer "is this username real and does it belong to this person" without
anybody typing. It **degrades to the manual case** when no partner is connected
or the partner is down, because blocking registration on somebody else's uptime
is not a trade worth making.

Two rules that hold throughout:

  * a refusal names WHICH requirement failed, and for a team, WHICH member. "You
    are not eligible" sends somebody to support; "Chidi has not connected a Free
    Fire account" they can fix themselves.
  * nothing here blocks a tournament with no requirements set. The default is
    still open to everyone.
"""
from datetime import date


# --------------------------------------------------------------------------
# The kinds
# --------------------------------------------------------------------------

AUTOMATIC = 'automatic'      # we already hold the answer
SUBMITTED = 'submitted'      # the entrant gives us something, a person checks it
PARTNER = 'partner'          # a partner's system answers, or a person does

KINDS = {
    'country': {
        'check': AUTOMATIC,
        'label': 'Play from a particular country',
        'config': {'countries': []},
    },
    'min_age': {
        'check': AUTOMATIC,
        'label': 'Be over a minimum age',
        'config': {'min_age': 18},
    },
    'verified_email': {
        'check': AUTOMATIC,
        'label': 'Have a verified email address',
        'config': {},
    },
    'verified_identity': {
        'check': AUTOMATIC,
        'label': 'Have a verified identity',
        'config': {},
    },
    'profile_image': {
        'check': AUTOMATIC,
        'label': 'Have a profile picture',
        'config': {},
    },
    'game_account': {
        'check': AUTOMATIC,
        'label': 'Have connected an account for this game',
        'config': {},
    },
    'game_details': {
        'check': AUTOMATIC,
        'label': 'Have filled in their in-game name or UID',
        'config': {},
    },
    'team_logo': {
        'check': AUTOMATIC,
        'label': 'The team has a logo',
        'config': {},
    },
    'social_follow': {
        'check': SUBMITTED,
        'label': 'Follow these accounts',
        # The organiser gives the links; the entrant gives a username per link.
        'config': {'links': [], 'help': ''},
    },
    'download': {
        'check': SUBMITTED,
        'label': 'Download something and give us a detail from it',
        # The organiser names the field, because "Riot ID" and "Epic username"
        # are not the same question and a generic "Username" asks neither.
        'config': {'url': '', 'field_label': '', 'help': ''},
    },
    'custom_field': {
        'check': SUBMITTED,
        'label': 'Answer a question',
        'config': {'field_label': '', 'help': ''},
    },
    'partner_verified': {
        'check': PARTNER,
        'label': 'A partner confirms the account',
        'config': {'partner': '', 'field_label': '', 'help': ''},
    },
}


def kind_catalogue():
    """Everything an organiser can add, for the wizard."""
    return [
        {
            'kind': key,
            'label': spec['label'],
            'checked_by': spec['check'],
            'config': spec['config'],
        }
        for key, spec in KINDS.items()
    ]


class RequirementError(ValueError):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def clean(raw):
    """Check one submitted requirement and return the stored form."""
    if not isinstance(raw, dict):
        raise RequirementError('A requirement is a set of named settings.')

    kind = str(raw.get('kind') or '').strip()
    spec = KINDS.get(kind)
    if spec is None:
        raise RequirementError('There is no requirement called %r.' % kind, 'kind')

    config = raw.get('config') or {}
    if not isinstance(config, dict):
        raise RequirementError('config is a set of named settings.', 'config')

    out = {'kind': kind, 'required': bool(raw.get('required', True)), 'config': {}}

    if kind == 'country':
        countries = [str(c).strip().upper() for c in (config.get('countries') or []) if str(c).strip()]
        if not countries:
            raise RequirementError('Name at least one country.', 'countries')
        out['config']['countries'] = countries

    elif kind == 'min_age':
        try:
            age = int(config.get('min_age'))
        except (TypeError, ValueError):
            raise RequirementError('The minimum age has to be a number.', 'min_age')
        if not 0 < age < 100:
            raise RequirementError('A minimum age between 1 and 99.', 'min_age')
        out['config']['min_age'] = age

    elif kind == 'social_follow':
        links = [str(u).strip() for u in (config.get('links') or []) if str(u).strip()]
        if not links:
            raise RequirementError('Give at least one account to follow.', 'links')
        out['config']['links'] = links
        out['config']['help'] = str(config.get('help') or '')[:400]

    elif kind in ('download', 'custom_field', 'partner_verified'):
        label = str(config.get('field_label') or '').strip()
        if not label:
            raise RequirementError(
                'Name the field you are asking for, so the entrant knows what to type.',
                'field_label')
        out['config']['field_label'] = label[:80]
        out['config']['help'] = str(config.get('help') or '')[:400]
        if kind == 'download':
            url = str(config.get('url') or '').strip()
            if not url.startswith(('http://', 'https://')):
                raise RequirementError('Give the link they should download from.', 'url')
            out['config']['url'] = url[:400]
        if kind == 'partner_verified':
            out['config']['partner'] = str(config.get('partner') or '').strip()[:80]

    return out


# --------------------------------------------------------------------------
# The automatic checks
# --------------------------------------------------------------------------

def _age(birthday):
    today = date.today()
    return today.year - birthday.year - (
        (today.month, today.day) < (birthday.month, birthday.day))


def check_automatic(requirement, user, *, tournament=None, team=None):
    """(met, reason, detail). `reason` names what to do, not that they failed.

    "You are not eligible" sends somebody to support. "Chidi has not connected a
    Free Fire account" they can fix themselves in a minute.

    `detail` carries a code and its parameters so the page can write the same
    sentence in the reader's language. The English `reason` stays as the
    fallback for anything reading the API directly.
    """
    kind = requirement['kind']
    config = requirement.get('config') or {}

    if kind == 'country':
        wanted = config.get('countries') or []
        theirs = (getattr(user, 'country', '') or '').strip().upper()
        if theirs not in wanted:
            names = ', '.join(wanted)
            return False, 'This tournament is open to players in %s.' % names, {
                'code': 'country', 'params': {'countries': names}}

    elif kind == 'min_age':
        # UserProfile is a plain FK rather than a one-to-one, so `user.userprofile`
        # would quietly return nothing and refuse everybody for want of a
        # birthday they had actually filled in.
        profile = user.userprofile_set.order_by('profile_id').first()
        birthday = getattr(profile, 'date_of_birth', None)
        age = config['min_age']
        if birthday is None:
            return False, ('This tournament is %s+, so it needs your date of birth '
                           'on your profile first.' % age), {
                'code': 'min_age_no_dob', 'params': {'age': age}}
        if _age(birthday) < age:
            return False, ('This tournament is open to players aged %s and over.'
                           % age), {'code': 'min_age', 'params': {'age': age}}

    elif kind == 'verified_email':
        if not getattr(user, 'is_active', False):
            return False, 'Verify your email address first.', {
                'code': 'verified_email', 'params': {}}

    elif kind == 'verified_identity':
        wallet = getattr(user, 'wallet', None)
        if not getattr(wallet, 'kyc_verified', False):
            return False, 'This tournament needs a verified identity.', {
                'code': 'verified_identity', 'params': {}}

    elif kind == 'profile_image':
        profile = user.userprofile_set.order_by('profile_id').first()
        if not getattr(profile, 'profile_picture', None):
            return False, 'Add a picture to your profile first.', {
                'code': 'profile_image', 'params': {}}

    elif kind == 'game_account':
        game = getattr(tournament, 'tournament_game', None)
        if game is None:
            return True, None, None
        from vent_auth.models import GameAccount
        if not GameAccount.objects.filter(user=user, game=game).exists():
            return False, ('Connect your %s account on your profile first.'
                           % game.game_title), {
                'code': 'game_account', 'params': {'game': game.game_title}}

    elif kind == 'game_details':
        game = getattr(tournament, 'tournament_game', None)
        if game is None:
            return True, None, None
        from vent_auth.models import GameAccount
        account = GameAccount.objects.filter(user=user, game=game).first()
        if account is None or not (account.game_username or '').strip():
            return False, ('Add your in-game name for %s on your profile first.'
                           % game.game_title), {
                'code': 'game_details', 'params': {'game': game.game_title}}

    elif kind == 'team_logo':
        if team is not None and not getattr(team, 'team_logo', None):
            return False, 'Your team needs a logo before it can enter.', {
                'code': 'team_logo', 'params': {}}

    return True, None, None


def is_automatic(requirement):
    return KINDS.get(requirement.get('kind'), {}).get('check') == AUTOMATIC


def evaluate(requirements, user, *, tournament=None, team=None, submissions=None):
    """What this person still owes, in order.

    Returns a list of rows. `met` false and `required` true is what stops a
    registration; `blocking()` picks those out. Every row carries a `code` and
    its `params` so the page can write the sentence in the reader's language,
    and the English `reason` as the fallback.

    A tournament with no requirements always produces an empty list, which is
    the default and the overwhelmingly common case.
    """
    submissions = submissions or {}
    out = []

    for requirement in requirements or []:
        kind = requirement.get('kind')
        spec = KINDS.get(kind)
        if spec is None:
            continue

        common = {
            # `id` is carried through so the entrant's screen can post a
            # submission against the right row. Without it the checklist
            # renders a Send button with nowhere to send to.
            'id': requirement.get('id'),
            'kind': kind,
            'label': spec['label'],
            'required': requirement.get('required', True),
            'config': requirement.get('config') or {},
        }

        if spec['check'] == AUTOMATIC:
            met, reason, detail = check_automatic(
                requirement, user, tournament=tournament, team=team)
            out.append(dict(common,
                            met=met, reason=reason,
                            needs_submission=False,
                            # Nobody is reviewing an automatic check, so a row
                            # that is not met is theirs to act on now. The page
                            # draws that differently from one that is waiting
                            # on a person.
                            waiting_on_review=False,
                            code=(detail or {}).get('code'),
                            params=(detail or {}).get('params') or {}))
            continue

        # Submitted, or partner-checked and falling back to submitted. Met only
        # once a person has approved it.
        state = submissions.get(kind)
        status = (state or {}).get('status')
        met = status == 'approved'
        reason = None
        code = None
        params = {}
        if not met:
            if status == 'refused':
                # The organiser's own words. Not translatable, and should not be.
                reason = state.get('note') or 'This was not accepted. Send it again.'
                code = 'refused'
                params = {'note': state.get('note') or ''}
            elif state:
                reason = 'Waiting for the organiser to check this.'
                code = 'pending'
            else:
                reason = spec['label']
                code = 'todo'

        out.append(dict(common,
                        met=met, reason=reason,
                        needs_submission=not state,
                        waiting_on_review=bool(state) and status == 'pending',
                        code=code, params=params))

    return out


def blocking(results):
    """The ones that actually stop a registration."""
    return [r for r in results if r.get('required', True) and not r['met']]
