# -*- coding: utf-8 -*-
"""Words that depend on a number.

"Paid 4 VC across 3 lines", with the s in brackets, reached the organiser's
Money tab on 18 September 2026, and the sweep that followed found twenty-odd
more sentences of that shape a person can read: a notification title, the
guest checkout's refusals, the holds panel, the admin console's bulk actions.
A bracketed s is a sentence nobody finished writing. The frontend's
`scripts/check-plurals.mjs` reads both repos and refuses a new one; this is
what to write instead.

The server's own sentences are English. Anything with a code is translated
by the screen, which keeps its own one/many pair; these words are the
fallback and the notification inbox, which shows what the server wrote.
"""


def count(n, one, many=None):
    """'1 ticket', '3 tickets', '0 tickets'. `many` defaults to one + 's'."""
    n = int(n or 0)
    word = one if n == 1 else (many if many is not None else one + 's')
    return '%d %s' % (n, word)
