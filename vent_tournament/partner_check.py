"""Asking a partner to confirm one of their own usernames.

CEO: "if maybe there is a way to se things like this automated, so people
hosting events on the website can sutomatically verify users by maybe if its an
app, the install a v-ent feature on their app or site to check usernames or what
nots"

So: an organiser adds a `partner_verified` requirement naming a partner, the
entrant sends their username on that partner's service, and we ask the partner
whether it is real. The partner answers yes or no and we record it without
anybody reading anything.

Three things this is built around, in order of how much trouble each one saves.

**It falls back to a person, always.** No verification URL, a timeout, a
connection refused, a 500, a body that is not JSON, a JSON body in a shape we do
not recognise: every one of those leaves the submission `pending` for the
organiser to look at. Blocking a registration because somebody else's server is
down is not a trade worth making, and it is the failure that would be blamed on
us.

**It is asked at submission, not at render.** `evaluate()` runs every time the
page draws. An outbound HTTP call in there would put a partner's latency on the
critical path of a page load, and hit their server once per refresh.

**It never sends more than the answer needs.** The partner gets the field they
asked for and its value. Not the entrant's email, not their V-ENT id, not the
tournament. A partner confirming a username does not need to know who is asking
about whom.
"""
import logging

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

# Short on purpose. A person is waiting on the other side of this, and the
# fallback is a person reading it anyway, so there is nothing to gain by holding
# the request open.
TIMEOUT_SECONDS = 4


class Outcome:
    """What the partner said, or that they could not be asked.

    `checked` false means nothing was learned - not that the answer was no.
    Conflating the two is how "their server was slow" becomes "you are not who
    you say you are".
    """

    def __init__(self, checked, verified=False, reason='', detail=''):
        self.checked = checked
        self.verified = verified
        self.reason = reason
        self.detail = detail

    def __repr__(self):
        return 'Outcome(checked=%r, verified=%r, reason=%r)' % (
            self.checked, self.verified, self.reason)


def partner_for(name):
    """The partner a requirement names, if it is one we would actually call."""
    if not name:
        return None
    from vent_partners.models import Partner

    return (Partner.objects
            .filter(slug=str(name).strip().lower(), status='approved')
            .exclude(verification_url='')
            .first())


def ask(partner, field_label, value):
    """Ask a partner to confirm one of their own usernames.

    Returns an `Outcome`. Never raises: every failure is `checked=False`, which
    the caller turns into "waiting for the organiser".
    """
    if partner is None or not partner.verification_url:
        return Outcome(False, reason='no_partner')

    try:
        response = requests.post(
            partner.verification_url,
            json={
                'field': field_label,
                'value': value,
                'asked_at': timezone.now().isoformat(),
            },
            headers={
                'Content-Type': 'application/json',
                # The partner's own key, so they can tell it is us asking and
                # refuse anybody else. Sent as a bearer token because that is
                # what every other integration on this platform uses.
                'Authorization': 'Bearer %s' % partner.verification_secret,
                'User-Agent': 'V-ENT/1.0 (+https://v-ent.co)',
            },
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.info('partner verification unreachable for %s: %s', partner.slug, exc)
        return Outcome(False, reason='unreachable', detail=str(exc)[:200])

    if response.status_code >= 500:
        return Outcome(False, reason='partner_error',
                       detail='HTTP %s' % response.status_code)
    if response.status_code == 401 or response.status_code == 403:
        # Our own credential is wrong. Worth a log line that says so plainly,
        # because it will otherwise look like every entrant failing at once.
        logger.warning('partner %s refused our verification credential', partner.slug)
        return Outcome(False, reason='refused_us',
                       detail='HTTP %s' % response.status_code)
    if response.status_code >= 400:
        return Outcome(False, reason='bad_request',
                       detail='HTTP %s' % response.status_code)

    try:
        body = response.json()
    except ValueError:
        return Outcome(False, reason='not_json')

    if not isinstance(body, dict) or 'verified' not in body:
        # A 200 in a shape we do not recognise is not a yes. It is most often a
        # login page served with a 200, which is exactly the case that must not
        # read as approval.
        return Outcome(False, reason='unrecognised')

    verified = bool(body.get('verified'))
    return Outcome(
        True,
        verified=verified,
        reason='' if verified else 'not_found',
        # The partner's own words, shown to the entrant when they are refused,
        # because "the partner says no" is not something anybody can act on.
        detail=str(body.get('message') or '')[:300],
    )
