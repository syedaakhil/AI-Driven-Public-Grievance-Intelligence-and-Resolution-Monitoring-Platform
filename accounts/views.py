from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import otp
from .backends import WEAK_PASSWORD_FLAG
from .decorators import CITIZEN_SESSION_KEY

MAX_ADMIN_FAILURES = 10
LOCKOUT_SECONDS = 15 * 60


def _login_url(tab='', next_url=''):
    params = {key: value for key, value in (('as', tab), ('next', next_url)) if value}
    return reverse('accounts:login') + (f'?{urlencode(params)}' if params else '')


def login_view(request):
    next_url = request.GET.get('next', '')
    # Arriving from a protected dashboard page (?next=) opens the admin tab.
    tab = request.GET.get('as') or ('admin' if next_url else 'citizen')
    waiting = otp.pending(request)
    return render(request, 'accounts/login.html', {
        'tab': 'admin' if tab == 'admin' else 'citizen',
        'next': next_url,
        'waiting': waiting,
        'waiting_phone': otp.mask(waiting['phone']) if waiting else '',
        'waiting_digits': waiting['phone'][3:] if waiting else '',
        'dev_code': request.session.get(otp.DEV_CODE_KEY, '') if waiting else '',
        'otp_on_screen': otp.shows_code_on_screen(),
    })


@require_POST
def admin_login(request):
    next_url = request.POST.get('next', '')
    failures_key = f'admin-login-failures:{request.META.get("REMOTE_ADDR", "")}'
    if cache.get(failures_key, 0) >= MAX_ADMIN_FAILURES:
        messages.error(request, _('Too many failed attempts. Please try again in 15 minutes.'))
        return redirect(_login_url('admin', next_url))

    user = authenticate(
        request,
        username=request.POST.get('username', '').strip(),
        password=request.POST.get('password', ''),
    )
    if user is None or not user.is_staff:
        if not cache.add(failures_key, 1, LOCKOUT_SECONDS):
            cache.incr(failures_key)
        if getattr(request, WEAK_PASSWORD_FLAG, False):
            messages.error(request, _('This account still has a weak password, so it is blocked on the live '
                                      'site. Set a strong one with “manage.py changepassword”.'))
        else:
            messages.error(request, _('Invalid username or password.'))
        return redirect(_login_url('admin', next_url))

    cache.delete(failures_key)
    login(request, user)
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()},
                                       require_https=request.is_secure()):
        return redirect(next_url)
    return redirect('grievances:home')


@require_POST
def send_otp(request):
    phone = otp.normalize_phone(request.POST.get('phone'))
    if not phone:
        messages.error(request, _('Enter a valid 10-digit mobile number starting with 6, 7, 8 or 9.'))
        return redirect('accounts:login')
    try:
        otp.send(request, phone)
    except otp.OTPError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _('OTP sent to %(phone)s.') % {'phone': otp.mask(phone)})
    return redirect('accounts:login')


@require_POST
def verify_otp(request):
    try:
        phone = otp.verify(request, request.POST.get('otp'))
    except otp.OTPError as exc:
        messages.error(request, str(exc))
        return redirect('accounts:login')
    request.session.cycle_key()
    request.session[CITIZEN_SESSION_KEY] = phone
    return redirect('grievances:complaint_registration')


@require_POST
def reset_otp(request):
    otp.clear(request)
    return redirect('accounts:login')


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, _('You have been signed out.'))
    return redirect('accounts:login')
