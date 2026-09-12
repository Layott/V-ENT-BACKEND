"""Every setting the tournament wizard collects has to survive the round trip.

    CEO, 2 September 2026: "BUILD A CATCHER FOR WHEN CREATING TOURNAMENTS AND
    BRACKETS TO MAKE SURE EVERYTHING IS SYNCED AND IT ALL LINKS TO EACH OTHER,
    SO THAT ONCE ONE INPUT IN TERMS OF SETTINGS OR OPTIONS IS OFF, IT'LL CATCH
    IT."

A setting an organiser types passes through four hands, and it is silently lost
at any one of them:

    1. the wizard SENDS it            formDataToSend.append('x', ...)
    2. create ACCEPTS it              request.data.get('x')
    3. edit ACCEPTS it                the same, in edit_tournament
    4. the view RETURNS it            so re-opening a draft can show it
    5. the mapper RESTORES it         back into the wizard's own field names

Nothing warns when a link is missing. The wizard posts the field, the endpoint
never reads it, the response is a cheerful 200, and the organiser is told it
saved. That is exactly what happened:

  * the wizard sent `max_number_of_participants`; edit only knew
    `max_number_of_teams`, so a tournament set to 5 teams reported 0/32
  * the wizard sent sponsors, prizes, options and league points; edit read
    none of them, so nothing changed after the first save
  * the mapper read `max_number_of_participants` off a payload that answers
    `max_number_of_teams`, so re-opening a draft drew an empty box - and an
    empty box submits the default

Each was invisible until somebody looked at a live record and disbelieved it.

    python tools/check-wizard-roundtrip.py

What it cannot do is prove a value is stored correctly; only a test does that.
What it catches is the link that was never made.
"""
import os
import re
import sys


def _workspace_root():
    """The directory holding V-ENT-BACKEND and V-ENT-FRONTEND.

    Walked for rather than computed from a fixed number of `dirname` calls, so
    this file works whether it sits in the workspace `tools/` or inside the
    backend repo's. It lives in the repo because the workspace root is not
    version controlled, and a checker that exists on one machine only is not a
    rule anybody else is held to.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, 'V-ENT-FRONTEND')):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        here = parent


ROOT = _workspace_root()

BACKEND = os.path.join(ROOT, 'V-ENT-BACKEND')
FRONTEND = os.path.join(ROOT, 'V-ENT-FRONTEND')

WIZARD = os.path.join(
    FRONTEND, 'src/components/create-tournament-component/CreateTournamentComponent.js')
PAGE = os.path.join(FRONTEND, 'src/app/tournaments/create-tournament/page.js')
VIEWS = os.path.join(BACKEND, 'vent_tournament/views.py')

# Fields the wizard sends that are deliberately not read back, with the reason.
# An exemption nobody can read is a hole nobody remembers opening.
NOT_ROUND_TRIPPED = {
    'is_draft': 'set by which button was pressed, never restored',
    'tournament_logo': 'a File. Cannot be put back into a file input',
    'tournament_banner': 'a File, as above',
    'rules_document': 'a File, as above',
    'sponsor_logos': 'Files, as above',
    'entry_type': 'a wizard-side alias for entry_fee',
}

# Fields whose name legitimately changes between the wizard and the model,
# because the wizard speaks about participants and the model about teams.
# Listed so the check knows they ARE linked rather than reporting them.
ALIASES = {
    'max_number_of_participants': ('max_number_of_teams', 'player_size'),
    'min_number_of_participants': ('min_number_of_teams',),
    'entry_fee': ('entry_fee_price', 'entry_fee'),
    'prize_data': ('prize_distributions', 'prize_distribution'),
    'sponsor_names': ('sponsors',),
    'sponsor_types': ('sponsors',),
    'sponsor_usernames': ('sponsors',),
    'prize_distribution_type': ('prize_type',),
}


def read(path):
    if not os.path.exists(path):
        return ''
    with open(path, encoding='utf-8', errors='replace') as handle:
        return handle.read()


def function_body(source, name):
    """The text of one python function, to its next top-level definition.

    Takes the LARGEST match, not the first. `views.py` carries a commented-out
    `create_tournament` from an older version at line 216, and taking the first
    match read that instead - which reports every field in the wizard as
    unread, i.e. a checker that cries wolf about everything and is therefore
    ignored. Dead code that looks like live code is exactly what a scanner
    trips on.
    """
    bodies = []
    for match in re.finditer(r'^def %s\(' % re.escape(name), source, re.M):
        start = match.start()
        nxt = re.compile(r'^(?:@|def )', re.M).search(source, start + 10)
        bodies.append(source[start:nxt.start() if nxt else len(source)])
    return max(bodies, key=len) if bodies else ''


def with_helpers(source, name, depth=2):
    """A view's body PLUS the bodies of the module-level helpers it calls.

    Calibration, 7 September 2026. This reported 19 broken links and TEN of
    them were `points_win`, `points_draw`, `points_loss`, `players_per_team`
    and `tiebreakers` against both create and edit. All five are read, by
    `_league_settings(request.data, team_size)`, which both views call - the
    checker just could not see through the call.

    Ten false positives out of nineteen is a checker nobody works down, and
    the three real faults behind them sat for weeks because of it. Two levels
    deep, because a helper may itself delegate; deeper than that and this
    starts matching the whole file, which would find everything and mean
    nothing.
    """
    body = function_body(source, name)
    if depth <= 0:
        return body
    text = [body]
    for called in set(re.findall(r'\b(_[a-z][a-z0-9_]*)\s*\(', body)):
        if called == name:
            continue
        helper = function_body(source, called)
        if helper:
            text.append(with_helpers(source, called, depth - 1))
    return '\n'.join(text)


def main():
    wizard = read(WIZARD)
    page = read(PAGE)
    views = read(VIEWS)

    sent = sorted(set(re.findall(
        r"formDataToSend\.append\(\s*'([a-z_0-9]+)'", wizard)))
    if not sent:
        print('Could not read the wizard. Has CreateTournamentComponent moved?')
        return 1

    create = with_helpers(views, 'create_tournament')
    edit = with_helpers(views, 'edit_tournament')
    view_one = function_body(views, 'view_tournament')

    problems = []

    for field in sent:
        if field in NOT_ROUND_TRIPPED:
            continue

        names = (field,) + ALIASES.get(field, ())

        def seen_in(block):
            return any(re.search(r"['\"]%s['\"]" % re.escape(n), block)
                       or re.search(r'\b%s\b' % re.escape(n), block)
                       for n in names)

        if not seen_in(create):
            problems.append((field, 'create_tournament never reads it',
                             'the wizard posts it and creating drops it'))
        if not seen_in(edit):
            problems.append((field, 'edit_tournament never reads it',
                             'continuing a draft silently discards this'))
        if not (seen_in(view_one) or seen_in(views)):
            problems.append((field, 'nothing returns it',
                             're-opening a draft cannot show what was chosen'))
        # The mapper is checked against the ALIASES too. It was checked
        # against the raw field name alone, so `prize_data` and the three
        # `sponsor_*` fields read as missing while the mapper restores them
        # under the wizard's own names, `prize_distribution` and `sponsors`.
        # Four more false positives in the same count of nineteen.
        if not any(re.search(r'\b%s\b' % re.escape(n), page) for n in names):
            problems.append((field, 'the draft mapper does not restore it',
                             're-opening a draft asks for it again, and an '
                             'empty field submits the default'))

    for field, where, why in problems:
        print('%-32s %s' % (field, where))
        print('%-32s %s' % ('', why))

    print('')
    print('%d field(s) the wizard sends, %d broken link(s)'
          % (len(sent), len(problems)))
    return 1 if problems else 0


SELF_TEST_SOURCE = '''
def _league_settings(data, team_size):
    return {'points_win': data.get('points_win', 3)}


def _unrelated(data):
    return data.get('nothing_to_do_with_it')


@api_view(['POST'])
def create_tournament(request):
    title = request.data.get('tournament_title')
    rules = _league_settings(request.data, 1)
    return Response({})


@api_view(['PUT'])
def edit_tournament(request, tournament_id):
    if 'tournament_title' in request.data:
        pass
    return Response({})
'''


def self_test():
    """Proved both ways, on the exact shapes this got wrong.

    `points_win` is read only inside a helper, and reporting it was ten of the
    nineteen findings on 7 September 2026. `never_read_anywhere` is the fault
    the checker exists for and must still be caught.
    """
    cases = [
        ('create_tournament', 'tournament_title', True,  'read directly'),
        ('create_tournament', 'points_win',       True,  'read inside a helper it calls'),
        ('create_tournament', 'never_read_here',  False, 'genuinely dropped'),
        ('edit_tournament',   'tournament_title', True,  'read directly'),
        ('edit_tournament',   'points_win',       False, 'edit does not call the helper'),
        ('create_tournament', 'nothing_to_do_with_it', False,
         'a helper it does NOT call must not count as reading it'),
    ]
    bad = 0
    for view, field, expected, why in cases:
        block = with_helpers(SELF_TEST_SOURCE, view)
        got = bool(re.search(r'\b%s\b' % re.escape(field), block))
        if got != expected:
            bad += 1
            print('FAIL  %s / %s: expected %s (%s)'
                  % (view, field, 'seen' if expected else 'not seen', why))
    if bad:
        print('%d of %d case(s) wrong' % (bad, len(cases)))
        return 1
    print('%d cases, both directions: self-test passed' % len(cases))
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
