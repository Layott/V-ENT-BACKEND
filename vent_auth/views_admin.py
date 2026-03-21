import os

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Users, Waitlist
from .views_helpers import send_email


@api_view(["POST"])
def admin_login(request):
    password = request.data.get("password")

    if not password:
        return Response({"status": "error", "message": "Password is required"}, status=status.HTTP_400_BAD_REQUEST)

    if password == os.environ.get("ADMIN_PASSWORD"):
        return Response({"status": "success", "message": "Admin Login Successful"}, status=status.HTTP_200_OK)
    else:
        return Response({"status": "error", "message": "Invalid password"}, status=status.HTTP_401_UNAUTHORIZED)


@api_view(["GET"])
def get_all_username_and_email(request):
    users = Users.objects.all().values("username", "email")
    return Response({"status": "success", "data": list(users)}, status=status.HTTP_200_OK)


@api_view(["GET"])
def get_number_of_all_users(request):
    user_count = Users.objects.count()
    return Response({"status": "success", "total_users": user_count}, status=status.HTTP_200_OK)


@api_view(["POST"])
def check_username_availability(request):
    username = request.data.get("username")

    if not username:
        return Response({"status": "error", "message": "Username is required"}, status=status.HTTP_400_BAD_REQUEST)

    exists = Users.objects.filter(username=username).exists()

    if exists:
        return Response({"status": "success", "message": "Username exists"}, status=status.HTTP_200_OK)
    else:
        return Response({"status": "error", "message": "Username does not exist"}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def add_email_to_waitlist(request):
    email = request.data.get("email")

    if not email:
        return Response({"status": "error", "message": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

    if Waitlist.objects.filter(email=email).exists():
        return Response({"status": "error", "message": "This email is already on the waitlist."}, status=status.HTTP_400_BAD_REQUEST)

    if Users.objects.filter(email=email).exists():
        return Response({"status": "error", "message": "This email is already on the waitlist."}, status=status.HTTP_400_BAD_REQUEST)

    Waitlist.objects.create(email=email)

    subject = "Welcome to Vermillion City 🎉"
    email_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Welcome to Vermillion Enterprise!</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      line-height: 1.6;
      color: #333;
      background-color: #f9f9f9;
      margin: 0;
      padding: 0;
    }
    .container {
      width: 90%;
      max-width: 600px;
      margin: 20px auto;
      background: #fff;
      border-radius: 10px;
      box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
      overflow: hidden;
    }
    .content {
      padding: 20px;
    }
    h2 {
      color: #ff4747;
    }
    ul {
      padding-left: 20px;
    }
    a {
      text-decoration: none;
    }
    .footer {
      margin-top: 20px;
      font-size: 14px;
      color: #555;
    }
    .banner img {
      width: 100%;
      display: block;
    }
    .social-buttons {
      margin: 15px 0;
      text-align: center;
    }
    .social-buttons a {
      display: inline-block;
      margin: 5px;
      padding: 10px 18px;
      border-radius: 30px;
      font-size: 14px;
      font-weight: bold;
      color: #fff;
    }
    .instagram { background-color: #E1306C; }
    .tiktok { background-color: #010101; }
    .x { background-color: #1DA1F2; }
    .discord { background-color: #5865F2; }
    .whatsapp { background-color: #25D366; }
  </style>
</head>
<body>
  <div class="banner">
    <img src="https://vermillionent.pythonanywhere.com/media/images/top.jpg" alt="Top Banner">
  </div>

  <div class="container">
    <div class="content">
      <h2>Welcome to Vermillion City 🎉</h2>
      <p>Hello there!!,</p>
      <p>Welcome to the Vermillion Enterprise community! 🎉 We're thrilled to have you on board.</p>
      <p>
        We are building a platform for people in the anime and gaming industry.
        We share the same passions as you — anime, games, graphic design,
        game development, video editing, esports, and so much more.
      </p>

      <h2>What to do:</h2>
      <ul>
        <li><strong>Explore:</strong> Check out our planned features (it's on the landing page if you haven't seen it yet).</li>
        <li><strong>Earn:</strong> Our referral program is starting soon! If you're up for earning some rewards or small change, keep an eye out for our emails 🤝</li>
      </ul>

      <h2>Stay Connected:</h2>
      <p>Follow us and join the conversation:</p>
      <div class="social-buttons">
        <a href="https://www.instagram.com/myventhq" target="_blank" class="instagram">Instagram</a>
        <a href="https://www.tiktok.com/@myventhq" target="_blank" class="tiktok">TikTok</a>
        <a href="https://www.x.com/myventhq" target="_blank" class="x">X</a>
        <a href="https://discord.gg/hNxg2qVq5Y" target="_blank" class="discord">Discord</a>
        <a href="https://whatsapp.com/channel/0029VaFQkAR0lwgpEABI4y1O" target="_blank" class="whatsapp">WhatsApp</a>
      </div>
      <ul>
        <li>We'll release updates regularly and run exciting programs, so prepare for the big launch 😉</li>
        <li>Keep an eye on your inbox for exclusive opportunities.</li>
      </ul>

      <h2>Shop:</h2>
      <p>
        We'll soon have some merchandise and gaming products for you in <strong>Vermillion City (our shop)</strong>.<br>
        We'll announce once it's live — you'll be able to browse, request custom items, and grab your favorites.
      </p>

      <p><em>Fun fact:</em> <strong>"Vermillion City"</strong> was inspired by the anime <em>Pokémon</em> — a place where you can find whatever it is you want.</p>

      <p>Thank you for joining us on this exciting journey.<br>
      If you have any questions, feel free to reach out at <a href="mailto:info@v-ent.co">info@v-ent.co</a>.</p>

      <div class="footer">
        <p>Thank you,<br>The V-ENT Team</p>
      </div>
    </div>
  </div>

  <div class="banner">
    <img src="https://vermillionent.pythonanywhere.com/media/images/bottom.jpg" alt="Bottom Banner">
  </div>
</body>
</html>
"""

    send_email(email, subject, email_content)
    return Response({"status": "success", "message": "Email added to waitlist successfully."}, status=status.HTTP_201_CREATED)
