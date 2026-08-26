"""Prove every email the platform can send still renders and sends.

Run it after any template change, and before believing that a flow "sends an
email". It exercises all fifteen senders against throwaway objects, using
Django's in-memory backend by default so nobody receives anything, and reports
one line per email: whether it sent, its subject, and how big the HTML came out.

    python manage.py check_emails            # renders and sends to memory
    python manage.py check_emails --live you@example.com   # actually posts them

The in-memory run is the one to trust for "does this still work". The live run
is for looking at a design.
"""
from decimal import Decimal

from django.core import mail
from django.core.management.base import BaseCommand
from django.test import override_settings
from django.utils import timezone

from vent_auth import emails


class _Fake:
    """A stand-in with whatever attributes the caller asks for."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _sample_objects():
    """Objects shaped like the real ones, without touching the database."""
    user = _Fake(
        user_id=1, username='sample_player', full_name='Sample Player',
        email='sample@example.com', is_active=True, is_founding_member=True,
        founding_position=7, login_events=_Fake(first=lambda: _Fake(
            created_at=timezone.now(), city='Lagos', country='Nigeria',
            ip='102.89.221.5', user_agent='Mozilla/5.0 (Windows NT 10.0) Chrome/124',
        )),
    )
    tournament = _Fake(
        tournament_id=42, tournament_title='Naija Free Fire Weekly #12',
        start_date_and_time=timezone.now(), entry_fee='Paid',
        entry_fee_price=Decimal('500.00'),
        tournament_game=_Fake(game_title='Free Fire'), slug='naija-free-fire-weekly-12',
        bracket_type='battle_royale', tournament_type='online',
        tournament_location='Online', max_number_of_teams=32, team_size=4,
        tournament_creator=_Fake(username='organiser', full_name='Organiser'),
        prize_pool_coins=1000, prize_currency='NGN', prize_pool_total=Decimal('1000000.00'),
    )
    event = _Fake(
        event_id=5, name='V-ENT Lagos Meetup', location='Yaba, Lagos',
        start_date=timezone.now(), slug='v-ent-lagos-meetup',
    )
    ticket = _Fake(
        id=9, code='VENT-9F2K-118', price_vc=5,
        tier=_Fake(name='General Admission', price=Decimal('5000.00'), event=event),
        event=event, user=user, purchased_at=timezone.now(),
        attendee_email='sample@example.com', attendee_name='Sample Player',
    )
    withdrawal = _Fake(
        id=3, amount=250, bank_name='GTBank', account_number='0123456789',
        account_name='Sample Player', requested_at=timezone.now(),
        wallet=_Fake(user=user),
    )
    partner = _Fake(
        partner_id=2, name='African Free Fire Community', contact_name='AFC Team',
        contact_email='partners@example.com', status='approved', sso_status='requested',
        requested_scopes=['tournaments:read', 'teams:read'],
        approved_scopes=['tournaments:read'], review_note='Approved for tournament data.',
    )
    return user, tournament, ticket, withdrawal, partner


def _cases():
    user, tournament, ticket, withdrawal, partner = _sample_objects()
    to = 'sample@example.com'
    return [
        ('signup verification', lambda: emails.send_verify_email(to, name='Sample', code='123456')),
        ('signup verification (resend)',
         lambda: emails.send_verify_email(to, name='Sample', code='123456', resend=True)),
        ('welcome', lambda: emails.send_welcome(to, name='Sample')),
        ('password reset', lambda: emails.send_password_reset(to, name='Sample', code='654321')),
        ('password reset (resend)',
         lambda: emails.send_password_reset(to, name='Sample', code='654321', resend=True)),
        ('new email verification',
         lambda: emails.send_verify_new_email(to, name='Sample', code='222333',
                                              old_email='old@example.com')),
        ('waitlist welcome', lambda: emails.send_waitlist_welcome(to)),
        ('waitlist claim',
         lambda: emails.send_waitlist_claim(to, name='Sample', username='sample_player',
                                            position=7, claim_url='https://v-ent.co/claim/token',
                                            hold_days=90)),
        ('tournament registered',
         lambda: emails.send_tournament_registered(user, tournament, entry_paid_vc=500)),
        ('ticket purchased', lambda: emails.send_ticket_purchased(ticket)),
        ('payout approved',
         lambda: emails.send_payout_approved(withdrawal, amount_ngn=250000)),
        ('payout rejected',
         lambda: emails.send_payout_rejected(withdrawal, reason='Bank details did not match.')),
        ('kyc approved', lambda: emails.send_kyc_approved(user)),
        ('kyc rejected', lambda: emails.send_kyc_rejected(user, reason='Document was unreadable.')),
        ('new sign-in alert', lambda: emails.send_login_alert(user)),
        ('partner application received',
         lambda: emails.send_partner_application_received(partner)),
        ('partner decision', lambda: emails.send_partner_decision(partner)),
    ]


class Command(BaseCommand):
    help = 'Render and send every email the platform can send, and report on each.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--live', metavar='ADDRESS',
            help='Actually send them, to this address, through the configured relay.',
        )

    def handle(self, *args, **options):
        live = options.get('live')
        cases = _cases()

        if live:
            for name, sender in cases:
                self._report(name, sender(), None)
            self.stdout.write(self.style.WARNING(
                f'\nSent live to {live}. Check the relay log for status=sent.'))
            return

        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            mail.outbox = []
            failures = 0
            for name, sender in cases:
                before = len(mail.outbox)
                ok = sender()
                message = mail.outbox[-1] if len(mail.outbox) > before else None
                if not ok or message is None:
                    failures += 1
                self._report(name, ok, message)

            total = len(cases)
            summary = f'{total - failures} of {total} emails rendered and sent'
            style = self.style.SUCCESS if failures == 0 else self.style.ERROR
            self.stdout.write(style(f'\n{summary}.'))
            if failures:
                raise SystemExit(1)

    def _report(self, name, ok, message):
        if not ok:
            self.stdout.write(self.style.ERROR(f'  FAILED   {name}'))
            return
        if message is None:
            self.stdout.write(self.style.SUCCESS(f'  sent     {name}'))
            return
        html = ''
        for content, mimetype in getattr(message, 'alternatives', []) or []:
            if mimetype == 'text/html':
                html = content
        self.stdout.write(self.style.SUCCESS(
            f'  sent     {name:32} {len(html):>6} bytes  "{message.subject}"'))
