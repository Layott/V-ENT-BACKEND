import logging
import os
import random
import string
import requests
from io import BytesIO
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
from .models import Users, UserWallet

logger = logging.getLogger(__name__)


# Cross-platform font resolution for the generated default avatar.
# Ordered by preference: bundled repo font first (works everywhere), then the
# fonts that ship by default on the common host OSes, then Pillow's built-in
# bitmap font as a last resort. This must never raise on Linux (prod EC2).
_BUNDLED_FONT = os.path.join(settings.BASE_DIR, 'assets', 'fonts', 'Inter-Bold.ttf')
_FONT_CANDIDATES = [
    _BUNDLED_FONT,
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',      # Debian/Ubuntu (EC2)
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf',
    '/Library/Fonts/Arial Bold.ttf',                            # macOS
    'C:\\Windows\\Fonts\\arialbd.ttf',                          # Windows
]


def _load_avatar_font(size=40):
    """Return a usable PIL font, trying bundled + system fonts, never raising."""
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, IOError):
            continue
    # Pillow >= 10.1 can scale the built-in bitmap font; older falls back to 1x.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def generate_wallet_id():
    """Generate a unique 10-digit wallet ID."""
    return str(random.randint(10**9, 10**10 - 1))


def create_user_wallet(user):
    while True:
        wallet_id = generate_wallet_id()
        if not UserWallet.objects.filter(user_wallet_id=wallet_id).exists():
            break

    user_wallet = UserWallet.objects.create(
        user=user,
        user_wallet_id=wallet_id
    )
    user_wallet.save()
    return "Wallet created successfully"


def send_email(to_address, subject, html_body):
    """Send one HTML email through whatever backend settings configure.

    This used to open a hardcoded connection to smtp.gmail.com:465 with
    credentials from EMAIL_ADDRESS/EMAIL_PASSWORD, ignoring Django's mail
    settings entirely, and swallow every exception. In production that meant
    signup answered "Verification link sent to email" while nothing was sent
    and nothing was logged.

    Now it uses the configured backend - on the server that is the local
    Postfix, which relays to the mail provider - and failures are logged.
    Callers still get True/False so no existing flow changes shape.
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=html_body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            to=[to_address],
        )
        message.attach_alternative(html_body, 'text/html')
        message.send(fail_silently=False)
        return True
    except Exception:
        logger.exception('send_email failed for %s (subject=%r)', to_address, subject)
        return False

def generate_session_token(length=16):
    """Generate a random 16-character token"""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


def create_default_profile_picture(full_name):
    image = Image.new('RGB', (100, 100), color='#46484F')
    draw = ImageDraw.Draw(image)

    font = _load_avatar_font(size=40)

    names = (full_name or '').split()
    initials = ''.join(name[0].upper() for name in names[:2]) or 'V'

    text_bbox = draw.textbbox((0, 0), initials, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]

    width, height = image.size
    x = (width - text_width) / 2
    y = (height - text_height) / 2

    draw.text((x, y), initials, fill='#ED1C24', font=font)

    temp_image = BytesIO()
    image.save(temp_image, format='PNG')
    temp_image.seek(0)

    return temp_image


def generate_unique_username(email):
    base_username = email.split('@')[0]
    username = base_username
    counter = 1

    while Users.objects.filter(username=username).exists():
        suffix = ''.join(random.choices(string.digits, k=3))
        username = f"{base_username}_{suffix}"
        counter += 1

    return username


def download_image_from_url(url):
    response = requests.get(url)
    if response.status_code == 200:
        return BytesIO(response.content)
    raise Exception("Failed to download profile picture.")


def session_timeout_minutes():
    """Minutes a login_session_token stays valid.

    Centralised so the window is one setting rather than the same literal
    repeated in eighteen places, which is how it ended up meaning "two hours"
    everywhere long after that stopped being a sensible answer.
    """
    from django.conf import settings
    return int(getattr(settings, 'SESSION_TOKEN_TIMEOUT_MINUTES', 60 * 24 * 14))
