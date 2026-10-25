import re

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from django.utils.text import Truncator
from django.utils.translation import gettext, gettext_lazy as _

from accounts.otp import normalize_phone

from .models import (Category, Complaint, Department, EmployeeReport, Officer, SupportEnquiry, Ward,
                     WorkAssignment)

NAME_RE = re.compile(r"^[A-Za-z஀-௿ऀ-ॿ .'-]+$")
PHOTO_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
MAX_CITIZEN_FILE_BYTES = 5 * 1024 * 1024
PREVIOUS_COPY_TYPES = {'application/pdf', 'image/jpeg', 'image/png'}
PREVIOUS_REFERENCE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 /.-]*$')

PRIORITY_RANK = Case(
    When(priority=Complaint.Priority.HIGH, then=Value(0)),
    When(priority=Complaint.Priority.MEDIUM, then=Value(1)),
    default=Value(2),
    output_field=IntegerField(),
)
STATUS_RANK = Case(
    When(status=Complaint.Status.SUBMITTED, then=Value(0)),
    When(status=Complaint.Status.ASSIGNED, then=Value(1)),
    When(status=Complaint.Status.IN_PROGRESS, then=Value(2)),
    default=Value(3),
    output_field=IntegerField(),
)


def style_fields(form):
    """Give each widget the CSS class the design uses."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, (forms.RadioSelect, forms.CheckboxInput, forms.FileInput)):
            continue
        if isinstance(widget, forms.Select):
            widget.attrs.setdefault('class', 'select')
        elif isinstance(widget, forms.Textarea):
            widget.attrs.setdefault('class', 'textarea')
        else:
            widget.attrs.setdefault('class', 'input')


def _check_upload(upload, allowed_types, label):
    """Size and type check for a freshly uploaded file (not one already saved)."""
    if upload and hasattr(upload, 'content_type'):
        if upload.size > MAX_CITIZEN_FILE_BYTES:
            raise ValidationError(gettext('“%(name)s” is larger than the 5 MB limit. Please choose a smaller file.')
                                  % {'name': upload.name})
        if upload.content_type not in allowed_types:
            raise ValidationError(gettext('“%(name)s” is not an accepted format. Please choose a %(formats)s file.')
                                  % {'name': upload.name, 'formats': label})
    return upload


class CitizenComplaintForm(forms.ModelForm):
    """The public form a citizen fills in after verifying their mobile number."""

    YES_NO = [('yes', _('Yes')), ('no', _('No'))]

    reported_before = forms.ChoiceField(
        choices=YES_NO, widget=forms.RadioSelect,
        label=_('Have You Previously Reported This Problem?'),
        error_messages={'required': _('Select Yes or No.')},
    )
    declaration = forms.BooleanField(
        error_messages={'required': _('Please confirm that the information you have provided is accurate.')},
    )

    class Meta:
        model = Complaint
        fields = [
            'citizen_name', 'citizen_address', 'taluk', 'description', 'attachment', 'location', 'impact',
            'previous_reference', 'previous_copy',
        ]
        # The template writes its own inputs (see complaint_registration.html); this form only validates.

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('citizen_address', 'taluk', 'location', 'impact'):
            self.fields[name].required = True
        messages = {
            'citizen_name': _('Enter your full name.'),
            'citizen_address': _('Enter your residential address.'),
            'taluk': _('Enter your taluk.'),
            'description': _('Describe the problem you are facing.'),
            'location': _('Enter the area affected by the problem.'),
            'impact': _('Explain how this problem affects you.'),
        }
        for name, message in messages.items():
            self.fields[name].error_messages['required'] = message

    def clean_citizen_name(self):
        name = self.cleaned_data['citizen_name'].strip()
        if len(name) < 2:
            raise ValidationError(gettext('Your name must have at least 2 characters.'))
        if not NAME_RE.match(name):
            raise ValidationError(gettext('Use letters, spaces, full stops, apostrophes or hyphens in your name.'))
        return name

    def clean_citizen_address(self):
        address = self.cleaned_data['citizen_address'].strip()
        if len(address) < 10:
            raise ValidationError(gettext('Please enter your full address, including house number and street.'))
        if len(address) > 500:
            raise ValidationError(gettext('Please keep the address under 500 characters.'))
        return address

    def clean_taluk(self):
        taluk = self.cleaned_data['taluk'].strip()
        if len(taluk) < 2 or not any(char.isalpha() for char in taluk):
            raise ValidationError(gettext('Enter a valid taluk name.'))
        return taluk

    def clean_description(self):
        description = self.cleaned_data['description'].strip()
        if len(description) < 20:
            raise ValidationError(gettext('Please describe the problem in a little more detail (at least 20 characters).'))
        if len(description) > 2000:
            raise ValidationError(gettext('Please keep the description under 2000 characters.'))
        return description

    def clean_location(self):
        location = self.cleaned_data['location'].strip()
        if len(location) < 3:
            raise ValidationError(gettext('Please enter the locality, street, village, ward or landmark.'))
        return location

    def clean_impact(self):
        impact = self.cleaned_data['impact'].strip()
        if len(impact) < 10:
            raise ValidationError(gettext('Please explain the effect in a little more detail (at least 10 characters).'))
        if len(impact) > 1000:
            raise ValidationError(gettext('Please keep this under 1000 characters.'))
        return impact

    def clean_attachment(self):
        return _check_upload(self.cleaned_data.get('attachment'), PHOTO_TYPES, gettext('JPG, JPEG, PNG or WEBP'))

    def clean_previous_reference(self):
        reference = self.cleaned_data['previous_reference'].strip()
        if reference and not PREVIOUS_REFERENCE_RE.match(reference):
            raise ValidationError(gettext('Use only letters, numbers, spaces, hyphens, full stops or slashes.'))
        return reference

    def clean_previous_copy(self):
        return _check_upload(self.cleaned_data.get('previous_copy'), PREVIOUS_COPY_TYPES, gettext('PDF, JPG, JPEG or PNG'))

    def clean(self):
        cleaned = super().clean()
        # Details of an earlier complaint only count when the citizen answered Yes.
        if cleaned.get('reported_before') != 'yes':
            cleaned['previous_reference'] = ''
            cleaned['previous_copy'] = None
            self.errors.pop('previous_reference', None)
            self.errors.pop('previous_copy', None)
        return cleaned

    def save(self, commit=True):
        complaint = super().save(commit=False)
        complaint.previously_reported = self.cleaned_data['reported_before'] == 'yes'
        if not complaint.previously_reported:
            complaint.previous_reference = ''
            complaint.previous_copy = None
        complaint.title = Truncator(complaint.description.splitlines()[0]).chars(80)
        complaint.channel = Complaint.Channel.PORTAL
        complaint.filed_by_citizen = True
        if commit:
            complaint.save()
        return complaint


class StatusUpdateForm(forms.Form):
    category = forms.ModelChoiceField(Category.objects.all(), required=False, empty_label=_('Not assigned'),
                                      label=_('Service category'))
    ward = forms.ModelChoiceField(Ward.objects.all(), required=False, empty_label=_('Not assigned'), label=_('Ward'))
    status = forms.ChoiceField(choices=Complaint.Status.choices, label=_('Status'))
    officer = forms.ModelChoiceField(
        queryset=Officer.objects.exclude(status=Officer.Status.INACTIVE).select_related('ward'),
        required=False,
        empty_label=_('Not assigned'),
        label=_('Employee in charge'),
    )
    note = forms.CharField(
        required=False, max_length=1000, label=_('Note'),
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': _('What was done? (optional)')}),
    )
    share_note = forms.BooleanField(
        required=False, label=_('Show this note to the citizen'),
        help_text=_('The citizen always sees the status. Tick this only for notes written for them.'),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['officer'].label_from_instance = lambda employee: f'{employee.name} · {employee.employee_id}'
        style_fields(self)


class ComplaintFilterForm(forms.Form):
    SORTS = [
        ('newest', _('Newest first')),
        ('oldest', _('Oldest first')),
        ('priority', _('Priority')),
        ('status', _('Status')),
        ('ward', _('Ward')),
    ]

    q = forms.CharField(required=False, label=_('Search'), widget=forms.TextInput(
        attrs={'placeholder': _('Search by complaint ID or title'), 'type': 'search'}))
    category = forms.ModelChoiceField(Category.objects.all(), required=False, empty_label=_('All categories'))
    ward = forms.ModelChoiceField(Ward.objects.all(), required=False, empty_label=_('All wards'))
    status = forms.ChoiceField(choices=[('', _('All statuses'))] + Complaint.Status.choices, required=False)
    priority = forms.ChoiceField(choices=[('', _('All priorities'))] + Complaint.Priority.choices, required=False)
    channel = forms.ChoiceField(choices=[('', _('All channels'))] + Complaint.Channel.choices, required=False)
    sort = forms.ChoiceField(choices=SORTS, required=False, label=_('Sort by'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)

    def filter(self, queryset):
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        if data['q']:
            queryset = queryset.filter(Q(reference__icontains=data['q']) | Q(title__icontains=data['q']))
        for name in ('category', 'ward', 'status', 'priority', 'channel'):
            if data[name]:
                queryset = queryset.filter(**{name: data[name]})
        sort = data['sort'] or 'newest'
        if sort == 'oldest':
            return queryset.order_by('created_at')
        if sort == 'priority':
            return queryset.annotate(rank=PRIORITY_RANK).order_by('rank', '-created_at')
        if sort == 'status':
            return queryset.annotate(rank=STATUS_RANK).order_by('rank', '-created_at')
        if sort == 'ward':
            return queryset.order_by('ward__number', '-created_at')
        return queryset.order_by('-created_at')


class FiledComplaintFilterForm(forms.Form):
    """Search and filters for the "Filed complaint" page (complaints registered by citizens)."""

    REVIEW = [('', _('All complaints')), ('pending', _('Needs category & ward')), ('done', _('Reviewed'))]
    SORTS = [('newest', _('Newest first')), ('oldest', _('Oldest first'))]

    q = forms.CharField(required=False, label=_('Search'), widget=forms.TextInput(
        attrs={'placeholder': _('ID, citizen name, taluk or area'), 'type': 'search'}))
    status = forms.ChoiceField(choices=[('', _('All statuses'))] + Complaint.Status.choices, required=False)
    review = forms.ChoiceField(choices=REVIEW, required=False, label=_('Review'))
    sort = forms.ChoiceField(choices=SORTS, required=False, label=_('Sort by'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)

    def filter(self, queryset):
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        if data['q']:
            q = data['q']
            queryset = queryset.filter(
                Q(reference__icontains=q) | Q(citizen_name__icontains=q) | Q(title__icontains=q) |
                Q(taluk__icontains=q) | Q(location__icontains=q))
        if data['status']:
            queryset = queryset.filter(status=data['status'])
        if data['review'] == 'pending':
            queryset = queryset.filter(Complaint.needs_review_filter())
        elif data['review'] == 'done':
            queryset = queryset.exclude(Complaint.needs_review_filter())
        return queryset.order_by('created_at' if data['sort'] == 'oldest' else '-created_at')


class OfficerFilterForm(forms.Form):
    """Search and filters on the Employees page."""

    ASSIGNMENT_CHOICES = [('', _('Assigned or available')), ('assigned', _('Assigned')),
                          ('available', _('Available'))]
    SORTS = [
        ('name', _('Name (A–Z)')),
        ('tasks', _('Most open tasks')),
        ('complaints', _('Most open complaints')),
        ('newest', _('Newest first')),
    ]

    q = forms.CharField(required=False, label=_('Search employees'), widget=forms.TextInput(
        attrs={'placeholder': _('Name, employee ID or ward'), 'type': 'search'}))
    department = forms.ModelChoiceField(Department.objects.all(), required=False, empty_label=_('All departments'),
                                        label=_('Department'))
    ward = forms.ModelChoiceField(Ward.objects.all(), required=False, empty_label=_('All wards'), label=_('Ward'))
    status = forms.ChoiceField(choices=[('', _('Active and on leave'))] + Officer.Status.choices, required=False,
                               label=_('Status'))
    assignment = forms.ChoiceField(choices=ASSIGNMENT_CHOICES, required=False, label=_('Assignment'))
    sort = forms.ChoiceField(choices=SORTS, required=False, label=_('Sort by'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)

    def filter(self, queryset):
        """Expects the open_tasks annotation from views.employee_queryset()."""
        data = self.cleaned_data if self.is_valid() else {}
        q = data.get('q', '').strip()
        if q:
            ward_number = q.lower().removeprefix('ward').strip()
            ward_match = Q(ward__name__icontains=q) | Q(ward__area__icontains=q)
            if ward_number.isdigit():
                ward_match |= Q(ward__number=int(ward_number))
            queryset = queryset.filter(Q(name__icontains=q) | Q(employee_id__icontains=q) | ward_match)
        for name in ('department', 'ward'):
            if data.get(name):
                queryset = queryset.filter(**{name: data[name]})
        if data.get('status'):
            queryset = queryset.filter(status=data['status'])
        else:
            queryset = queryset.exclude(status=Officer.Status.INACTIVE)
        if data.get('assignment') == 'assigned':
            queryset = queryset.filter(open_tasks__gt=0)
        elif data.get('assignment') == 'available':
            queryset = queryset.filter(open_tasks=0)
        sort = data.get('sort') or 'name'
        if sort == 'tasks':
            return queryset.order_by('-open_tasks', 'name')
        if sort == 'complaints':
            return queryset.order_by('-open_complaints', 'name')
        if sort == 'newest':
            return queryset.order_by('-date_joined', '-id')
        return queryset.order_by('name')


class EmployeeForm(forms.ModelForm):
    """Add (and edit) an employee. The employee ID is made automatically and never changes."""

    class Meta:
        model = Officer
        fields = ['name', 'department', 'designation', 'ward', 'phone', 'email', 'photo']
        labels = {'name': _('Full name'), 'department': _('Department'), 'designation': _('Designation'),
                  'ward': _('Ward'), 'phone': _('Mobile number'), 'email': _('Email'), 'photo': _('Photo')}
        widgets = {
            'name': forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': _('e.g. Priya Raman')}),
            'designation': forms.TextInput(attrs={'placeholder': _('e.g. Assistant Engineer')}),
            'phone': forms.TextInput(attrs={'inputmode': 'tel', 'placeholder': _('10-digit mobile number')}),
            'email': forms.EmailInput(attrs={'placeholder': 'name@example.com'}),
            'photo': forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['department'].empty_label = _('Choose a department')
        self.fields['ward'].empty_label = _('Choose a ward')
        style_fields(self)

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        if len(name) < 2:
            raise ValidationError(gettext('The name must have at least 2 characters.'))
        if not NAME_RE.match(name):
            raise ValidationError(gettext('Use letters, spaces, full stops, apostrophes or hyphens in the name.'))
        return name

    def clean_designation(self):
        designation = ' '.join(self.cleaned_data['designation'].split())
        if len(designation) < 2:
            raise ValidationError(gettext('Enter the designation, for example Assistant Engineer.'))
        return designation

    def clean_phone(self):
        phone = self.cleaned_data['phone'].strip()
        if not phone:
            return ''
        normalized = normalize_phone(phone)
        if not normalized:
            raise ValidationError(gettext('Enter a valid 10-digit mobile number starting with 6, 7, 8 or 9.'))
        return normalized

    def clean_photo(self):
        return _check_upload(self.cleaned_data.get('photo'), PHOTO_TYPES, gettext('JPG, JPEG, PNG or WEBP'))


class EmployeeEditForm(EmployeeForm):
    class Meta(EmployeeForm.Meta):
        fields = EmployeeForm.Meta.fields + ['status']
        labels = {**EmployeeForm.Meta.labels, 'status': _('Status')}


class EmployeeReportForm(forms.ModelForm):
    """A complaint about an employee."""

    class Meta:
        model = EmployeeReport
        fields = ['reason', 'details']
        labels = {'reason': _('Reason'), 'details': _('Details')}
        widgets = {'details': forms.Textarea(attrs={'rows': 3, 'maxlength': 1000,
                                                    'placeholder': _('What happened, and when?')})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['reason'].choices = [('', gettext('Choose a reason'))] + list(EmployeeReport.Reason.choices)
        style_fields(self)

    def clean_details(self):
        details = self.cleaned_data['details'].strip()
        if len(details) < 10:
            raise ValidationError(gettext('Please describe what happened (at least 10 characters).'))
        return details


class WorkAssignmentForm(forms.ModelForm):
    """
    Give an employee work: an open complaint (typed or picked by its ID) or a free-form task.
    For a complaint the title can be left empty; it is taken from the complaint.
    """

    complaint_ref = forms.CharField(
        required=False, max_length=20, label=_('Complaint ID (optional)'),
        widget=forms.TextInput(attrs={'list': 'open-complaints', 'autocomplete': 'off',
                                      'placeholder': _('e.g. CC-2026-00042')}))

    class Meta:
        model = WorkAssignment
        fields = ['title', 'description', 'priority', 'due_date']
        labels = {'title': _('Task'), 'description': _('Instructions'), 'priority': _('Priority'),
                  'due_date': _('Due date')}
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': _('e.g. Inspect the drain on Gandhi Street')}),
            'description': forms.Textarea(attrs={'rows': 2, 'maxlength': 1000}),
            'due_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['title'].required = False
        self.complaint = None
        style_fields(self)

    def clean_complaint_ref(self):
        reference = self.cleaned_data['complaint_ref'].strip().upper()
        if not reference:
            return ''
        self.complaint = Complaint.objects.filter(reference=reference).first()
        if self.complaint is None:
            raise ValidationError(gettext('No complaint with ID %(reference)s was found.') % {'reference': reference})
        if not self.complaint.is_open:
            raise ValidationError(gettext('Complaint %(reference)s is already resolved.') % {'reference': reference})
        return reference

    def clean_due_date(self):
        due = self.cleaned_data.get('due_date')
        if due and due < timezone.localdate():
            raise ValidationError(gettext('Choose today or a later date.'))
        return due

    def clean(self):
        data = super().clean()
        data['title'] = ' '.join((data.get('title') or '').split())
        if not data['title']:
            if self.complaint is not None:
                data['title'] = Truncator(f'{self.complaint.reference}: {self.complaint.title}').chars(120)
            elif not self.errors.get('complaint_ref'):
                self.add_error('title', gettext('Describe the task, or enter an open complaint ID.'))
        return data

    def save(self, commit=True):
        assignment = super().save(commit=False)
        assignment.title = self.cleaned_data['title']
        assignment.complaint = self.complaint
        if commit:
            assignment.save()
        return assignment


class SupportEnquiryForm(forms.ModelForm):
    class Meta:
        model = SupportEnquiry
        fields = ['name', 'email', 'topic', 'message']
        labels = {'name': _('Your name'), 'email': _('Email'), 'topic': _('Topic'), 'message': _('Message')}
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': _('e.g. Arjun'), 'autocomplete': 'off'}),
            'email': forms.EmailInput(attrs={'placeholder': 'name@example.com', 'autocomplete': 'off'}),
            'message': forms.Textarea(attrs={'rows': 5, 'maxlength': 800,
                                             'placeholder': _('How can we help? (at least 15 characters)')}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['topic'].choices = [('', gettext('Choose a topic'))] + list(SupportEnquiry.Topic.choices)
        style_fields(self)

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if len(name) < 2:
            raise ValidationError(gettext('Please enter your name.'))
        return name

    def clean_message(self):
        message = self.cleaned_data['message'].strip()
        if len(message) < 15:
            raise ValidationError(gettext('Message should be at least 15 characters (%(count)s so far).')
                                  % {'count': len(message)})
        return message
