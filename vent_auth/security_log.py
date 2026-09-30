"""Refusals worth seeing: a failed sign-in, a rate limit, a wrong PIN or code.

Owner rule R81: failed logins and refusals are logged, and no password,
token, cookie or code is ever in a log line. Every line here carries WHAT was
refused, the account number when there is one, and the address it came from,
and nothing anybody typed. `vent.security` goes to the same place gunicorn's
own log goes (journald on the box: `journalctl -u vent-api | grep vent.security`).

    security_log.refused('login_failed', request)
    security_log.refused('pin_wrong', user_id=wallet.user_id, left=3)
"""
import logging

log = logging.getLogger('vent.security')


def _ip(request):
    if request is None:
        return ''
    try:
        from .geo import client_ip
        return client_ip(request) or ''
    except Exception:                                       # noqa: BLE001
        return ''


def refused(event, request=None, *, user_id=None, **facts):
    """One line: the event, who (by number), from where, and plain facts.

    `facts` must be counts, codes and names of things, never anything a
    person typed: that is the whole promise of this module."""
    parts = ['event=%s' % event]
    if user_id is not None:
        parts.append('user=%s' % user_id)
    ip = _ip(request)
    if ip:
        parts.append('ip=%s' % ip)
    for key in sorted(facts):
        parts.append('%s=%s' % (key, facts[key]))
    log.warning(' '.join(parts))
