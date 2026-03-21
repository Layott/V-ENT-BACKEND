import os
import random
import string
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from .models import Users, UserWallet


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
    smtp_server = 'smtp.gmail.com'
    smtp_port = 465
    from_address = os.environ.get('EMAIL_ADDRESS')
    password = os.environ.get('EMAIL_PASSWORD')

    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    try:
        msg = MIMEMultipart()
        msg['From'] = from_address
        msg['To'] = to_address
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html'))

        server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        server.login(from_address, password)
        server.sendmail(from_address, to_address, msg.as_string())
        server.quit()

        return True
    except Exception as e:
        return False


def generate_session_token(length=16):
    """Generate a random 16-character token"""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


def create_default_profile_picture(full_name):
    image = Image.new('RGB', (100, 100), color='#46484F')
    draw = ImageDraw.Draw(image)

    font_path = "C:\\Users\\habee\\Downloads\\despair_time\\Despair Time Straight.otf"
    try:
        font = ImageFont.truetype(font_path, size=40)
    except IOError:
        font = ImageFont.load_default()

    names = full_name.split()
    initials = ''.join(name[0].upper() for name in names[:2])

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
