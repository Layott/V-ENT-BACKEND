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
    'esports_image': {
        'check': AUTOMATIC,
        # Not the same thing as a profile picture. This one is a picture the
        # person has RELEASED for organisers to use on event and tournament
        # pages, which is what an organiser actually needs for a bracket, a
        # player card or a broadcast overlay.
        'label': 'Have released an esports picture',
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
    'penalty_points': {
        'check': AUTOMATIC,
        # The column has existed on both a profile and a team since the
        # beginning and nothing has ever read it. The spec asks for it twice:
        # "penalty point limit" as an entry condition, and "restrict or block
        # teams on penalty points", which is the same rule seen from the two
        # sides it applies to.
        'label': 'Be under a penalty point limit',
        'config': {'max_points': 5},
        'premium': True,
    },
    'ranking': {
        'check': AUTOMATIC,
        # Above or below a position, because both directions are real: a closed
        # invitational wants the top sixteen, and a newcomers' cup wants
        # everybody outside it. The scope is the tournament's own game, and a
        # region when the organiser names one.
        'label': 'Be inside or outside a ranking position',
        'config': {'mode': 'top', 'position': 100, 'region': ''},
        'premium': True,
    },
    'partner_verified': {
        'check': PARTNER,
        'label': 'A partner confirms the account',
        'config': {'partner': '', 'field_label': '', 'help': ''},
    },
}


def _options_for(kind):
    """The choices behind a field, for the screen that draws it."""
    if kind == 'ranking':
        from vent_auth import regions
        return {'modes': ['top', 'below'], 'regions': list(regions.ORDER)}
    return {}


#: The kinds a premium account may use. Built from the same table the kinds are
#: defined in, so adding one cannot leave this list behind.
PREMIUM_KINDS = frozenset(
    key for key, spec in KINDS.items() if spec.get('premium'))


def kind_catalogue():
    """Everything an organiser can add, for the wizard."""
    return [
        {
            'kind': key,
            'label': spec['label'],
            'checked_by': spec['check'],
            'config': spec['config'],
            # What a field may be set to, where the answer is a list the server
            # owns. A region typed by hand and misspelled resolves to no
            # countries, which silently means "the whole platform": a wider
            # tournament than the organiser asked for, with nothing to see.
            'options': _options_for(key),
            # So the wizard can show it as a premium feature rather than
            # offering it and refusing on save. A control rendered live and
            # then refused is the thing the signed-out rule bans, and it is no
            # better when the reason is a plan instead of an account.
            'premium': bool(spec.get('premium')),
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

    elif kind == 'penalty_points':
        try:
            limit = int(config.get('max_points'))
        except (TypeError, ValueError):
            raise RequirementError('The penalty point limit has to be a number.',
                                   'max_points')
        if not 0 <= limit <= 1000:
            raise RequirementError('A penalty point limit between 0 and 1000.',
                                   'max_points')
        out['config']['max_points'] = limit

    elif kind == 'ranking':
        mode = str(config.get('mode') or 'top').strip().lower()
        if mode not in ('top', 'below'):
            raise RequirementError(
                'A ranking condition is either the top of the table or below a '
                'position.', 'mode')
        try:
            position = int(config.get('position'))
        except (TypeError, ValueError):
            raise RequirementError('The ranking position has to be a number.',
                                   'position')
        if position < 1:
            raise RequirementError('A ranking position of 1 or more.', 'position')
        out['config']['mode'] = mode
        out['config']['position'] = position
        # Empty means the whole platform, which is what an unfilled field
        # should mean. A region that is NOT empty and is not one we know is
        # refused rather than ignored: ignoring it admits everybody, which is
        # the opposite of what somebody typing a region wants.
        region = str(config.get('region') or '').strip()[:60]
        if region:
            from vent_auth import regions
            if region not in regions.ORDER:
                raise RequirementError(
                    'That is not a region V-ENT knows. Leave it blank for the '
                    'whole platform.', 'region')
        out['config']['region'] = region

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

    elif kind == 'esports_image':
        # Both halves, always together. An esports picture with no release is
        # a picture nobody has granted the organiser anything over, so it does
        # not satisfy a requirement that exists precisely to obtain that grant.
        from vent_auth.models import UserGallery
        released = UserGallery.objects.filter(
            user=user, kind=UserGallery.KIND_ESPORTS,
            released_at__isnull=False).exists()
        if not released:
            return False, ('Upload an esports picture and release it for '
                           'organisers before entering.'), {
                'code': 'esports_image', 'params': {}}

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

    elif kind == 'penalty_points':
        limit = config.get('max_points', 0)
        profile = user.userprofile_set.order_by('profile_id').first()
        theirs = getattr(profile, 'penalty_point', 0) or 0
        if theirs > limit:
            return False, ('This tournament is for players with %s penalty '
                           'points or fewer, and you have %s.'
                           % (limit, theirs)), {
                'code': 'penalty_points',
                'params': {'limit': limit, 'points': theirs}}
        # Both sides of one rule. A team carries penalties of its own, and a
        # tournament that bars a player for them has no reason to admit a club
        # with worse.
        team_points = (getattr(team, 'penalty_points', 0) or 0) if team is not None else 0
        if team_points > limit:
            return False, ('This tournament is for teams with %s penalty points '
                           'or fewer, and yours has %s.'
                           % (limit, team_points)), {
                'code': 'penalty_points_team',
                'params': {'limit': limit, 'points': team_points}}

    elif kind == 'ranking':
        # Worked out from completed matches each time it is asked, which is the
        # same work the rankings page does and through the same module, so the
        # rank enforced here is the rank shown there. It runs only when an
        # organiser has actually set this requirement.
        from vent_auth import ranking_core
        game = getattr(tournament, 'tournament_game', None)
        rank, total = ranking_core.position_for(
            user,
            game=getattr(game, 'game_title', None),
            region=config.get('region') or None)
        position = config.get('position') or 1
        where = config.get('region') or ''
        if config.get('mode') == 'below':
            # A newcomers' cup. Unranked counts as below, which is the point:
            # somebody who has never played is exactly who it is for.
            if rank is not None and rank <= position:
                return False, ('This tournament is for players outside the top '
                               '%s.' % position), {
                    'code': 'ranking_below',
                    'params': {'position': position, 'rank': rank,
                               'region': where}}
        elif rank is None:
            # Unranked is its own answer. "You are in the top 100" is not a
            # sentence to show somebody who has never played a match, and
            # telling them so is the difference between a rule and a wall.
            return False, ('This tournament is for ranked players, and you have '
                           'not completed a ranked match yet.'), {
                'code': 'ranking_unranked',
                'params': {'position': position, 'region': where}}
        elif rank > position:
            return False, ('This tournament is for players in the top %s, and '
                           'you are %s.' % (position, rank)), {
                'code': 'ranking_top',
                'params': {'position': position, 'rank': rank,
                           'total': total, 'region': where}}

    elif kind == 'team_logo':
        if team is not None and not getattr(team, 'team_logo', None):
            return False, 'Your team needs a logo before it can enter.', {
                'code': 'team_logo', 'params': {}}

    return True, None, None


def is_automatic(requirement):
    return KINDS.get(requirement.get('kind'), {}).get('check') == AUTOMATIC


# Kinds that are about a person, so a team has to satisfy them once per member
# rather than once for whoever pressed the button.
PER_MEMBER = {
    'country', 'min_age', 'verified_email', 'verified_identity',
    'profile_image', 'esports_image', 'game_account', 'game_details',
    # Every player, not just whoever pressed the button. A team of five whose
    # fourth player is suspended is not an eligible team, and nobody would find
    # out until the match.
    'penalty_points', 'ranking',
}


def team_members(team):
    """Everyone who would actually play, captain included.

    Returns the captain first, because when several members fail it is the most
    useful one to name, and an empty list when the team has no members recorded
    rather than raising - a team with nobody in it fails the size check
    elsewhere, and failing twice for the same reason helps nobody.
    """
    if team is None:
        return []
    from vent_auth.models import TeamMembers

    rows = list(
        TeamMembers.objects.filter(team=team)
        .select_related('user')
        .order_by('-is_captain', 'team_member_id')
    )
    members = [row.user for row in rows if row.user_id]

    # The owner is not always in TeamMembers, and they are certainly on the team.
    owner = getattr(team, 'team_owner', None)
    if owner is not None and all(m.user_id != owner.user_id for m in members):
        members.insert(0, owner)
    return members


def check_for_team(requirement, team, *, tournament=None):
    """(met, reason, detail) for a whole team, naming the member who failed."""
    for member in team_members(team):
        met, reason, detail = check_automatic(
            requirement, member, tournament=tournament, team=team)
        if not met:
            who = member.username or member.full_name or 'A team member'
            detail = dict(detail or {})
            detail['params'] = dict(detail.get('params') or {}, member=who)
            detail['code'] = 'member_%s' % detail.get('code', 'unknown')
            return False, '%s: %s' % (who, reason), detail
    return True, None, None


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
            # A team entry satisfies a per-person requirement once per member.
            # Checking only whoever pressed the button admits a team whose
            # fourth player never connected an account, and nobody finds out
            # until the match.
            if team is not None and kind in PER_MEMBER:
                met, reason, detail = check_for_team(
                    requirement, team, tournament=tournament)
            else:
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
