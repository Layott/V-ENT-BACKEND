"""Which currencies the live Flutterwave account can take (inbox 361).

    python manage.py flw_currencies            probe every currency, record the ones accepted
    python manage.py flw_currencies --show     print what is recorded, change nothing

A currency is offered to payers only after Flutterwave has accepted a checkout
in it on THIS account: a rate existing says nothing about whether the account
may collect in that currency. The probe asks for a hosted checkout link for
20,000 naira's worth with that currency's methods; a link is not a charge, and
an unused link expires on its own. The answer is recorded in the admin
settings under `flutterwave.currencies` with the date, and NGN is always on.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_auth import flutterwave
from vent_auth import flutterwave_currency as fx
from vent_auth.models import AdminSetting


class Command(BaseCommand):
    help = 'Probe the live Flutterwave account for the currencies it can collect in.'

    def add_arguments(self, parser):
        parser.add_argument('--show', action='store_true')

    def handle(self, *args, **opts):
        stored = (AdminSetting.load().data or {}).get(fx.SETTING_SECTION, {})
        if opts['show']:
            self.stdout.write('recorded: %s (checked %s)' % (
                ', '.join(fx.enabled()), stored.get('checked_at') or 'never'))
            return
        if not flutterwave.configured():
            self.stderr.write('Flutterwave is not configured here; nothing probed.')
            return

        accepted, refused = [], {}
        for code in fx.CURRENCIES:
            if code == 'NGN':
                continue
            try:
                per_unit = fx.rate(code)
                # 20,000 naira's worth: 1,000 was under the 1.00 floor in USD,
                # GBP and EUR, which Flutterwave refuses as "parameters missing"
                # and which read as the currency itself being refused.
                amount = max(fx.amount_in(code, Decimal('20000'), per_unit),
                             fx.MINIMUM.get(code, Decimal('0')))
                flutterwave._call('POST', '/payments', {
                    'tx_ref': flutterwave.new_reference('PROBE'),
                    'amount': str(amount),
                    'currency': code,
                    'redirect_url': 'https://v-ent.co/',
                    'customer': {'email': 'probe@v-ent.co', 'name': 'V-ENT currency check'},
                    'customizations': {'title': 'V-ENT', 'description': 'Currency check'},
                    'payment_options': ', '.join(fx.CURRENCIES[code]['methods'].split(',')),
                })
                accepted.append(code)
                self.stdout.write('%s accepted (%s %s for 20,000 NGN)' % (code, amount, code))
            except (flutterwave.Refused, fx.CurrencyError) as exc:
                refused[code] = str(exc)[:120]
                self.stdout.write('%s refused: %s' % (code, str(exc)[:120]))
            except flutterwave.Unreachable as exc:
                refused[code] = 'unreachable'
                self.stdout.write('%s not checked, Flutterwave did not answer: %s' % (code, str(exc)[:80]))

        AdminSetting.put(fx.SETTING_SECTION, currencies=accepted,
                         refused=refused, checked_at=timezone.now().isoformat())
        self.stdout.write('recorded: %s' % ', '.join(['NGN'] + accepted))
