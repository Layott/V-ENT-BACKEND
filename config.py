import os

# SMTP
smtp_server = 'smtp.gmail.com'
smtp_port = 465

# Company Email
company_email = os.environ.get('COMPANY_EMAIL')

# Password
password = os.environ.get('COMPANY_EMAIL_PASSWORD')

# Messages
class Messages:
    missing_field = "All Fields Are Required"
    link_sent = "Verification Link Sent To Email"
    send_failed = "Failed To Send Verification Email"
