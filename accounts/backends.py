from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

# Set on the request when a correct but weak password was refused, so the login page can say why.
WEAK_PASSWORD_FLAG = 'weak_password_refused'


class StrongPasswordBackend(ModelBackend):
    """
    The usual username/password login, except that outside debug mode a password that fails
    AUTH_PASSWORD_VALIDATORS is refused. This stops the demo admin/1234 account (created by
    accounts/migrations/0001) from working on a live site until its password is changed.
    Covers both /login/admin/ and Django's /admin/.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None or settings.DEBUG:
            return user
        try:
            validate_password(password, user)
        except ValidationError:
            if request is not None:
                setattr(request, WEAK_PASSWORD_FLAG, True)
            return None
        return user
