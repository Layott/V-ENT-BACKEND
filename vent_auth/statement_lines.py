"""What a wallet history line says, as a code and its facts, for every language.

CEO, 30 September 2026 (inbox 388): wallet history lines such as "Sent to
@walk_organiser" were English sentences written on the server, so a French or
Portuguese reader saw English in the middle of their own page. "convert for
the different languages".

`Transaction.description` stays what it always was: the English sentence,
kept for the admin console, exports and anything that reads the column. This
module reads that sentence back into `{code, params}`, so the screen can say
it in the reader's language (`txn.<code>` in the dictionaries) with the names,
titles and numbers filled in untouched. Reading it back, rather than adding a
column every writer must remember to fill, is what makes it hold for the rows
already written and for any writer that only sets a description.

A sentence nothing here recognises comes back as `None`, and the screen shows
the description as written: a person's own note, an admin's typed reason, or a
new wording nobody has added yet. `tests_statement_lines` reads every wording
the code writes and fails on one this catalogue does not know, so the last
case stays a fault to fix and not a quiet fallback.
"""
import re

#: Wordings that wrap another, peeled off first and said after the main line.
#: Order matters: " - returned" is appended to a withdrawal that may itself
#: carry nothing else, and a fee note sits at the very end of its sentence.
_SUFFIXES = [
    ('returned', re.compile(r'^(?P<rest>.+) - returned(?:: (?P<reason>.+))?$', re.S)),
    ('serviceFee', re.compile(r'^(?P<rest>.+) \(incl\. (?P<fee>[\d.]+) VC service fee\)$', re.S)),
    ('platformFee', re.compile(r'^(?P<rest>.+) \(after a (?P<fee>[\d.]+) VC V-ENT fee\)$', re.S)),
]

#: (code, pattern). Specific before general: "Refund - cancelled tournament:"
#: before "Refund - ", "Order X cancelled at" before "Order X cancelled", and
#: the bare "X at Y" of a vendor slot last of all.
_LINES = [
    ('sent', r'Sent to (?P<to>.+?)(?:: (?P<note>.+))?'),
    ('received', r'Received from (?P<from>.+?)(?:: (?P<note>.+))?'),
    ('transferTo', r'To (?P<to>.+)'),
    ('transferFrom', r'From (?P<from>.+)'),
    ('topupProvider', r'Top up via (?P<provider>Paystack|Flutterwave)(?: - (?P<ngn>[\d.,]+) NGN)?'),
    ('topupCard', r'Top-up with (?P<brand>.+) ending (?P<last4>\w{2,4})'),
    ('cardCharge', r'(?P<brand>.+) ending (?P<last4>\w{2,4})'),
    ('premium', r'V-ENT premium, (?P<months>\d+) months?'),
    ('refundTournamentCancelled', r'Refund - cancelled tournament: (?P<title>.+)'),
    ('refundNoCheckin', r'Refund - did not check in: (?P<title>.+)'),
    ('refundEventCancelled', r'Refund - (?P<event>.+) cancelled: (?P<code>\S+)'),
    ('refund', r'Refund - (?P<what>.+)'),
    ('withdrawal', r'Withdrawal to (?P<bank>.+) (?P<account>\w{4,})'),
    ('membership', r'Membership - (?P<plan>.+)'),
    ('membershipsSettled', r'Memberships - (?P<plan>.+)'),
    ('settlement', r'Settlement - (?P<name>.+)'),
    ('prizeTopup', r'Prize top-up - (?P<title>.+)'),
    ('prizePayout', r'Prize payout - position (?P<position>\d+) - (?P<title>.+)'),
    ('registrationFee', r'Registration fee - (?P<title>.+)'),
    ('entryFee', r'Entry fee: (?P<title>.+)'),
    ('runnerUpPrize', r'Runner up prize: (?P<title>.+)'),
    ('marketplaceSale', r'Marketplace sale: (?P<title>.+)'),
    ('marketplaceRefund', r'Marketplace refund: (?P<title>.+)'),
    ('marketplace', r'Marketplace: (?P<title>.+)'),
    ('animeSubscription', r'Anime subscription: (?P<series>.+)'),
    ('animeChapter', r'Anime: (?P<series>.+) #(?P<number>[\w.]+)'),
    ('demoCoinsRemoved', r'Removed: coins that were never bought \(demo account\)'),
    ('tickets', r'(?P<quantity>\d+)x (?P<tier>.+?) - (?P<event>.+)'),
    ('orderAt', r'Order at (?P<vendor>.+)'),
    ('saleAt', r'Sale at (?P<vendor>.+)'),
    ('orderCancelledAt', r'Order (?P<code>\S+) cancelled at (?P<stall>.+)'),
    ('orderCancelled', r'Order (?P<code>\S+) cancelled'),
    ('slotRefunded', r'(?P<slot>.+) at (?P<event>.+) refunded'),
    ('slotSold', r'(?P<slot>.+) sold at (?P<event>.+)'),
    ('slotBought', r'(?P<slot>.+) at (?P<event>.+)'),
]
_COMPILED = [(code, re.compile('^%s$' % pattern, re.S)) for code, pattern in _LINES]

#: Every code a screen may be asked to translate, main lines and suffixes.
CODES = tuple(code for code, _ in _LINES) + tuple(code for code, _ in _SUFFIXES)


def _clean(groups):
    return {key: value for key, value in groups.items() if value is not None}


def parse(description):
    """`{code, params, suffixes}` for a known wording, else None.

    `suffixes` is a list of `{code, params}` said after the main line, such as
    a returned withdrawal's reason or a service fee included in a charge.
    """
    text = (description or '').strip()
    if not text:
        return None
    suffixes = []
    for code, pattern in _SUFFIXES:
        m = pattern.match(text)
        if m:
            text = m.group('rest')
            params = _clean({k: v for k, v in m.groupdict().items() if k != 'rest'})
            suffixes.insert(0, {'code': code, 'params': params})
    for code, pattern in _COMPILED:
        m = pattern.match(text)
        if m:
            return {'code': code, 'params': _clean(m.groupdict()), 'suffixes': suffixes}
    return None
