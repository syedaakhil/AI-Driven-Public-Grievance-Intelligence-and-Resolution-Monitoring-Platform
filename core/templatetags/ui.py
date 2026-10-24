"""Template helpers shared by every page: {% icon %}, {% static_v %} and {% query %}."""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext

register = template.Library()

# 24x24 stroke icons
ICONS = {
    'home': '<path d="M3.5 11 12 4l8.5 7"/><path d="M5.5 9.8V20h13V9.8"/><path d="M10 20v-5.5h4V20"/>',
    'file': '<path d="M13.5 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z"/><path d="M13.5 3.5V9H19"/><path d="M8.5 13h7M8.5 16.5h4.5"/>',
    'building': '<path d="M3 20.5h18"/><path d="M4 9.5 12 4l8 5.5"/><path d="M4 9.5h16"/><path d="M6 12v6M10 12v6M14 12v6M18 12v6"/><path d="M4.5 18.5h15"/>',
    'table': '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M3.5 14.5h17M9.5 4.5v15"/>',
    'phone': '<path d="M6 3.5h3l1.8 4.6-2.3 1.4a10.5 10.5 0 0 0 6 6l1.4-2.3 4.6 1.8v3a2 2 0 0 1-2 2A15.5 15.5 0 0 1 4 5.5a2 2 0 0 1 2-2"/>',
    'help': '<circle cx="12" cy="12" r="8.5"/><path d="M9.7 9.4a2.4 2.4 0 1 1 3.3 2.2c-.6.3-1 .8-1 1.5v.4"/><path d="M12 16.6h.01"/>',
    'chevron-right': '<path d="m9.5 6 6 6-6 6"/>',
    'chevron-down': '<path d="m6 9.5 6 6 6-6"/>',
    'arrow-right': '<path d="M5 12h13.5M13 6.5l5.5 5.5-5.5 5.5"/>',
    'arrow-left': '<path d="M19 12H5.5M11 6.5 5.5 12l5.5 5.5"/>',
    'globe': '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5a13 13 0 0 1 0 17M12 3.5a13 13 0 0 0 0 17"/>',
    'bell': '<path d="M6.5 9.5a5.5 5.5 0 0 1 11 0c0 5.5 2.5 7.5 2.5 7.5H4s2.5-2 2.5-7.5"/><path d="M10.2 20.5a2 2 0 0 0 3.6 0"/>',
    'check': '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    'road': '<path d="M8 20.5 10.5 3.5M16 20.5 13.5 3.5M12 6.5v2M12 11v2.5M12 16v2"/>',
    'droplet': '<path d="M12 3.5s6 6.6 6 10.8a6 6 0 0 1-12 0C6 10.1 12 3.5 12 3.5z"/>',
    'waves': '<path d="M3.5 8c1.4 0 1.9-1.4 4.2-1.4S10.6 8 12 8s1.9-1.4 4.2-1.4S19.1 8 20.5 8M3.5 13c1.4 0 1.9-1.4 4.2-1.4S10.6 13 12 13s1.9-1.4 4.2-1.4 2.9 1.4 4.3 1.4M3.5 18c1.4 0 1.9-1.4 4.2-1.4S10.6 18 12 18s1.9-1.4 4.2-1.4 2.9 1.4 4.3 1.4"/>',
    'trash': '<path d="M4.5 7h15M10 11v5.5M14 11v5.5M6 7l.9 11.6a1.6 1.6 0 0 0 1.6 1.4h7a1.6 1.6 0 0 0 1.6-1.4L18 7M9.5 7V4.5h5V7"/>',
    'megaphone': '<path d="M4 10v4h3l7 4V6L7 10z"/><path d="M17 9.5a3.5 3.5 0 0 1 0 5"/><path d="M7 14l1 5h2.5l-1-4.5"/>',
    'trend-up': '<path d="m3.5 16.5 5.5-5.5 4 4 7.5-7.5"/><path d="M15 7.5h5.5V13"/>',
    'menu': '<path d="M4 6.5h16M4 12h16M4 17.5h16"/>',
    'x': '<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    'chat': '<path d="M4 5.5h11a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5H9l-3.5 3v-3H4A1.5 1.5 0 0 1 2.5 13V7A1.5 1.5 0 0 1 4 5.5z"/><path d="M16.5 9.5H20a1.5 1.5 0 0 1 1.5 1.5v5.5A1.5 1.5 0 0 1 20 18h-1v2.5L16 18h-4.5"/><path d="M6.5 10h6"/>',
    'send': '<path d="M20.5 3.5 10 14"/><path d="M20.5 3.5 14 20.5l-4-6.5-6.5-4z"/>',
    'search': '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.3-4.3"/>',
    'mail': '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="m4 7.5 8 5.5 8-5.5"/>',
    'upload': '<path d="M12 15.5V4.5M7.5 9 12 4.5 16.5 9"/><path d="M4.5 15.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3"/>',
    'user': '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    'users': '<circle cx="9" cy="8.5" r="3.3"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M15.5 5.2a3.3 3.3 0 0 1 0 6.6M17.5 14.4a6 6 0 0 1 3.5 5.6"/>',
    'pin': '<path d="M12 20.5s6.5-5.8 6.5-11a6.5 6.5 0 0 0-13 0c0 5.2 6.5 11 6.5 11z"/><circle cx="12" cy="9.5" r="2.3"/>',
    'clock': '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    'info': '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
    'alert': '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4M12 16.8h.01"/>',
    'download': '<path d="M12 4.5v11M7.5 11l4.5 4.5 4.5-4.5"/><path d="M4.5 19.5h15"/>',
    'refresh': '<path d="M19.5 11A7.5 7.5 0 0 0 6 6.6L3.5 9M3.5 4.5V9H8M4.5 13A7.5 7.5 0 0 0 18 17.4l2.5-2.4M20.5 19.5V15H16"/>',
    'list': '<path d="M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
    'sparkle': '<path d="M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4M6 6l2.8 2.8M15.2 15.2 18 18M18 6l-2.8 2.8M8.8 15.2 6 18"/>',
    'access': '<circle cx="12" cy="4.8" r="1.6"/><path d="M5 8.5 12 10l7-1.5M12 10v4.5L9 20.5M12 14.5l3 6"/>',
    'logout': '<path d="M14.5 4.5h3a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-3"/><path d="M10 16.5 5.5 12 10 7.5M5.5 12h10"/>',
    'shield': '<path d="M12 3.5 5 6.2v5.5c0 4.4 3 7.4 7 8.8 4-1.4 7-4.4 7-8.8V6.2z"/>',
    'settings': '<circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.3M12 18.2v2.3M3.5 12h2.3M18.2 12h2.3M6 6l1.6 1.6M16.4 16.4 18 18M18 6l-1.6 1.6M7.6 16.4 6 18"/>',
}


@register.simple_tag
def icon(name, size=''):
    css = 'icon' + (f' icon--{size}' if size else '')
    return format_html(
        '<svg class="{}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{}</svg>',
        css,
        mark_safe(ICONS.get(name, ICONS['info'])),
    )


@register.simple_tag
def static_v(path):
    """Like {% static %}, plus ?v=<last modified time> so browsers reload a file after it changes."""
    url = static(path)
    found = finders.find(path)
    if found:
        url += f'?v={int(os.path.getmtime(found))}'
    return url


@register.filter
def absolute(value):
    return abs(value)


@register.filter
def tr(value):
    """
    Translate text stored in the database, such as a category or ward name, into the visitor's
    language. Only text listed in grievances/reference_i18n.py has a Hindi version; anything else
    (citizens' own words, officer names) is shown unchanged.
    """
    return gettext(str(value)) if value not in (None, '') else value


@register.simple_tag(takes_context=True)
def query(context, **changes):
    """The current query string with some parameters changed; an empty value removes one."""
    params = context['request'].GET.copy()
    for key, value in changes.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f'?{encoded}' if encoded else '?'
