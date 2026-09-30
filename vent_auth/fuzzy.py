"""Forgiving search: the closest matches, not only exact ones (inbox 383).

CEO, 30 September 2026, after "Winlo" answered "No account found" on the
Send page: "It should have brought up the closest versions to that name. I
need the search option across the entire site to be very very flexible. Any
search bar must work the same way that you don't have to type what you're
searching for correctly or fully."

One algorithm, written twice: here for every search endpoint, and in
src/lib/fuzzy.js for the filters that run in the browser. Both read the same
cases from fuzzy_fixtures.json (a copy lives in each repo and check-all
fails when they differ), so a name found on one screen is found on every
screen.

What counts as a match, strongest first:

    exact                              "winlola"        -> Winlola
    the start of the name              "winlo"          -> Winlola
    the start of any word              "kings"          -> Port Harcourt Kings
    anywhere inside                    "nlol"           -> Winlola
    ignoring spaces                    "portharcourt"   -> Port Harcourt Kings
    every word, each allowed a typo    "port harcort"   -> Port Harcourt Kings
    a typo or two                      "winlila"        -> Winlola
    the letters in order               "wnll"           -> Winlola

Case and accents never matter: "evenement" finds "Événement".
"""
import re
import unicodedata

from django.db.models import Q, QuerySet

#: Below this a candidate is not shown at all.
MIN_SCORE = 0.55

#: A table this size or smaller is ranked whole; a larger one is narrowed in
#: the database first by pieces of the query, which still admits a typo.
RANK_WHOLE_UP_TO = 5000

_NON_WORD = re.compile(r'[^0-9a-z]+')


def normalise(text):
    """Lower case, no accents, words separated by single spaces."""
    if text is None:
        return ''
    text = unicodedata.normalize('NFKD', str(text))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return _NON_WORD.sub(' ', text.casefold()).strip()


def _distance(a, b, cap):
    """Edits (insert, delete, change, swap two neighbours) from a to b, or cap+1 once past cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2 = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        low = cur[0]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
            low = min(low, cur[j])
        if low > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


def _allowed(word):
    """Typos forgiven for a query word of this length."""
    n = len(word)
    if n < 4:
        return 0
    return 1 if n <= 6 else 2


def _word_fits(qw, tw):
    """Edits needed for query word qw to be text word tw, or to begin it; None if too many."""
    cap = _allowed(qw)
    if tw.startswith(qw):
        return 0
    best = _distance(qw, tw, cap)
    if len(tw) > len(qw):
        best = min(best, _distance(qw, tw[:len(qw)], cap), _distance(qw, tw[:len(qw) + 1], cap))
    return best if best <= cap else None


def _in_order(q, t):
    it = iter(t)
    return all(c in it for c in q)


def score(query, text):
    """0.0 (no match) to 1.0 (exact), for one query against one piece of text."""
    q = normalise(query)
    t = normalise(text)
    if not q or not t:
        return 0.0
    if t == q:
        return 1.0
    if t.startswith(q):
        return 0.95
    words = t.split(' ')
    if any(w.startswith(q) for w in words):
        return 0.9
    if q in t:
        return 0.85
    q_flat, t_flat = q.replace(' ', ''), t.replace(' ', '')
    if len(q_flat) >= 3 and q_flat in t_flat:
        return 0.8
    q_words = q.split(' ')
    fits = []
    for qw in q_words:
        edits = [e for e in (_word_fits(qw, tw) for tw in words) if e is not None]
        if not edits:
            fits = None
            break
        fits.append(min(edits))
    if fits is not None:
        # Each typo costs a little, and so does each word the text has beyond
        # the query's, so "walk organser" puts walk_organiser above
        # walk_br_organiser.
        extra = max(0, len(words) - len(q_words))
        return max(MIN_SCORE, 0.75 - 0.05 * sum(fits) - 0.01 * min(extra, 5))
    if len(q_flat) >= 3 and q_flat[0] == t_flat[0] and _in_order(q_flat, t_flat):
        return 0.6
    return 0.0


def best_score(query, texts):
    return max((score(query, t) for t in texts if t), default=0.0)


def _value(obj, path):
    for part in path.split('__'):
        if obj is None:
            return None
        obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
    return obj


def _narrow(queryset, query, fields):
    """Candidates that share a piece of the query with one of the fields."""
    q = normalise(query).replace(' ', '')
    pieces = {q[i:i + 3] for i in range(max(1, len(q) - 2))} if len(q) >= 3 else {q}
    cond = Q()
    for field in fields:
        for piece in pieces:
            cond |= Q(**{f'{field}__icontains': piece})
    return queryset.filter(cond)


def search(items, query, fields, limit=None, min_score=MIN_SCORE):
    """Items ranked by how well any of `fields` matches `query`, best first.

    `items` is a QuerySet or any iterable of objects or dicts. `fields` are
    attribute paths (`owner__username` follows a relation). Ties keep the
    order the items came in, so a listing's own ordering (newest, most
    popular) decides between equally good matches.
    """
    q = (query or '').strip()
    if not q:
        return list(items[:limit] if limit else items)
    if isinstance(items, QuerySet) and items.count() > RANK_WHOLE_UP_TO:
        items = _narrow(items, q, fields)
    ranked = []
    for position, item in enumerate(items):
        s = best_score(q, [_value(item, f) for f in fields])
        if s >= min_score:
            ranked.append((-s, position, item))
    ranked.sort(key=lambda row: (row[0], row[1]))
    out = [item for _, _, item in ranked]
    return out[:limit] if limit else out


def filter(queryset, query, fields, min_score=MIN_SCORE):  # noqa: A001 - the queryset verb, on purpose
    """A QuerySet holding only the forgiving matches, best first.

    For views that page, count or filter further after searching: it drops
    in where `.filter(Q(x__icontains=q) | ...)` stood and keeps every
    QuerySet method working. A later `.order_by()` replaces relevance with
    that ordering, which is the view's decision to make.
    """
    from django.db.models import Case, IntegerField, Value, When
    q = (query or '').strip()
    if not q:
        return queryset
    ids = [obj.pk for obj in search(queryset, q, fields, min_score=min_score)]
    if not ids:
        return queryset.none()
    rank = Case(*[When(pk=pk, then=Value(i)) for i, pk in enumerate(ids)], output_field=IntegerField())
    return queryset.filter(pk__in=ids).annotate(_fuzzy_rank=rank).order_by('_fuzzy_rank')
