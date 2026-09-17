from django.contrib.auth.backends import BaseBackend
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()

class EmailOrUsernameModelBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        # `first()` rather than `get()`: one person's username can be another
        # person's email address, and two rows used to raise MultipleObjectsReturned
        # out of a sign-in.
        user = User.objects.filter(Q(username=username) | Q(email=username)).first()
        if user is None:
            return None

        # An account made through Google has no password at all (the column is
        # null), and `check_password(None)` raised a TypeError out of
        # `identify_hasher`, so signing in with the email of any Google account
        # was a 500. Found 17 September 2026 by the IDOR cases signing in as
        # a fixture with no password. No usable password means "not this way".
        if not user.password or not user.has_usable_password():
            return None
        if user.check_password(password):
            return user
        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
