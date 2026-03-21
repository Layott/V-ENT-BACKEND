# This file is a re-export shim.
# All view functions have been split into focused modules:
#   views_helpers.py  — shared utilities (send_email, generate_session_token, etc.)
#   views_auth.py     — signup, login, logout, email verification, password reset
#   views_profile.py  — profile CRUD, game accounts, community, teams, social links
#   views_social.py   — Google/Facebook OAuth flows
#   views_gallery.py  — image upload and gallery management
#   views_wallet.py   — wallet / send_funds
#   views_admin.py    — admin endpoints and waitlist
#
# urls.py uses `from .views import *` so all names must remain importable from here.

from .views_helpers import *
from .views_auth import *
from .views_profile import *
from .views_social import *
from .views_gallery import *
from .views_wallet import *
from .views_admin import *
