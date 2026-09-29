"""Make a VAPID key pair for browser push, and write it to the backend .env.

    python manage.py vapid_keys            print the PUBLIC key only
    python manage.py vapid_keys --write    append a new pair to .env (refuses if one exists)

The private key is written straight to `.env` and never printed, so it does not
end up in a terminal log or a chat. A new pair invalidates every existing
browser subscription, which is why --write refuses to replace one.
"""
import base64
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def new_pair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, 'big')
    public = key.public_key().public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)
    return _b64(public), _b64(private)


class Command(BaseCommand):
    help = 'Create VAPID keys for browser push.'

    def add_arguments(self, parser):
        parser.add_argument('--write', action='store_true')

    def handle(self, *args, write, **_):
        path = os.path.join(settings.BASE_DIR, '.env')
        existing = open(path, encoding='utf-8').read() if os.path.exists(path) else ''
        if not write:
            current = os.environ.get('VAPID_PUBLIC_KEY', '')
            self.stdout.write('VAPID_PUBLIC_KEY=%s' % (current or '(not set)'))
            return
        if 'VAPID_PRIVATE_KEY=' in existing:
            raise CommandError('A VAPID pair is already in .env. Replacing it would cut off every '
                               'browser that subscribed. Remove it by hand if that is intended.')
        public, private = new_pair()
        with open(path, 'a', encoding='utf-8') as fh:
            if existing and not existing.endswith('\n'):
                fh.write('\n')
            fh.write('# Browser push (vapid_keys, %s)\n' % __import__('datetime').date.today())
            fh.write('VAPID_PUBLIC_KEY=%s\nVAPID_PRIVATE_KEY=%s\n' % (public, private))
            fh.write('VAPID_SUBJECT=mailto:support@v-ent.co\n')
        self.stdout.write('Written. VAPID_PUBLIC_KEY=%s (restart the API to load it)' % public)
