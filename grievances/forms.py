import re

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils.text import Truncator
from django.utils.translation import gettext, gettext_lazy as _

from .models import Category, Complaint, Officer, SupportEnquiry, Ward

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
        queryset=Officer.objects.filter(is_active=True).select_related('category', 'ward'),
        required=False,
        empty_label=_('Not assigned'),
        label=_('Officer in charge'),
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
    SORTS = [
        ('rate', _('Resolution rate')),
        ('assigned', _('Assigned complaints')),
        ('open', _('Most open complaints')),
        ('name', _('Name (A–Z)')),
    ]

    q = forms.CharField(required=False, label=_('Search officers'), widget=forms.TextInput(
        attrs={'placeholder': _('Name, designation or area'), 'type': 'search'}))
    category = forms.ModelChoiceField(Category.objects.all(), required=False, empty_label=_('All departments'),
                                      label=_('Department'))
    ward = forms.ModelChoiceField(Ward.objects.all(), required=False, empty_label=_('All wards'))
    sort = forms.ChoiceField(choices=SORTS, required=False, label=_('Sort by'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].label_from_instance = lambda category: gettext(category.department)
        style_fields(self)


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
