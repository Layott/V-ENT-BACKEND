"""Every email V-ENT sends, named.

Views used to build their own HTML inline, which is how the platform ended up
sending `<p>Hi,</p>` with a broken image tag pointing at a host that no longer
exists. Each message now has one function here, so a view says what it wants to
send and never how it looks, and the eleven templates under `templates/emails/`
are the only place the design lives.

Nothing in this module raises. A verification code that reaches the database but
not the inbox is recoverable (the user asks for a resend); a signup that 500s
because the mail relay hiccuped is not.
"""
import logging
import os
import re

from django.conf import settings

logger = logging.getLogger(__name__)

# Every link and every host name printed in a message comes from here, so the
# day the platform moves host there is one value to change. FRONTEND_URL is
# guarded in settings, so this cannot end up pointing at a dead host.
APP_URL = getattr(settings, 'FRONTEND_URL', 'https://v-ent.co').rstrip('/')
APP_HOST = APP_URL.split('://', 1)[-1]

# The V-ENT mark, embedded in every message rather than linked.
#
# A remote <img> is the easy version and the wrong one: most clients block
# remote images until the reader opts in, so the first thing anyone sees from us
# would be a broken box, and it makes the mail depend on the app being up.
# Attaching the file as a related part with a Content-ID means the image travels
# inside the message and renders on open. Resend is only the relay here - it
# forwards the MIME we build, so this works the same through any SMTP path.
LOGO_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'email_logo.png')
LOGO_CID = 'ventlogo'
_logo_bytes = None


def _logo():
    """The logo file, read once. None if it is missing, which is not fatal."""
    global _logo_bytes
    if _logo_bytes is None:
        try:
            with open(LOGO_PATH, 'rb') as handle:
                _logo_bytes = handle.read()
        except OSError:
            logger.warning('email logo missing at %s; sending without it', LOGO_PATH)
            _logo_bytes = b''
    return _logo_bytes or None


def _plain_text(html):
    """A readable text/plain part.

    EmailMultiAlternatives was being handed the HTML as its plain body, so any
    client preferring text got a wall of table markup. Stripping tags is crude
    but it leaves the code, the amount, and the link readable, which is all the
    text part has to do.
    """
    text = re.sub(r'(?is)<(script|style|head).*?</\1>', '', html)
    text = re.sub(r'(?i)<br\s*/?>|</(p|tr|div|h1|h2|table)>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;|&#847;|&zwnj;', ' ', text)
    text = re.sub(r'&copy;', '(c)', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    return text.strip()


def _send(to_address, subject, template, context):
    """Render `template` and send it. Returns True on success, never raises."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    context = dict(
        context,
        logo_cid=LOGO_CID if _logo() else '',
        app_url=APP_URL,
        app_host=APP_HOST,
    )

    try:
        html = render_to_string(f'emails/{template}', context)
    except Exception:
        logger.exception('email template %r failed to render for %s', template, to_address)
        return False

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=_plain_text(html),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            to=[to_address],
        )
        message.attach_alternative(html, 'text/html')

        logo = _logo()
        if logo:
            from email.mime.image import MIMEImage

            # multipart/related wraps the alternative parts and the image, which
            # is what lets `cid:` resolve. Marked inline so no client lists the
            # logo as an attachment.
            message.mixed_subtype = 'related'
            image = MIMEImage(logo, 'png')
            image.add_header('Content-ID', f'<{LOGO_CID}>')
            image.add_header('Content-Disposition', 'inline', filename='v-ent.png')
            message.attach(image)

        message.send(fail_silently=False)
        logger.info('sent %r to %s', template, to_address)
        return True
    except Exception:
        logger.exception('send failed: %r to %s', template, to_address)
        return False


def _expiry_label():
    """How long a verification token lasts, in the words the email uses."""
    minutes = getattr(settings, 'VERIFICATION_TOKEN_MINUTES', 120)
    hours = minutes // 60
    if hours >= 1:
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{minutes} minutes"


def _first_name(user):
    full = (getattr(user, 'full_name', '') or '').strip()
    if full:
        return full.split()[0]
    return getattr(user, 'username', None) or 'there'


# ---------------------------------------------------------------------------
# Getting in
# ---------------------------------------------------------------------------

def send_verify_email(to_address, *, name, code=None, verify_url=None, resend=False):
    """Confirm an address, by typed code or by one-tap link.

    Two flows share this message and they are not interchangeable. Signup
    verifies by link, so it passes `verify_url` and no code - its token is a
    URL token, and printing one as a code hands the reader forty characters
    with no screen to type them into. The email-first flow passes a real
    six-digit `code`. Whichever arrives is the one the email shows.
    """
    subject = 'Confirm your email to finish signing up'
    if resend:
        subject = 'Your V-ENT verification link' if verify_url else 'Your V-ENT verification code'
    return _send(
        to_address,
        subject,
        'verify_email.html',
        {'name': name, 'code': code, 'expires_in': _expiry_label(), 'verify_url': verify_url},
    )


def send_welcome(to_address, *, name):
    return _send(to_address, 'Welcome to V-ENT', 'welcome.html', {'name': name})


def send_waitlist_claim(to_address, *, name, username, position, claim_url, hold_days):
    """Launch-day invitation to a pre-launch reserver.

    Promises exactly two things, because both are free: the username they
    reserved, and a permanent founding-member number. No coin bonus is mentioned
    - see WAITLIST_CLAIM_BONUS_VC, which is 0 by decision.
    """
    return _send(
        to_address,
        'V-ENT is open. Claim your account',
        'waitlist_claim.html',
        {
            'name': name,
            'username': username,
            'position': position,
            'claim_url': claim_url,
            'hold_days': hold_days,
        },
    )


def send_password_reset(to_address, *, name, code, reset_url=None, resend=False):
    return _send(
        to_address,
        'Your new V-ENT password reset code' if resend else 'Your V-ENT password reset code',
        'reset_password.html',
        {'name': name, 'code': code, 'expires_in': _expiry_label(), 'reset_url': reset_url},
    )


def send_verify_new_email(to_address, *, name, code, old_email):
    return _send(
        to_address,
        'Confirm your new V-ENT address',
        'verify_new_email.html',
        {'name': name, 'code': code, 'expires_in': _expiry_label(),
         'new_email': to_address, 'old_email': old_email},
    )


def send_waitlist_welcome(to_address):
    return _send(to_address, 'You are on the V-ENT waitlist', 'waitlist_welcome.html', {})


# ---------------------------------------------------------------------------
# Playing
# ---------------------------------------------------------------------------

def send_tournament_registered(user, tournament, *, entry_paid_vc=0):
    """Confirmation that a slot is held. Sent once, on a confirmed registration."""
    starts = tournament.start_date_and_time
    rows = [
        ('Game', tournament.tournament_game.game_title if tournament.tournament_game else 'To be announced'),
        ('Starts', starts.strftime('%d %b %Y, %H:%M') if starts else 'To be announced'),
        ('Format', tournament.bracket_type or 'Single elimination'),
    ]
    if entry_paid_vc:
        rows.append(('Entry paid', f'{int(entry_paid_vc):,} VC', '#D4AF37'))
    pool = tournament.prize_pool_coins
    if pool:
        rows.append(('Prize pool', f'{pool:,} VC', '#D4AF37'))

    return _send(
        user.email,
        f'You are in: {tournament.tournament_title}',
        'tournament_registered.html',
        {
            'name': _first_name(user),
            'tournament': tournament.tournament_title,
            'starts': starts.strftime('%d %b, %H:%M') if starts else 'soon',
            'bracket_url': f'{APP_URL}/tournaments/view-tournament?id={tournament.tournament_id}',
            'rows': rows,
        },
    )


def send_ticket_purchased(ticket):
    """One email per ticket, because each ticket admits one person by its code."""
    event = ticket.event
    starts = event.start_date
    rows = [
        ('Tier', ticket.tier.name),
        ('Venue', event.location or 'Online'),
        ('Doors', starts.strftime('%d %b %Y, %H:%M') if starts else 'To be announced'),
        ('Paid', f'{int(ticket.price_vc):,} VC', '#D4AF37'),
    ]
    return _send(
        ticket.attendee_email or ticket.user.email,
        f'Your ticket for {event.name}',
        'ticket_purchased.html',
        {
            'name': ticket.attendee_name or _first_name(ticket.user),
            'event': event.name,
            'code': ticket.code,
            'ticket_url': f'{APP_URL}/events/my-tickets',
            'rows': rows,
        },
    )


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

def send_payout_approved(withdrawal, *, amount_ngn):
    user = withdrawal.wallet.user
    return _send(
        user.email,
        'Your withdrawal is on the way',
        'payout_processed.html',
        {
            'name': _first_name(user),
            'headline': 'Your withdrawal is on the way',
            'state': 'approved',
            'rejected': False,
            'intro': 'your withdrawal has been approved and sent to your bank.',
            'amount_ngn': f'NGN {amount_ngn:,}',
            'rows': [
                ('Amount', f'{withdrawal.amount:,} VC', '#D4AF37'),
                ('You receive', f'NGN {amount_ngn:,}', '#4CAF50'),
                ('Bank', f'{withdrawal.bank_name} {withdrawal.account_number}'),
                ('Reference', f'WDR-{withdrawal.id}'),
                ('Arrives', 'within 1 to 3 business days'),
            ],
        },
    )


def send_payout_rejected(withdrawal, *, reason):
    user = withdrawal.wallet.user
    return _send(
        user.email,
        'We could not process your withdrawal',
        'payout_processed.html',
        {
            'name': _first_name(user),
            'headline': 'We could not process that withdrawal',
            'state': 'rejected',
            'rejected': True,
            'intro': 'your withdrawal request was not approved, and every coin is back in your wallet.',
            'reason': reason or 'No reason was recorded. Contact support and we will explain.',
            'rows': [
                ('Amount returned', f'{withdrawal.amount:,} VC', '#D4AF37'),
                ('Bank', f'{withdrawal.bank_name} {withdrawal.account_number}'),
                ('Reference', f'WDR-{withdrawal.id}'),
            ],
        },
    )


def send_kyc_approved(user):
    return _send(
        user.email,
        'Your identity has been verified',
        'kyc_result.html',
        {
            'name': _first_name(user),
            'approved': True,
            'headline': 'Identity verified',
            'preheader': 'Your ID check passed. Withdrawals are unlocked.',
            'intro': 'your document checked out.',
            'cta': 'View my wallet',
        },
    )


def send_kyc_rejected(user, *, reason):
    return _send(
        user.email,
        'We need a clearer copy of your ID',
        'kyc_result.html',
        {
            'name': _first_name(user),
            'approved': False,
            'headline': 'We need a clearer document',
            'preheader': 'Your ID check did not pass. Here is what to fix.',
            'intro': 'we could not verify the document you sent.',
            'reason': reason or 'The document could not be read. Retake the photo in good light '
                                'with all four corners visible.',
            'cta': 'Try again',
        },
    )


def send_login_alert(user, request=None):
    """Tell somebody their account was signed into from somewhere new.

    Only called when the address has not been seen on the account before, and
    only when the account has the alert switched on - it is on by default,
    because the first time this matters is the time nobody expected it.
    """
    try:
        from .models import UserSetting

        # Settings live in one JSON blob keyed by section, so read it that way
        # rather than expecting a column that does not exist.
        setting = UserSetting.objects.filter(user=user).first()
        if setting is not None:
            security = (setting.data or {}).get('security') or {}
            if isinstance(security, dict) and security.get('login_alerts') is False:
                return False

        latest = user.login_events.first() if hasattr(user, 'login_events') else None
        where = ', '.join(p for p in [getattr(latest, 'city', ''), getattr(latest, 'country', '')] if p)
        rows = [
            ('When', latest.created_at.strftime('%d %b %Y, %H:%M') if latest else 'Just now'),
            ('Where', where or 'Unknown location'),
            ('IP address', getattr(latest, 'ip', '') or 'Unknown'),
            ('Device', _short_agent(getattr(latest, 'user_agent', ''))),
        ]
        return _send(
            user.email.strip().lower(),
            'New sign-in to your V-ENT account',
            'login_alert.html',
            {
                'name': user.full_name or user.username,
                'rows': rows,
                'reset_url': f'{APP_URL}/forgot-password',
            },
        )
    except Exception:
        logger.exception('login alert failed for %s', getattr(user, 'email', '?'))
        return False


def _short_agent(agent):
    """A user agent string, reduced to something a person can read."""
    if not agent:
        return 'Unknown device'
    browsers = [('Edg/', 'Edge'), ('OPR/', 'Opera'), ('Chrome/', 'Chrome'),
                ('Firefox/', 'Firefox'), ('Safari/', 'Safari')]
    systems = [('Windows NT 10', 'Windows'), ('Windows', 'Windows'), ('Android', 'Android'),
               ('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Mac OS X', 'Mac'), ('Linux', 'Linux')]
    browser = next((label for token, label in browsers if token in agent), 'Unknown browser')
    system = next((label for token, label in systems if token in agent), 'Unknown device')
    return f'{browser} on {system}'


def send_partner_application_received(partner):
    """Acknowledge an application, and be clear that nothing is granted yet."""
    try:
        return _send(
            partner.contact_email,
            'We have your V-ENT partner application',
            'partner_status.html',
            {
                'name': partner.contact_name or partner.name,
                'heading': 'Application received',
                'intro': (
                    f'we have your application for {partner.name}. An admin reviews it before '
                    'any access is granted, and you will hear from us either way.'
                ),
                'rows': [
                    ('Partner', partner.name),
                    ('Access requested', ', '.join(partner.requested_scopes) or 'None specified'),
                    ('Sign-in with V-ENT', 'Requested' if partner.sso_status == 'requested' else 'Not requested'),
                ],
                'body': '',
                'cta_url': f'{APP_URL}/partners',
                'cta_label': 'Open the partner area',
            },
        )
    except Exception:
        logger.exception('partner application email failed')
        return False


def send_partner_decision(partner):
    """Tell a partner what was decided, and exactly what they may read."""
    approved = partner.status == 'approved'
    try:
        return _send(
            partner.contact_email,
            f'Your V-ENT partner application was {partner.status}',
            'partner_status.html',
            {
                'name': partner.contact_name or partner.name,
                'heading': 'Partner access approved' if approved else f'Partner application {partner.status}',
                'intro': (
                    'your partner account is live. The scopes below are what your keys can read.'
                    if approved else
                    f'your application for {partner.name} was {partner.status}.'
                ),
                'rows': [
                    ('Partner', partner.name),
                    ('Status', partner.status.title()),
                    ('Scopes granted', ', '.join(partner.approved_scopes) or 'None'),
                ],
                'body': partner.review_note or '',
                'cta_url': f'{APP_URL}/partners',
                'cta_label': 'Open the partner area',
            },
        )
    except Exception:
        logger.exception('partner decision email failed')
        return False
