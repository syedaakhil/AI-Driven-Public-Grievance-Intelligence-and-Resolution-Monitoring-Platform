"""Figures for the home dashboard, all counted from the database."""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext

from .models import Complaint, Ward

CHANNEL_COLOURS = {
    Complaint.Channel.PORTAL: '#2E5487',
    Complaint.Channel.WHATSAPP: '#5C9B79',
    Complaint.Channel.X: '#263B5B',
    Complaint.Channel.FACEBOOK: '#7FA3CF',
    Complaint.Channel.INSTAGRAM: '#C77A9B',
    Complaint.Channel.PHONE: '#E5A15B',
    Complaint.Channel.WALK_IN: '#C9D6E8',
    Complaint.Channel.EMAIL: '#9AA5B4',
}
# Illustrated map (SVG viewBox 0 0 300 250): ward number -> outline points, fill, marker position
MAP_SHAPES = {
    1: ('70,100 140,92 205,98 196,150 140,164 62,152', '#EADFCB', (118, 128)),
    2: ('40,46 120,22 214,26 262,64 205,98 140,92 70,100', '#D5E1F0', (160, 56)),
    3: ('205,98 262,64 278,138 252,196 196,150', '#DCE3EE', (238, 124)),
    4: ('62,152 140,164 196,150 252,196 200,226 100,224 44,190', '#D7E3D3', (126, 198)),
}
MAPPED_WARDS = len(MAP_SHAPES)


def _start_of_month(moment):
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _shift_months(moment, months):
    years, month_index = divmod(moment.month - 1 + months, 12)
    return moment.replace(year=moment.year + years, month=month_index + 1)


def month_periods(count, months_back=0):
    """(label, start, end) for `count` calendar months, ending `months_back` months before this one."""
    first = _shift_months(_start_of_month(timezone.localtime()), -(count - 1 + months_back))
    return [
        (date_format(_shift_months(first, i), 'M'), _shift_months(first, i), _shift_months(first, i + 1))
        for i in range(count)
    ]


def week_periods(count):
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=count - 1 - i) for i in range(count)]
    return [(date_format(start, 'd M'), start, start + timedelta(weeks=1)) for start in starts]


def _between(queryset, field, start, end):
    return queryset.filter(**{f'{field}__gte': start, f'{field}__lt': end})


def _series(periods, field, queryset):
    return [_between(queryset, field, start, end).count() for _, start, end in periods]


def percent_change(now, before):
    """None when there is nothing to compare with (shown as a dash)."""
    if not before:
        return None
    return round((now - before) / before * 100, 1)


def dashboard(range_months):
    complaints = Complaint.objects.all()
    resolved = complaints.filter(status=Complaint.Status.RESOLVED)

    months = month_periods(range_months)
    earlier = month_periods(range_months, months_back=range_months)
    weeks = week_periods(8)
    start, end = months[0][1], months[-1][2]
    prev_start, prev_end = earlier[0][1], earlier[-1][2]

    filed = _between(complaints, 'created_at', start, end)
    filed_before = _between(complaints, 'created_at', prev_start, prev_end)
    reported, reported_before = filed.count(), filed_before.count()
    closed = _between(resolved, 'resolved_at', start, end).count()
    closed_before = _between(resolved, 'resolved_at', prev_start, prev_end).count()

    def rate(queryset, total):
        return round(queryset.filter(status=Complaint.Status.RESOLVED).count() / total * 100, 1) if total else None

    monthly_resolved = _series(months, 'resolved_at', resolved)

    channel_counts = dict(filed.values_list('channel').annotate(total=Count('id')))
    channels = [
        {
            'label': label,
            'count': channel_counts.get(value, 0),
            'share': round(channel_counts.get(value, 0) / reported * 100) if reported else 0,
            'colour': CHANNEL_COLOURS[value],
        }
        for value, label in Complaint.Channel.choices
    ]

    all_wards = list(Ward.objects.annotate(
        open_count=Count('complaints', filter=Q(complaints__status__in=Complaint.OPEN_STATUSES))
    ))
    wards = [ward for ward in all_wards if ward.number in MAP_SHAPES]
    # Citizen complaints have no ward until the corporation reviews them; they get their own bar.
    unassigned_open = complaints.filter(ward__isnull=True, status__in=Complaint.OPEN_STATUSES).count()
    busiest = max([ward.open_count for ward in wards] + [unassigned_open, 1])
    for ward in wards:
        ward.bar = round(ward.open_count / busiest * 100)
        ward.shape, ward.fill, (ward.pin_x, ward.pin_y) = MAP_SHAPES[ward.number]

    return {
        'reported': reported,
        'reported_change': percent_change(reported, reported_before),
        'resolved': closed,
        'resolved_change': percent_change(closed, closed_before),
        'working': complaints.filter(
            status__in=[Complaint.Status.ASSIGNED, Complaint.Status.IN_PROGRESS]).count(),
        'waiting': complaints.filter(status=Complaint.Status.SUBMITTED).count(),
        'rate': rate(filed, reported),
        'rate_before': rate(filed_before, reported_before),
        'channels': channels,
        'wards': wards,
        'unassigned': {'open': unassigned_open, 'bar': round(unassigned_open / busiest * 100)},
        'citizen_filed': filed.filter(filed_by_citizen=True).count(),
        # For the optional street map (Leaflet)
        'ward_points': [
            {'id': ward.pk, 'label': str(ward), 'area': gettext(ward.area), 'open': ward.open_count,
             'lat': float(ward.latitude), 'lng': float(ward.longitude)}
            for ward in all_wards if ward.latitude is not None and ward.longitude is not None
        ],
        'chart': {
            'monthly': {
                'labels': [label for label, _, _ in months],
                'reported': _series(months, 'created_at', complaints),
                'resolved': monthly_resolved,
            },
            'weekly': {
                'labels': [label for label, _, _ in weeks],
                'reported': _series(weeks, 'created_at', complaints),
                'resolved': _series(weeks, 'resolved_at', resolved),
            },
            'channels': {
                'labels': [channel['label'] for channel in channels if channel['count']],
                'counts': [channel['count'] for channel in channels if channel['count']],
                'colours': [channel['colour'] for channel in channels if channel['count']],
            },
            'resolvedLine': {
                'labels': [label for label, _, _ in months],
                'values': monthly_resolved,
            },
        },
    }
