from django.conf import settings
from django.utils import translation
from django.utils.cache import patch_vary_headers


class CookieLanguageMiddleware:
    """
    Show the site in the language the visitor picked (English or Hindi), remembered in the language
    cookie that django.views.i18n.set_language sets. Unlike Django's LocaleMiddleware it ignores the
    browser's Accept-Language header, so a first visit is always in English (LANGUAGE_CODE).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.languages = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request):
        code = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if code not in self.languages:
            code = settings.LANGUAGE_CODE
        translation.activate(code)
        request.LANGUAGE_CODE = code
        response = self.get_response(request)
        patch_vary_headers(response, ('Cookie',))
        response.headers.setdefault('Content-Language', code)
        return response
