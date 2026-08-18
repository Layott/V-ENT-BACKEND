"""Move already-uploaded KYC documents out of the public media tree.

Before migration 0035 these files were written to MEDIA_ROOT/kyc/, which nginx
serves openly. This walks every KYCDocument, copies the file to
PRIVATE_MEDIA_ROOT/kyc/ and removes the public copy. Safe to run repeatedly.

    python manage.py move_kyc_private            # do it
    python manage.py move_kyc_private --dry-run  # just report
"""
import os
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand

from vent_auth.models import KYCDocument


class Command(BaseCommand):
    help = 'Move KYC identity documents from MEDIA_ROOT into PRIVATE_MEDIA_ROOT.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry = options['dry_run']
        moved = missing = already = 0

        for doc in KYCDocument.objects.exclude(document_image=''):
            name = doc.document_image.name              # "kyc/passport_7.jpg"
            public = os.path.join(settings.MEDIA_ROOT, name)
            private = os.path.join(settings.PRIVATE_MEDIA_ROOT, name)

            if os.path.exists(private):
                already += 1
                if os.path.exists(public):
                    self.stdout.write(f'  removing stale public copy: {name}')
                    if not dry:
                        os.remove(public)
                continue

            if not os.path.exists(public):
                missing += 1
                self.stdout.write(self.style.WARNING(f'  missing on disk: {name}'))
                continue

            self.stdout.write(f'  {name} -> private/')
            if not dry:
                os.makedirs(os.path.dirname(private), exist_ok=True)
                shutil.move(public, private)
                os.chmod(private, 0o640)
            moved += 1

        # Drop the now-empty public kyc directory so nothing can land back in it.
        public_dir = os.path.join(settings.MEDIA_ROOT, 'kyc')
        if not dry and os.path.isdir(public_dir) and not os.listdir(public_dir):
            os.rmdir(public_dir)

        verb = 'would move' if dry else 'moved'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {moved}, already private {already}, missing {missing}'
        ))
