"""Mail claim links to pre-launch waitlist members.

This writes to real inboxes, so it defaults to doing nothing: without --send it
reports what it would do and stops. Sending is also resumable - a reservation
with `claim_sent_at` set is skipped unless --resend is passed - so an
interrupted run can be repeated without mailing anyone twice.

    python manage.py send_waitlist_claims                     # dry run
    python manage.py send_waitlist_claims --send --limit 5    # a real batch of 5
    python manage.py send_waitlist_claims --send              # everyone left
    python manage.py send_waitlist_claims --send --only-verified

--limit exists because sending 102 messages from a young domain in one burst is
how a domain gets rate limited. Small batch first, check Resend for bounces,
then the rest.
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from vent_auth import emails
from vent_auth.models import WaitlistReservation


class Command(BaseCommand):
    help = "Send claim links to unclaimed waitlist reservations."

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true',
                            help='Actually send. Without this nothing leaves the box.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Send at most this many, oldest queue position first')
        parser.add_argument('--only-verified', action='store_true',
                            help='Only addresses that confirmed themselves on the waitlist')
        parser.add_argument('--resend', action='store_true',
                            help='Include reservations already mailed once')
        parser.add_argument('--email', default=None,
                            help='Send to one address only. For testing the template.')
        parser.add_argument('--delay', type=float, default=0.5,
                            help='Seconds between messages (default 0.5)')

    def handle(self, *args, **options):
        send = options['send']
        hold_days = getattr(settings, 'WAITLIST_HOLD_DAYS', 90)
        frontend = getattr(settings, 'FRONTEND_URL', 'https://app.v-ent.co').rstrip('/')

        queryset = WaitlistReservation.objects.filter(
            claimed_at__isnull=True, claim_token__isnull=False)

        if options['email']:
            queryset = queryset.filter(email__iexact=options['email'])
        if options['only_verified']:
            queryset = queryset.filter(email_verified=True)
        if not options['resend']:
            queryset = queryset.filter(claim_sent_at__isnull=True)

        queryset = queryset.order_by('position')
        if options['limit']:
            queryset = queryset[:options['limit']]

        reservations = list(queryset)

        if not reservations:
            self.stdout.write('Nothing to send.')
            return

        if not send:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN. {len(reservations)} claim emails would be sent.'))
            for reservation in reservations[:10]:
                masked = self._mask(reservation.email)
                self.stdout.write(
                    f'  #{reservation.position:<4} {reservation.username or "(no username)":<20} {masked}')
            if len(reservations) > 10:
                self.stdout.write(f'  ... and {len(reservations) - 10} more')
            self.stdout.write('\nAdd --send to actually send.')
            return

        sent = failed = 0
        for reservation in reservations:
            claim_url = f'{frontend}/claim/{reservation.claim_token}'
            name = (reservation.display_name or reservation.username or 'there').split()[0]

            ok = emails.send_waitlist_claim(
                reservation.email,
                name=name,
                username=reservation.username,
                position=reservation.position,
                claim_url=claim_url,
                hold_days=hold_days,
            )

            if ok:
                reservation.claim_sent_at = timezone.now()
                reservation.save(update_fields=['claim_sent_at'])
                sent += 1
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(
                    f'  failed: {self._mask(reservation.email)}'))

            if options['delay']:
                time.sleep(options['delay'])

        self.stdout.write(self.style.SUCCESS(f'Sent {sent}. Failed {failed}.'))
        if failed:
            self.stdout.write('Failures kept claim_sent_at empty, so re-running retries only those.')

    @staticmethod
    def _mask(email):
        """Enough to recognise a row in a log, not enough to be an address list."""
        local, _, domain = email.partition('@')
        head = local[:2] if len(local) > 2 else local[:1]
        return f'{head}***@{domain}'
