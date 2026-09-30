"""Wallet history lines read back into a code and its facts (inbox 388).

CEO, 30 September 2026: history lines were English sentences written on the
server; "convert for the different languages". The screen translates by the
code, so a wording this catalogue does not know stays English. The last test
reads every wording the code writes and fails on one that is not known.
"""
import pathlib
import re

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from vent_auth import statement_lines, wallets
from vent_auth.tests_wallet_flows import PIN, _user

#: One real sentence per code, as the writers produce it.
SAMPLES = {
    'sent': ('Sent to @walk_organiser', {'to': '@walk_organiser'}),
    'received': ('Received from @demo_temi: for the match', {'from': '@demo_temi', 'note': 'for the match'}),
    'transferTo': ('To Jesus Team', {'to': 'Jesus Team'}),
    'transferFrom': ('From @ada', {'from': '@ada'}),
    'topupProvider': ('Top up via Flutterwave - 5000 NGN', {'provider': 'Flutterwave', 'ngn': '5000'}),
    'topupCard': ('Top-up with visa ending 4081', {'brand': 'visa', 'last4': '4081'}),
    'cardCharge': ('Mastercard ending 1234', {'brand': 'Mastercard', 'last4': '1234'}),
    'premium': ('V-ENT premium, 3 months', {'months': '3'}),
    'refundTournamentCancelled': ('Refund - cancelled tournament: Naija Weekly', {'title': 'Naija Weekly'}),
    'refundNoCheckin': ('Refund - did not check in: Naija Weekly', {'title': 'Naija Weekly'}),
    'refundEventCancelled': ('Refund - Lagos Anime Con cancelled: VT-7K2', {'event': 'Lagos Anime Con', 'code': 'VT-7K2'}),
    'refund': ('Refund - Crest Club', {'what': 'Crest Club'}),
    'withdrawal': ('Withdrawal to GTBank 0123', {'bank': 'GTBank', 'account': '0123'}),
    'membership': ('Membership - Crest Club', {'plan': 'Crest Club'}),
    'membershipsSettled': ('Memberships - Crest Club', {'plan': 'Crest Club'}),
    'settlement': ('Settlement - Lagos Anime Con', {'name': 'Lagos Anime Con'}),
    'prizeTopup': ('Prize top-up - Naija Weekly', {'title': 'Naija Weekly'}),
    'prizePayout': ('Prize payout - position 2 - Naija Weekly', {'position': '2', 'title': 'Naija Weekly'}),
    'registrationFee': ('Registration fee - Naija Weekly', {'title': 'Naija Weekly'}),
    'entryFee': ('Entry fee: PUBG Mobile Naija Open', {'title': 'PUBG Mobile Naija Open'}),
    'runnerUpPrize': ('Runner up prize: PUBG Mobile Naija Open', {'title': 'PUBG Mobile Naija Open'}),
    'marketplaceSale': ('Marketplace sale: Signed jersey', {'title': 'Signed jersey'}),
    'marketplaceRefund': ('Marketplace refund: Signed jersey', {'title': 'Signed jersey'}),
    'marketplace': ('Marketplace: Signed jersey', {'title': 'Signed jersey'}),
    'animeSubscription': ('Anime subscription: Blade Road', {'series': 'Blade Road'}),
    'animeChapter': ('Anime: Blade Road #12', {'series': 'Blade Road', 'number': '12'}),
    'demoCoinsRemoved': ('Removed: coins that were never bought (demo account)', {}),
    'tickets': ('2x VIP - Lagos Anime Con', {'quantity': '2', 'tier': 'VIP', 'event': 'Lagos Anime Con'}),
    'orderAt': ('Order at Mama Put', {'vendor': 'Mama Put'}),
    'saleAt': ('Sale at Mama Put', {'vendor': 'Mama Put'}),
    'orderCancelledAt': ('Order QX7 cancelled at Mama Put', {'code': 'QX7', 'stall': 'Mama Put'}),
    'orderCancelled': ('Order QX7 cancelled', {'code': 'QX7'}),
    'slotRefunded': ('Food stall at Lagos Anime Con refunded', {'slot': 'Food stall', 'event': 'Lagos Anime Con'}),
    'slotSold': ('Food stall sold at Lagos Anime Con', {'slot': 'Food stall', 'event': 'Lagos Anime Con'}),
    'slotBought': ('Food stall at Lagos Anime Con', {'slot': 'Food stall', 'event': 'Lagos Anime Con'}),
}


class CatalogueTests(SimpleTestCase):
    def test_every_code_has_a_sample(self):
        main = set(code for code, _ in statement_lines._LINES)
        self.assertEqual(set(SAMPLES), main)

    def test_each_sample_reads_back(self):
        for code, (sentence, params) in SAMPLES.items():
            with self.subTest(code=code):
                line = statement_lines.parse(sentence)
                self.assertIsNotNone(line, sentence)
                self.assertEqual(line['code'], code)
                self.assertEqual(line['params'], params)

    def test_suffixes(self):
        line = statement_lines.parse('Withdrawal to GTBank 0123 - returned: KYC not approved')
        self.assertEqual(line['code'], 'withdrawal')
        self.assertEqual(line['suffixes'], [{'code': 'returned', 'params': {'reason': 'KYC not approved'}}])
        line = statement_lines.parse('Registration fee - Naija Weekly (incl. 5 VC service fee)')
        self.assertEqual((line['code'], line['suffixes'][0]),
                         ('registrationFee', {'code': 'serviceFee', 'params': {'fee': '5'}}))
        line = statement_lines.parse('Anime: Blade Road #12 (after a 1 VC V-ENT fee)')
        self.assertEqual(line['code'], 'animeChapter')
        self.assertEqual(line['suffixes'][0]['params'], {'fee': '1'})

    def test_free_text_is_left_alone(self):
        for text in ('', None, 'for the match on Saturday', 'Goodwill'):
            with self.subTest(text=text):
                self.assertIsNone(statement_lines.parse(text))


class ServedTests(TestCase):
    def test_history_and_shared_statement_carry_the_line(self):
        me, wallet = _user('slsender', balance=5)
        ada, ada_w = _user('slada', balance=0)
        wallets.transfer(wallet, ada_w, 2, debit_kind='send', credit_kind='receive',
                         debit_note='Sent to @slada', credit_note='Received from @slsender')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer %s' % me.login_session_token)
        row = client.get('/auth/wallet/transactions/').json()['data']['transactions'][0]
        self.assertEqual(row['line']['code'], 'sent')
        self.assertEqual(row['line']['params'], {'to': '@slada'})
        self.assertEqual(row['description'], 'Sent to @slada')
        self.assertEqual(wallets.statement(ada_w)[0]['line']['code'], 'received')


# ---------------------------------------------------------------------------
# The catcher: every wording written into Transaction.description is known.
# ---------------------------------------------------------------------------

#: Calls whose text ends up in Transaction.description.
_WRITERS = re.compile(r'Transaction\.objects\.create\(|\b(?:debit|credit|_credit|_move|transfer|_collect_from_\w+)\(|'
                      r'held\.description\s*=')
#: The first literal after a description-carrying argument, or a literal
#: that starts its own line (a positional argument), or a constant named as
#: the description.
#: Before the literal only a fallback chain may stand ("debit_note or note or
#: ("), never another argument, or `description=x, status='completed'` would
#: read the status as the wording.
_KWARG = re.compile(r"""\b(?:description|debit_note|credit_note|note)\s*=\s*(?:[\w.]+\s+or\s+)*\(?\s*f?(['"])(?P<text>.*?)(?<!\\)\1""")
_POSITIONAL = re.compile(r"""^\s+f?(['"])(?P<text>[A-Z][^'"]*?(?:%[sd]|\{)[^'"]*)\1""")
_CONSTANT_USE = re.compile(r'\bdescription\s*=\s*(?P<name>[A-Z][A-Z_]+)\b')
#: Text on these lines goes somewhere other than a history line.
_NOT_HISTORY = ('EventLedgerEntry', 'note=reason', 'premium_note', 'Error(', 'create_notification',
                'request.data', 'title=')


def _templates():
    root = pathlib.Path(__file__).resolve().parent.parent
    found = []
    for path in sorted(root.rglob('*.py')):
        parts = set(path.parts)
        if parts & {'migrations', 'tools', '.venv', 'venv'} or path.name.startswith('tests') \
                or path.name in ('seed_demo.py', 'statement_lines.py'):
            continue
        source = path.read_text(encoding='utf-8', errors='ignore')
        lines = source.splitlines()
        for number, line in enumerate(lines, 1):
            near = '\n'.join(lines[max(0, number - 9):number + 8])
            if not _WRITERS.search(near):
                continue
            own = '\n'.join(lines[max(0, number - 2):number])
            if any(word in own for word in _NOT_HISTORY):
                continue
            m = _KWARG.search(line)
            if not m:
                # A literal on its own line counts only as an argument of a
                # writer opened just above it, never of a refusal.
                for back in range(number - 2, max(-1, number - 5), -1):
                    if _WRITERS.search(lines[back]):
                        between = '\n'.join(lines[back:number - 1])
                        if not re.search(r'Error|_error\(|raise |Response\(', between):
                            m = _POSITIONAL.search(line)
                        break
            if m:
                found.append(('%s:%d' % (path.relative_to(root), number), m.group('text')))
            c = _CONSTANT_USE.search(line)
            if c:
                value = re.search(r"""^%s\s*=\s*(['"])(?P<text>.*?)\1""" % c.group('name'), source, re.M)
                if value:
                    found.append(('%s:%d' % (path.relative_to(root), number), value.group('text')))
    return found


def _filled(template, sample):
    # A placeholder glued to a word is a plural ending ("month%s"): empty.
    text = re.sub(r'(?<=[a-z])%s', '', template)
    text = re.sub(r'\{[^}]*\}', sample, text)
    return re.sub(r'%[sd]', sample, text)


def _known(template):
    for sample in ('7777', 'Sample Name'):
        filled = _filled(template, sample)
        if statement_lines.parse(filled):
            return True
        # A wrapper around a line written elsewhere ("%s - returned%s"): the
        # suffix is known and the rest is whatever that line already said.
        for _code, pattern in statement_lines._SUFFIXES:
            m = pattern.match(filled)
            if m and m.group('rest') == sample:
                return True
    return False


class EveryWordingKnownTests(SimpleTestCase):
    def test_every_written_wording_is_in_the_catalogue(self):
        unknown = ['%s  %r' % (where, text) for where, text in _templates()
                   # A bare placeholder is somebody's own note or a
                   # description handed down by the caller, read where it is
                   # built.
                   if _filled(text, '').strip(' :-') and not _known(text)]
        self.assertEqual(unknown, [])

    def test_the_scan_sees_the_writers(self):
        # Calibration: the scan must find the wordings it exists to hold. If
        # it found nothing, a passing test would mean nothing.
        found = ' | '.join(text for _where, text in _templates())
        for wording in ('Sent to %s%s', 'Refund - did not check in:', 'Top up via Paystack - ',
                        'Withdrawal to %s %s', 'Settlement - %s', 'Marketplace sale: %s',
                        'Anime: %s #%s', 'Top-up with %s ending %s', 'Membership - %s', 'To %s',
                        'From %s', 'Removed: coins that were never bought'):
            with self.subTest(wording=wording):
                self.assertIn(wording, found)

    def test_an_unknown_wording_fails(self):
        self.assertFalse(_known('Bonus from the house: %s'))
        self.assertTrue(_known('Refund - did not check in: {tournament.tournament_title}'))
