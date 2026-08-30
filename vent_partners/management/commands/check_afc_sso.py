"""Check an inbound provider's endpoints against its own discovery document.

The three URLs we sign people in through are configured with defaults read out
of AFC's integration guide. A guide is a document; a discovery document is what
the server currently believes, and the two drift. When they drift the failure
is quiet: the authorize URL still answers, the token exchange starts failing,
and the only visible symptom is people not being able to sign in.

    ./venv/bin/python manage.py check_afc_sso

Exits non-zero when something we rely on disagrees with what the provider
publishes, so it can be run before a deploy that turns the button on. A
provider that publishes no discovery document is reported and not treated as a
failure - the endpoints simply cannot be confirmed this way.
"""
import json
import urllib.error
import urllib.request

from django.core.management.base import BaseCommand

from vent_partners.views_sso import INBOUND_PROVIDERS, inbound_config


def _discovery(base):
    """The provider's discovery document, or None with a reason."""
    for suffix in ('/.well-known/openid-configuration',
                   '/../.well-known/openid-configuration'):
        url = base.rstrip('/') + suffix
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                if r.status == 200:
                    return json.loads(r.read()), url
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
            continue
    return None, None


class Command(BaseCommand):
    help = "Compare an inbound provider's configured endpoints with its discovery document."

    def add_arguments(self, parser):
        parser.add_argument('--provider', default='afc',
                            help='Which inbound provider to check. Default afc.')

    def handle(self, *args, **options):
        slug = options['provider']
        if slug not in INBOUND_PROVIDERS:
            self.stderr.write(self.style.ERROR('No inbound provider called %r.' % slug))
            raise SystemExit(2)

        cfg = inbound_config(slug)
        self.stdout.write('%s: credentials=%s enabled=%s' % (
            slug, cfg['credentials'], cfg['enabled']))

        # The issuer is the authorize URL's own directory: AFC mounts the whole
        # SSO surface under /sso/, and publishes discovery under that rather
        # than at the host root.
        base = cfg['authorize_url'].rsplit('/', 2)[0]
        doc, found_at = _discovery(base)
        if doc is None:
            self.stdout.write(self.style.WARNING(
                'No discovery document under %s. Endpoints cannot be confirmed this way.' % base))
            return

        self.stdout.write('discovery: %s' % found_at)

        problems = []
        for key, ours in (('authorization_endpoint', cfg['authorize_url']),
                          ('token_endpoint', cfg['token_url']),
                          ('userinfo_endpoint', cfg['userinfo_url'])):
            theirs = doc.get(key)
            if theirs and theirs.rstrip('/') != ours.rstrip('/'):
                problems.append('%s: we use %s, they publish %s' % (key, ours, theirs))
            else:
                self.stdout.write(self.style.SUCCESS('  %s ok' % key))

        supported = set(doc.get('scopes_supported') or [])
        if supported:
            missing = [s for s in cfg['scope'].split() if s not in supported]
            if missing:
                problems.append('scopes they no longer support: %s' % ' '.join(missing))
            else:
                self.stdout.write(self.style.SUCCESS('  scopes ok'))

        methods = set(doc.get('token_endpoint_auth_methods_supported') or [])
        # The exchange sends the secret in the form body, which is
        # client_secret_post. A provider that stopped accepting it would fail
        # every sign-in at the token step.
        if methods and 'client_secret_post' not in methods:
            problems.append('they no longer accept client_secret_post, which is how we authenticate')
        elif methods:
            self.stdout.write(self.style.SUCCESS('  client_secret_post ok'))

        if problems:
            for p in problems:
                self.stderr.write(self.style.ERROR('  %s' % p))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS('%s matches its discovery document.' % slug))
