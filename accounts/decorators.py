from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect
from django.utils.translation import gettext as _

CITIZEN_SESSION_KEY = 'citizen_phone'

# Dashboard pages are for corporation admins (staff users) only.
staff_required = user_passes_test(lambda user: user.is_active and user.is_staff)


def citizen_required(view):
    """Allow only citizens who have verified their mobile number with an OTP."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.session.get(CITIZEN_SESSION_KEY):
            messages.info(request, _('Please verify your mobile number to continue.'))
            return redirect('accounts:login')
        return view(request, *args, **kwargs)

    return wrapper
