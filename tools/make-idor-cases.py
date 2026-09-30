"""Write security/idor-cases.json from a probe of every id-taking address (R88).

    python manage.py idor_routes > routes.json
    python tools/idor-probe.py routes.json results.json
    python tools/make-idor-cases.py routes.json results.json

The hand-written cases (no "generated" key) are kept, with their event slug
brought up to the fixture of the day. One case per address is added, its kind
decided by what the probe saw and read by hand (inbox 398, 30 September 2026):

  public read       a visitor with no session got the page: a tournament, an
                    event, a club. GET is expected for everybody; the writes are
                    still tried as B. Its existence is no secret, so the real
                    versus missing check is skipped for it.
  viewer read       B got an answer and a visitor did not: pages that answer
                    only about the person asking ("mine", a roster).
  anybody's action  B's write went through by design: follow, join, like, a
                    waitlist of your own. The other methods are not tried.
  owner only        B was refused on everything: the full probe runs.

Any 2xx that is none of those is a fault and is printed, not written.
"""
import json
import sys

ROUTES, RESULTS = sys.argv[1], sys.argv[2]
CASES = 'security/idor-cases.json'
ORDER = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']

# Read by hand from the probe on 30 September 2026: every write B made that
# answered 2xx, and why it is the feature rather than a hole.
ANYBODY = {
    'event/<str:event_id>/waitlist/': 'joining and leaving an event waitlist is your own entry',
    'event/<str:event_id>/track/': 'a page-view beacon; records nothing about another person',
    'event/<str:event_id>/origins/': 'withdrawing your own map cell',
    'event/<str:event_id>/ref/<str:code>/visit/': 'a referral click counter',
    'team/request-join/<int:team_id>/': 'asking to join a team is open to everybody',
    'organization/<str:org_id>/apply/': 'applying to an organisation is open to everybody',
    'organization/<str:org_id>/follow/': 'following is open to everybody',
    'auth/follow/<str:kind>/<str:ref>/': 'following a person or a team is open to everybody',
    'post/<int:post_id>/like/': 'liking a public post',
    'club/<str:club_id>/join/': 'joining an open club',
    'club/<str:club_ref>/leave/': 'leaving a club you are in',
    'thread/<int:thread_id>/upvote/': 'upvoting a public thread',
    'device/<str:device_id>/revoke/': 'acts only on the caller\'s own session whatever id is sent',
}
# Kinds of record whose page answers everybody, so a 403 on one says nothing new.
# Open actions B may take, answered 400 or 409 to the empty body the probe
# sends: the refusal is about the request, not about whose record it is. Read
# by hand, 30 September 2026.
OPEN = {
    'tournament/<str:tournament_id>/head-to-head/': 'public statistics that need two names',
    'tournament/<str:tournament_id>/stats/head-to-head/': 'public statistics that need two names',
    'tournament/match/<int:match_id>/report-score/': 'refused 403 to anybody not in the match (checked first since 30 Sept)',
    'tournament/match/<int:match_id>/confirm-score/': 'refused 403 to anybody not in the match (checked first since 30 Sept)',
    'tournament/<str:tournament_id>/check-in/': 'checking yourself in; the answer is about the check-in window',
    'tournament/<str:tournament_id>/lineup/submit/': 'your own lineup; the answer is about the tournament not using lineups',
    'event/<str:event_id>/slots/<int:slot_id>/buy/': 'buying a vendor slot is open to anybody',
    'event/vendor/<str:vendor_id>/order/': 'ordering from a stall is open to anybody',
    'event/vendor/<str:vendor_id>/contact/': 'writing to a stall is open to anybody',
    'event/vendor/<str:vendor_id>/quote/': 'a price quote is open to anybody',
    'event/<str:event_id>/guest-buy/': 'buying a ticket is open to anybody',
    'event/<str:event_id>/buy-ticket/': 'buying a ticket is open to anybody',
    'team/leave/<int:team_id>/': 'leaving a team is about your own membership (NOT_MEMBER)',
    'post/<int:post_id>/comment/': 'commenting on a public post',
    'thread/<int:thread_id>/reply/': 'replying to a public thread',
}
PUBLIC_FIRST = {'tournament_id', 'tournament_ref', 'event_id', 'event_ref', 'org_id', 'org_ref',
                'team_id', 'team_ref', 'club_ref', 'club_id', 'post_id', 'thread_id', 'user_id',
                'game_id', 'scrim_id',
                # on the public bracket and stall pages, and the follow/hook target named by kind+ref
                'match_id', 'tie_id', 'vendor_id', 'kind'}


def main():
    spec = json.load(open(ROUTES, encoding='utf-8'))
    rows = json.load(open(RESULTS, encoding='utf-8'))
    seen = {}
    for r in rows:
        seen.setdefault(r['route'], {}).setdefault(r['method'], {})[r['who']] = r['status']

    doc = json.load(open(CASES, encoding='utf-8'))
    kept = [c for c in doc.get('cases', []) if not c.get('generated')]
    for c in kept:
        for part in ('attempt', 'setup'):
            if part in c:
                c[part]['path'] = c[part]['path'].replace('walk-con-ea5e', spec['event'])
    faults, cases = [], []
    for r in spec['routes']:
        answers = seen.get(r['route'], {})
        ok = lambda m, who: 200 <= answers.get(m, {}).get(who, 0) < 300
        public = ok('GET', 'anon')
        viewer = ok('GET', 'b') and not public
        b_status = lambda m: answers.get(m, {}).get('b', 0)
        writes_ok = [m for m in ORDER[1:] if ok(m, 'b')]
        anybody = ANYBODY.get(r['route'])
        if writes_ok and not anybody:
            faults.append('%s %s: B got %s' % (','.join(writes_ok), r['route'],
                                                [answers[m]['b'] for m in writes_ok]))
            continue
        # The attempt is a method the view serves and refuses B on, a write
        # first. The probe tried all five, so a 405 means "not served".
        refused = [m for m in ORDER[1:] + ['GET'] if b_status(m) in (401, 403, 404)]
        expect = None
        open_reason = None
        if refused:
            method = refused[0]
            if b_status(method) == 401:
                # The partner API takes a partner key, never a person's session.
                expect = [401, 403, 404]
        elif public or viewer:
            method, expect = 'GET', [200]
        elif writes_ok:
            # Your own action: the first time it is made, a repeat is refused
            # as a repeat (already following, already applied).
            method, expect = writes_ok[0], sorted({b_status(writes_ok[0]), 200, 201, 400, 409})
        elif r['route'] in OPEN:
            method = next(m for m in ORDER if b_status(m) in (400, 409))
            expect = [b_status(method)]
            open_reason = OPEN[r['route']]
        else:
            served = [m for m in ORDER if b_status(m) not in (405, 0)]
            faults.append('%s: nothing refused B and nothing answered, B got %s' % (
                r['route'], {m: b_status(m) for m in served}))
            continue
        last = r['params'][-1]
        value = r['values'][last]
        path = r['path']
        if value != 'r88-none' and path.count('/%s/' % value) == 1:
            path = path.replace('/%s/' % value, '/{id}/')
        case = {'generated': True, 'name': '%s %s as B' % (method, r['route']),
                'route': r['route'], 'attempt': {'as': 'b', 'method': method, 'path': path}}
        if expect:
            case['attempt']['expect'] = expect
        if '{id}' in path:
            case['id'] = value
        r88, skip, why = {}, [], []
        if public:
            r88['publicRead'] = 'a visitor with no session reads this page by design (public by default, CEO 26 Aug)'
        elif viewer:
            r88['publicRead'] = 'answers only about the signed-in person asking; B sees B\'s own row, a visitor is refused'
        if open_reason:
            skip.append('existence')
            why.append('%s %s is answered %s by design: %s' % (method, r['route'], expect[0], open_reason))
        if anybody:
            skip.append('methods')
            why.append('%s: %s' % (','.join(writes_ok) or 'the action', anybody))
        if public or r['params'][0] in PUBLIC_FIRST:
            skip.append('existence')
            why.append('the record named first is public, so whether it exists is no secret')
        if value == 'r88-none':
            skip.extend(['existence', 'neighbours'])
            why.append('no local record of this kind yet; the address is tried with a key that names nothing')
        if skip:
            r88['skip'] = sorted(set(skip))
            r88['reason'] = '; '.join(why)
        if r88:
            case['r88'] = r88
        cases.append(case)
    doc['cases'] = kept + cases
    doc['$comment'] = (doc.get('$comment', '').split(' Generated cases:')[0]
                       + ' Generated cases: tools/make-idor-cases.py from a probe of every id address'
                       ' (python manage.py idor_routes; tools/idor-probe.py), read by hand on'
                       ' 30 September 2026. The event of the day is %s.' % spec['event'])
    doc['$comment'] = doc['$comment'].replace('walk-con-ea5e', spec['event'])
    json.dump(doc, open(CASES, 'w', encoding='utf-8', newline='\n'), indent=1, ensure_ascii=False)
    open(CASES, 'a', encoding='utf-8').write('\n')
    print('%d hand cases kept, %d generated, %d faults' % (len(kept), len(cases), len(faults)))
    for f in faults:
        print('FAULT', f)
    sys.exit(1 if faults else 0)


if __name__ == '__main__':
    main()
