"""Data the shared layout (sidebar, top bar, footer) needs on every page."""
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

# (key, url name, label, icon, badge)
MAIN_MENU = [
    ('home', 'grievances:home', _('Home'), 'home', ''),
    ('file', 'grievances:complaint_create', _('Filed complaint'), 'file', ''),
    ('officers', 'grievances:officers', _('Employees'), 'building', ''),
    ('dataset', 'grievances:complaint_list', _('Dataset'), 'table', ''),
    ('agent_work', 'mlops:agent_work', _('Agent Work'), 'settings', ''),
]

# url name -> (highlighted menu key, breadcrumb label)
PAGES = {
    'home': ('home', _('Overview')),
    'complaint_create': ('file', _('Filed complaints')),
    'officers': ('officers', _('Employees')),
    'officer_detail': ('officers', _('Employee details')),
    'officer_edit': ('officers', _('Edit employee')),
    'officer_id_card': ('officers', _('Employee ID card')),
    'complaint_list': ('dataset', _('Complaints dataset')),
    'complaint_detail': ('dataset', _('Complaint details')),
    'agent_work': ('agent_work', _('Agent Work')),
    'help_desk': ('help_desk', _('Citizen help desk')),
    'help_support': ('help_support', _('Help & support')),
}


def layout(request):
    match = getattr(request, 'resolver_match', None)
    active, crumb = PAGES.get(match.url_name if match else '', ('', ''))
    waiting = _citizen_complaints_waiting(request)
    menu = [
        {
            'key': key,
            'url': reverse(url_name),
            'label': label,
            'icon': icon,
            'badge': (str(waiting) if waiting else '') if key == 'file' else badge,
            'active': key == active,
        }
        for key, url_name, label, icon, badge in MAIN_MENU
    ]
    return {
        'menu': menu,
        'menu_active': active,
        'crumb': crumb,
        'year': timezone.localdate().year,
        'notifications': _notifications(request),
    }


def _citizen_complaints_waiting(request):
    """Citizen complaints still needing a category and ward (shown on the "Filed complaint" menu item)."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and user.is_staff):
        return 0
    from grievances.models import Complaint

    return Complaint.objects.filter(Complaint.needs_review_filter(), filed_by_citizen=True).count()


def _notifications(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and user.is_staff):
        return []
    from grievances.models import Complaint, Notice

    items = [
        {
            'icon': 'file',
            'title': f'{complaint.reference} · {complaint.title}',
            'meta': f'{complaint.get_status_display()} · {date_format(timezone.localtime(complaint.created_at), "d M Y")}',
            'url': complaint.get_absolute_url(),
        }
        for complaint in Complaint.objects.all()[:3]
    ]
    items += [
        {
            'icon': notice.icon,
            'title': notice.title,
            'meta': f'{notice.topic} · {date_format(notice.published_on, "d M Y")}',
            'url': reverse('grievances:home') + '#news',
        }
        for notice in Notice.objects.filter(is_published=True)[:3]
    ]
    return items
