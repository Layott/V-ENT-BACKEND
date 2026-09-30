"""Try every id-taking address as another user and as a visitor (owner rule R88).

    python manage.py idor_routes > routes.json
    python tools/idor-probe.py routes.json results.json [http://127.0.0.1:8000]

LOCAL ONLY: it sends every method the view takes, DELETE included, as user B
(walk_buyer_ada) against records user A owns. Back up local-dev.sqlite3 first
and restore it after; a write that lands is exactly what this is looking for.

Writes one row per (address, method, who) with the status, then prints the
ones that answered 2xx, which are read by hand: either the address is public
or an action any signed-in person may take by design, or it is a fault.
"""
import json
import sys
import urllib.error
import urllib.request

ROUTES, OUT = sys.argv[1], sys.argv[2]
BASE = (sys.argv[3] if len(sys.argv) > 3 else 'http://127.0.0.1:8000').rstrip('/')
ORDER = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None and method != 'GET' else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer %s' % token)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read()[:300]
    except urllib.error.HTTPError as err:
        return err.code, err.read()[:300]
    except Exception as err:  # a timeout or a dropped connection is a result too
        return 0, str(err).encode()


def main():
    spec = json.load(open(ROUTES, encoding='utf-8'))
    status, body = call('POST', '/auth/login/', body={
        'username_or_email': spec['b'], 'password': 'walk-con-2026'})
    token = json.loads(body or b'{}').get('data', {}).get('session_token') if status == 200 else None
    if not token:
        token = json.loads(body or b'{}').get('session_token')
    if not token:
        sys.exit('could not sign in as %s: %s %s' % (spec['b'], status, body))
    rows = []
    for r in spec['routes']:
        # Every method, not only the ones the view declares: a function view
        # declares nothing, and which ones answer 405 is itself the answer.
        methods = ORDER
        for method in methods:
            for who, tok in (('b', token), ('anon', None)):
                code, text = call(method, r['path'], tok, {} if method != 'GET' else None)
                rows.append({'route': r['route'], 'path': r['path'], 'view': r['view'],
                             'method': method, 'who': who, 'status': code,
                             'body': text.decode('utf-8', 'replace')[:200]})
    json.dump(rows, open(OUT, 'w', encoding='utf-8'), indent=1)
    ok = [x for x in rows if 200 <= x['status'] < 300]
    errors = [x for x in rows if x['status'] >= 500 or x['status'] == 0]
    print('%d requests, %d answered 2xx, %d answered 5xx or nothing' % (len(rows), len(ok), len(errors)))


if __name__ == '__main__':
    main()
