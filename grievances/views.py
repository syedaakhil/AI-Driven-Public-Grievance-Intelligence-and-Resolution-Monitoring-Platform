import csv
import logging
import os
import re

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db.models import Count, F, FloatField, Q
from django.db.models.functions import Cast
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.decorators import CITIZEN_SESSION_KEY, citizen_required, staff_required
from accounts.otp import mask as mask_phone

from . import notify, services
from .forms import (CitizenComplaintForm, ComplaintFilterForm, FiledComplaintFilterForm, OfficerFilterForm,
                    StatusUpdateForm, SupportEnquiryForm)
from .models import Category, Complaint, ComplaintUpdate, Notice, Officer, SupportEnquiry

logger = logging.getLogger(__name__)

PAGE_SIZE = 15
REFERENCE_RE = re.compile(r'^CC-\d{4}-\d{5,}$')


# ---------- Home dashboard ----------

@staff_required
def home(request):
    range_months = 3 if request.GET.get('range') == '3' else 6
    return render(request, 'grievances/home.html', {
        'stats': services.dashboard(range_months),
        'range_months': range_months,
        'notices': Notice.objects.filter(is_published=True)[:10],
    })


# ---------- Complaints ----------

@staff_required
def complaint_create(request):
    """The "Filed complaint" page: every complaint citizens registered through the portal."""
    citizen_complaints = Complaint.objects.filter(filed_by_citizen=True)
    form = FiledComplaintFilterForm(request.GET)
    complaints = form.filter(citizen_complaints.select_related('category', 'ward', 'officer'))
    summary = citizen_complaints.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Complaint.needs_review_filter()),
        working=Count('id', filter=Q(status__in=[Complaint.Status.ASSIGNED, Complaint.Status.IN_PROGRESS])),
        resolved=Count('id', filter=Q(status=Complaint.Status.RESOLVED)),
    )
    return render(request, 'grievances/complaint_form.html', {
        'form': form,
        'page': Paginator(complaints, PAGE_SIZE).get_page(request.GET.get('page')),
        'summary': summary,
        'filtered': any(request.GET.get(name) for name in ('q', 'status', 'review')),
    })


def _filtered_complaints(request):
    form = ComplaintFilterForm(request.GET)
    queryset = Complaint.objects.select_related('category', 'ward', 'officer')
    return form, form.filter(queryset)


@staff_required
def complaint_list(request):
    form, complaints = _filtered_complaints(request)
    page = Paginator(complaints, PAGE_SIZE).get_page(request.GET.get('page'))
    summary = Complaint.objects.aggregate(
        total=Count('id'),
        open=Count('id', filter=Q(status__in=Complaint.OPEN_STATUSES)),
        resolved=Count('id', filter=Q(status=Complaint.Status.RESOLVED)),
        high=Count('id', filter=Q(priority=Complaint.Priority.HIGH)),
    )
    return render(request, 'grievances/complaint_list.html', {
        'form': form,
        'page': page,
        'summary': summary,
        'filtered': any(request.GET.get(name) for name in form.fields if name != 'sort'),
    })


def _csv_safe(value):
    text = str(value)
    # Stop spreadsheet apps from treating a cell as a formula (OWASP CSV injection: = + - @ tab CR).
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


@staff_required
def complaint_export(request):
    _form, complaints = _filtered_complaints(request)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="city-corporation-complaints.csv"'
    response.write('﻿')  # lets Excel detect UTF-8
    writer = csv.writer(response)
    writer.writerow([_('Reference'), _('Title'), _('Category'), _('Department'), _('Ward'), _('Location'),
                     _('Channel'), _('Priority'), _('Status'), _('Officer'), _('Filed on'), _('Resolved on')])
    for complaint in complaints:
        writer.writerow([_csv_safe(value) for value in (
            complaint.reference, complaint.title,
            _(complaint.category.name) if complaint.category else _('Not assigned'),
            _(complaint.category.department) if complaint.category else '',
            complaint.ward or _('Not assigned'), complaint.location, complaint.get_channel_display(),
            complaint.get_priority_display(), complaint.get_status_display(),
            complaint.officer.name if complaint.officer else '',
            timezone.localtime(complaint.created_at).strftime('%Y-%m-%d %H:%M'),
            timezone.localtime(complaint.resolved_at).strftime('%Y-%m-%d %H:%M') if complaint.resolved_at else '',
        )])
    return response


def _timeline(complaint):
    reached = {}
    for update in complaint.updates.all():
        reached.setdefault(update.status, update.created_at)
    statuses = list(Complaint.Status)
    current = statuses.index(complaint.status)
    steps = []
    for index, status in enumerate(statuses):
        if index < current or (index == current and status == Complaint.Status.RESOLVED):
            state = 'done'
        elif index == current:
            state = 'current'
        else:
            state = 'pending'
        when = reached.get(status.value) if index <= current else None
        steps.append({'label': status.label, 'state': state, 'when': when})
    return steps


@staff_required
def complaint_detail(request, reference):
    complaint = get_object_or_404(
        Complaint.objects.select_related('category', 'ward', 'officer'), reference=reference)

    if request.method == 'POST':
        form = StatusUpdateForm(request.POST)
        if form.is_valid():
            category, ward, status, officer, note, share_note = (
                form.cleaned_data[key] for key in ('category', 'ward', 'status', 'officer', 'note', 'share_note'))
            changes = []
            if category != complaint.category:
                changes.append(f'Category set to {category.name}.' if category else 'Category removed.')
            if ward != complaint.ward:
                changes.append(f'Ward set to {ward}.' if ward else 'Ward removed.')
            # Route the complaint to the officer in charge of its category and ward: when it has no officer
            # yet, or when the category/ward was corrected and the current officer doesn't cover the new
            # one. An officer the admin picked (or removed) in this update is left as chosen.
            officer_untouched = officer == complaint.officer
            current_fits = officer is not None and (officer.category_id, officer.ward_id) == (
                getattr(category, 'pk', None), getattr(ward, 'pk', None))
            if category and ward and officer_untouched and not current_fits:
                had_officer = officer is not None
                officer = Officer.objects.filter(is_active=True, category=category, ward=ward).first()
                if officer and status == Complaint.Status.SUBMITTED:
                    status = Complaint.Status.ASSIGNED
                elif officer is None and had_officer and status == Complaint.Status.ASSIGNED:
                    status = Complaint.Status.SUBMITTED
            status_changed = status != complaint.status
            if status_changed:
                changes.append(f'Status changed to {Complaint.Status(status).label}.')
            if officer != complaint.officer:
                changes.append(f'Assigned to {officer.name}.' if officer else 'Officer removed.')
            if changes or note:
                complaint.category, complaint.ward = category, ward
                complaint.status, complaint.officer = status, officer
                complaint.save()
                ComplaintUpdate.objects.create(
                    complaint=complaint, status=status, author=request.user,
                    note=' '.join(changes + ([note] if note else [])),
                    is_public=bool(share_note and note),
                )
                # Sent after saving: a failed SMS is logged and reported, never undoes the update.
                if status_changed and notify.status_sms_recipient(complaint):
                    if notify.notify_status_change(complaint):
                        messages.success(request, _('Complaint %(reference)s updated. The citizen was sent an SMS about '
                                                    'the new status.') % {'reference': complaint.reference})
                    else:
                        messages.warning(request, _('Complaint %(reference)s updated, but the SMS to the citizen could '
                                                    'not be sent. See the server log.') % {'reference': complaint.reference})
                else:
                    messages.success(request, _('Complaint %(reference)s updated.') % {'reference': complaint.reference})
            else:
                messages.info(request, _('Nothing changed.'))
            return redirect(complaint)
    else:
        form = StatusUpdateForm(initial={
            'category': complaint.category, 'ward': complaint.ward,
            'status': complaint.status, 'officer': complaint.officer,
        })

    return render(request, 'grievances/complaint_detail.html', {
        'complaint': complaint,
        'form': form,
        'timeline': _timeline(complaint),
        'activity': complaint.updates.select_related('author').order_by('-created_at', '-id'),
    })


@staff_required
@permission_required('grievances.delete_complaint', raise_exception=True)
@require_POST
def complaint_delete(request, reference):
    """
    Permanently delete a complaint with its history and uploaded files (models.delete_complaint_files).
    Needs the "Can delete complaint" permission (superusers have it) and a ticked confirmation box.
    """
    complaint = get_object_or_404(Complaint, reference=reference)
    if request.POST.get('confirm') != 'yes':
        messages.error(request, _('Tick “I understand this cannot be undone” to delete the complaint.'))
        return redirect(complaint)
    complaint.delete()
    logger.warning('Complaint %s was deleted by %s.', reference, request.user.get_username())
    messages.success(request, _('Complaint %(reference)s was deleted.') % {'reference': reference})
    return redirect('grievances:complaint_list')


# URL name -> Complaint file field. The files hold citizens' personal details, so they are never public.
COMPLAINT_FILES = {'photo': 'attachment', 'previous-copy': 'previous_copy'}


def complaint_file(request, reference, kind):
    """
    A complaint's photo or earlier-complaint copy, for corporation staff or for the citizen who filed
    it (OTP-verified phone equal to citizen_contact). Another citizen gets 404, like /track/<ref>/;
    anyone signed in as neither is sent to the login page.
    """
    user = request.user
    if user.is_authenticated and user.is_active and user.is_staff:
        complaints = Complaint.objects.all()
    elif request.session.get(CITIZEN_SESSION_KEY):
        complaints = _citizen_complaints(request)
    else:
        return redirect_to_login(request.get_full_path())
    if kind not in COMPLAINT_FILES:
        raise Http404
    complaint = get_object_or_404(complaints, reference=reference)
    upload = getattr(complaint, COMPLAINT_FILES[kind])
    if not upload or not upload.storage.exists(upload.name):
        raise Http404
    response = FileResponse(upload.open('rb'), filename=os.path.basename(upload.name))
    response['Cache-Control'] = 'private, no-store'
    return response


# ---------- Officers ----------

@staff_required
def officers(request):
    form = OfficerFilterForm(request.GET)
    people = Officer.objects.filter(is_active=True).select_related('category', 'ward').annotate(
        assigned=Count('complaints'),
        resolved=Count('complaints', filter=Q(complaints__status=Complaint.Status.RESOLVED)),
    ).annotate(open=F('assigned') - F('resolved'))

    if form.is_valid():
        data = form.cleaned_data
        if data['q']:
            q = data['q']
            people = people.filter(Q(name__icontains=q) | Q(designation__icontains=q) |
                                   Q(ward__area__icontains=q) | Q(ward__name__icontains=q))
        if data['category']:
            people = people.filter(category=data['category'])
        if data['ward']:
            people = people.filter(ward=data['ward'])
        sort = data['sort'] or 'rate'
    else:
        sort = 'rate'

    if sort == 'name':
        people = people.order_by('name')
    elif sort == 'assigned':
        people = people.order_by('-assigned', 'name')
    elif sort == 'open':
        people = people.order_by('-open', 'name')
    else:
        people = people.annotate(
            rate=Cast('resolved', FloatField()) / Cast('assigned', FloatField())
        ).order_by(F('rate').desc(nulls_last=True), 'name')

    people = list(people)
    for officer in people:
        officer.rate = round(officer.resolved / officer.assigned * 100) if officer.assigned else 0

    totals = Officer.objects.filter(is_active=True).aggregate(
        count=Count('id', distinct=True),
        assigned=Count('complaints'),
        resolved=Count('complaints', filter=Q(complaints__status=Complaint.Status.RESOLVED)),
    )
    return render(request, 'grievances/officers.html', {
        'form': form,
        'officers': people,
        'view': 'table' if request.GET.get('view') == 'table' else 'cards',
        'totals': totals,
        'open_total': totals['assigned'] - totals['resolved'],
        'average_rate': round(totals['resolved'] / totals['assigned'] * 100) if totals['assigned'] else None,
        'any_officers': Officer.objects.filter(is_active=True).exists(),
    })


# ---------- Help pages ----------

@staff_required
def help_desk(request):
    reference = request.GET.get('ref', '').strip().upper()
    tracked, track_error = None, ''
    if reference:
        if not REFERENCE_RE.match(reference):
            track_error = _('Complaint IDs look like CC-2026-00042.')
        else:
            tracked = Complaint.objects.select_related('category', 'ward').filter(reference=reference).first()
            if not tracked:
                track_error = _('No complaint with ID %(reference)s was found.') % {'reference': reference}
    return render(request, 'grievances/help_desk.html', {
        'categories': Category.objects.all(),
        'reference': reference,
        'tracked': tracked,
        'track_error': track_error,
        'latest': Complaint.objects.first(),
    })


@staff_required
def help_support(request):
    if request.method == 'POST':
        form = SupportEnquiryForm(request.POST)
        if form.is_valid():
            enquiry = form.save()
            messages.success(request, _('Thank you. Your enquiry %(reference)s has been recorded.')
                             % {'reference': enquiry.reference})
            return redirect('grievances:help_support')
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = SupportEnquiryForm()
    return render(request, 'grievances/help_support.html', {
        'form': form,
        'recent': SupportEnquiry.objects.all()[:5],
    })


# ---------- Citizen ----------

LAST_CITIZEN_COMPLAINT_KEY = 'citizen_last_complaint'


def _known_taluks():
    return (Complaint.objects.exclude(taluk='').order_by('taluk')
            .values_list('taluk', flat=True).distinct()[:50])


def _save_citizen_complaint(form, phone):
    """Save a valid CitizenComplaintForm for the OTP-verified phone number, with its first history entry."""
    complaint = form.save(commit=False)
    complaint.citizen_contact = phone
    complaint.status = Complaint.Status.SUBMITTED
    complaint.save()
    ComplaintUpdate.objects.create(
        complaint=complaint, status=Complaint.Status.SUBMITTED,
        note='Complaint registered by the citizen through the portal (mobile number verified by OTP).',
        is_public=True,
    )
    return complaint


def _citizen_complaints(request):
    """
    The only complaints a citizen may see: those whose contact is their OTP-verified number.
    Every citizen-facing view must look complaints up through this queryset.
    """
    return Complaint.objects.filter(citizen_contact=request.session[CITIZEN_SESSION_KEY])


@citizen_required
def complaint_registration(request):
    phone = request.session[CITIZEN_SESSION_KEY]
    if request.method == 'POST':
        form = CitizenComplaintForm(request.POST, request.FILES)
        if form.is_valid():
            complaint = _save_citizen_complaint(form, phone)
            request.session[LAST_CITIZEN_COMPLAINT_KEY] = complaint.reference
            return redirect(reverse('grievances:complaint_registration') + '?submitted=1')
    else:
        form = CitizenComplaintForm()

    submitted = None
    if request.method == 'GET' and request.GET.get('submitted'):
        submitted = _citizen_complaints(request).filter(
            reference=request.session.get(LAST_CITIZEN_COMPLAINT_KEY, '')).first()

    mine = _citizen_complaints(request)
    return render(request, 'grievances/complaint_registration.html', {
        'form': form,
        'submitted': submitted,
        'masked_phone': mask_phone(phone),
        'my_complaints': mine[:5],
        'my_complaint_count': mine.count(),
        'taluks': _known_taluks(),
    })


@citizen_required
def track_list(request):
    """Every complaint filed with the citizen's verified mobile number, newest first."""
    complaints = _citizen_complaints(request).order_by('-created_at', '-id')
    return render(request, 'grievances/track_list.html', {
        'page': Paginator(complaints, PAGE_SIZE).get_page(request.GET.get('page')),
        'masked_phone': mask_phone(request.session[CITIZEN_SESSION_KEY]),
    })


@citizen_required
def track_detail(request, reference):
    # 404 (not 403) for someone else's complaint, so references can't be probed.
    complaint = get_object_or_404(_citizen_complaints(request).select_related('category', 'ward'),
                                  reference=reference)
    return render(request, 'grievances/track_detail.html', {
        'complaint': complaint,
        'timeline': _timeline(complaint),
        # Every status step; the note text only where staff marked it for the citizen.
        'updates': complaint.updates.order_by('-created_at', '-id'),
        'masked_phone': mask_phone(request.session[CITIZEN_SESSION_KEY]),
    })


@citizen_required
def citizen_complaint_page(request):
    """nagar-seva/filed-complaint.html: the standalone citizen form, which posts to api_complaints."""
    return render(request, 'filed-complaint.html', {'taluks': _known_taluks()})


@require_POST
def api_complaints(request):
    """
    Register a citizen complaint from multipart form data (the CitizenComplaintForm field names).
    201 {"complaint": {...}}, 400 {"errors": {field: [messages]}}, or 401 {"error", "login_url"}
    when the mobile number hasn't been verified. CSRF protected like every other POST.
    """
    phone = request.session.get(CITIZEN_SESSION_KEY)
    if not phone:
        return JsonResponse({'error': _('Please verify your mobile number to continue.'),
                             'login_url': reverse('accounts:login')}, status=401)
    form = CitizenComplaintForm(request.POST, request.FILES)
    if not form.is_valid():
        errors = {field: [error['message'] for error in field_errors]
                  for field, field_errors in form.errors.get_json_data().items()}
        return JsonResponse({'errors': errors}, status=400)

    complaint = _save_citizen_complaint(form, phone)
    request.session[LAST_CITIZEN_COMPLAINT_KEY] = complaint.reference
    return JsonResponse({'complaint': {
        'reference': complaint.reference,
        'status': complaint.get_status_display(),
        'submitted_at': timezone.localtime(complaint.created_at).isoformat(),
        'citizen_name': complaint.citizen_name,
        'taluk': complaint.taluk,
        'location': complaint.location,
        'attachment': os.path.basename(complaint.attachment.name) if complaint.attachment else None,
        'previously_reported': complaint.previously_reported,
        'previous_reference': complaint.previous_reference,
        'previous_copy': os.path.basename(complaint.previous_copy.name) if complaint.previous_copy else None,
    }}, status=201)
