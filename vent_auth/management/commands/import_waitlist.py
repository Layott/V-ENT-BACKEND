"""Import the pre-launch waitlist into the platform.

The waitlist ran on a separate site backed by Supabase. Rather than have the
platform query that database at sign-in time, the rows are copied here once and
the platform owns them from then on: if the marketing site is ever retired,
nobody loses the ability to claim.

Feed it a JSON array exported from `waitlist_entries`, either the raw PostgREST
response or anything with the same field names:

    python manage.py import_waitlist waitlist.json
    python manage.py import_waitlist waitlist.json --dry-run

Re-running is safe. Rows are matched on email, already-claimed rows are left
untouched, and a claim token is only minted for a row that does not have one.
"""
import json
import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from django.conf import settings
from vent_auth.models import WaitlistReservation


class Command(BaseCommand):
    help = "Import waitlist entries from a JSON export and mint claim tokens."

    def add_arguments(self, parser):
        parser.add_argument('path', help='Path to the JSON export of waitlist_entries')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change and write nothing')
        parser.add_argument('--hold-days', type=int, default=None,
                            help='Override WAITLIST_HOLD_DAYS for this import')

    def handle(self, *args, **options):
        path = options['path']
        dry_run = options['dry_run']
        hold_days = options['hold_days'] or getattr(settings, 'WAITLIST_HOLD_DAYS', 90)

        try:
            with open(path, encoding='utf-8') as handle:
                rows = json.load(handle)
        except (OSError, ValueError) as exc:
            raise CommandError(f'Could not read {path}: {exc}')

        if not isinstance(rows, list):
            raise CommandError('Expected a JSON array of waitlist entries')

        hold_until = timezone.now() + timedelta(days=hold_days)
        created = updated = skipped_claimed = 0
        tokens_minted = 0

        with transaction.atomic():
            for row in rows:
                email = (row.get('email') or '').strip().lower()
                if not email:
                    continue

                reservation = WaitlistReservation.objects.filter(email=email).first()

                if reservation and reservation.is_claimed:
                    # Their account exists. Re-importing must not resurrect a
                    # spent token or move a hold that no longer applies.
                    skipped_claimed += 1
                    continue

                if reservation is None:
                    reservation = WaitlistReservation(email=email)
                    created += 1
                else:
                    updated += 1

                username = (row.get('username') or '').strip().lower() or None
                reservation.username = username
                reservation.display_name = (row.get('display_name') or '').strip()
                reservation.game = (row.get('game') or '').strip()
                reservation.country = (row.get('country') or '').strip()
                reservation.position = int(row.get('position') or 0)
                reservation.referral_code = (row.get('referral_code') or '').strip()
                reservation.boost_count = int(row.get('boost_count') or 0)
                reservation.email_verified = bool(row.get('email_verified'))
                reservation.source_id = row.get('id')
                reservation.hold_expires_at = hold_until

                if not reservation.claim_token:
                    # 32 bytes of urlsafe randomness. The token is the only thing
                    # standing between a stranger and somebody's account, so it
                    # is generated here and never derived from anything guessable
                    # like the email or the queue position.
                    reservation.claim_token = secrets.token_urlsafe(32)
                    tokens_minted += 1

                if not dry_run:
                    reservation.save()

            if dry_run:
                transaction.set_rollback(True)

        total = WaitlistReservation.objects.count() if not dry_run else created + updated
        self.stdout.write(self.style.SUCCESS(
            f"{'Would import' if dry_run else 'Imported'}: "
            f"{created} new, {updated} updated, {skipped_claimed} already claimed (left alone), "
            f"{tokens_minted} claim tokens minted."))
        self.stdout.write(f"Usernames held until {hold_until:%Y-%m-%d} ({hold_days} days).")
        if not dry_run:
            self.stdout.write(f"Reservations in the database: {total}")
            self.stdout.write("Next: python manage.py send_waitlist_claims --dry-run")
