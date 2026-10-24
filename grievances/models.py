from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _


class Category(models.Model):
    """A civic service that complaints are filed under, and the department that handles it."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=60)
    department = models.CharField(max_length=100)
    target_time = models.CharField(max_length=40, help_text='Target resolution time, e.g. "48 hours".')
    description = models.CharField(max_length=200, blank=True)
    icon = models.CharField(max_length=20, default='file')
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'categories'

    def __str__(self):
        return gettext(self.name)   # Hindi version from reference_i18n.py when there is one


class Ward(models.Model):
    number = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=60)
    area = models.CharField(max_length=60)
    latitude = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    longitude = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)

    class Meta:
        ordering = ['number']

    def __str__(self):
        # Ward names are reference data with Hindi versions in reference_i18n.py
        return gettext('Ward %(number)s – %(name)s') % {'number': f'{self.number:02d}', 'name': gettext(self.name)}


class Officer(models.Model):
    name = models.CharField(max_length=80)
    designation = models.CharField(max_length=80)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='officers')
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, related_name='officers')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.designation})'


class Complaint(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = 'submitted', _('Submitted')
        ASSIGNED = 'assigned', _('Assigned')
        IN_PROGRESS = 'in_progress', _('In progress')
        RESOLVED = 'resolved', _('Resolved')

    class Priority(models.TextChoices):
        HIGH = 'high', _('High')
        MEDIUM = 'medium', _('Medium')
        LOW = 'low', _('Low')

    class Channel(models.TextChoices):
        PORTAL = 'portal', _('Portal')
        WHATSAPP = 'whatsapp', _('WhatsApp')
        X = 'x', _('X (Twitter)')
        FACEBOOK = 'facebook', _('Facebook')
        INSTAGRAM = 'instagram', _('Instagram')
        PHONE = 'phone', _('Phone call')
        WALK_IN = 'walk_in', _('Walk-in')
        EMAIL = 'email', _('Email')

    OPEN_STATUSES = (Status.SUBMITTED, Status.ASSIGNED, Status.IN_PROGRESS)

    reference = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    title = models.CharField(max_length=120)
    description = models.TextField()
    # Citizens don't pick these; a corporation admin sets them when reviewing the complaint.
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='complaints',
                                 null=True, blank=True)
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, related_name='complaints',
                             null=True, blank=True)
    location = models.CharField('Street / landmark', max_length=200, blank=True)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.PORTAL)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    citizen_name = models.CharField(max_length=80)
    citizen_contact = models.CharField(max_length=80, help_text='Email address or mobile number.')
    attachment = models.FileField(
        upload_to='complaints/%Y/%m/',
        blank=True,
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])],
    )
    # Filled in by citizens on the complaint registration page
    filed_by_citizen = models.BooleanField(default=False, db_index=True,
                                           help_text='Registered by the citizen after OTP sign-in.')
    citizen_address = models.TextField('Residential address', blank=True)
    taluk = models.CharField(max_length=80, blank=True)
    impact = models.TextField('Effect on the citizen', blank=True)
    previously_reported = models.BooleanField(null=True, blank=True)
    previous_reference = models.CharField('Previous complaint reference', max_length=50, blank=True)
    previous_copy = models.FileField(
        'Previous complaint copy',
        upload_to='complaints/previous/%Y/%m/',
        blank=True,
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])],
    )
    officer = models.ForeignKey(
        Officer, on_delete=models.SET_NULL, null=True, blank=True, related_name='complaints'
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.reference} {self.title}'

    def save(self, *args, **kwargs):
        if self.status == self.Status.RESOLVED and not self.resolved_at:
            self.resolved_at = timezone.now()
        elif self.status != self.Status.RESOLVED:
            self.resolved_at = None
        super().save(*args, **kwargs)
        if not self.reference:
            # Readable ID such as CC-2026-00042, based on the database id.
            self.reference = f'CC-{self.created_at:%Y}-{self.pk:05d}'
            type(self).objects.filter(pk=self.pk).update(reference=self.reference)

    def get_absolute_url(self):
        return reverse('grievances:complaint_detail', args=[self.reference])

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def needs_review(self):
        """A citizen complaint still waiting for a category and ward from the corporation."""
        return self.is_open and (self.category_id is None or self.ward_id is None)

    @classmethod
    def needs_review_filter(cls):
        """The same rule as `needs_review`, as a queryset filter."""
        return (models.Q(status__in=cls.OPEN_STATUSES)
                & (models.Q(category__isnull=True) | models.Q(ward__isnull=True)))


@receiver(post_delete, sender=Complaint)
def delete_complaint_files(sender, instance, **kwargs):
    """
    Uploaded files hold citizens' personal details, so they go with the complaint, whether it is
    deleted from the dashboard or from Django /admin/. Removed only once the database delete commits.
    """
    for upload in (instance.attachment, instance.previous_copy):
        if upload:
            transaction.on_commit(lambda storage=upload.storage, name=upload.name: storage.delete(name))


class ComplaintUpdate(models.Model):
    """One step in a complaint's history (status change and/or note)."""

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name='updates')
    status = models.CharField(max_length=20, choices=Complaint.Status.choices)
    note = models.TextField(blank=True)
    # The status step is always shown on the citizen's tracking page; the note only when this is set.
    is_public = models.BooleanField('Note shown to the citizen', default=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.complaint.reference}: {self.get_status_display()}'


class Notice(models.Model):
    """City news and service notices shown on the home dashboard."""

    ICONS = [
        ('droplet', 'Water'),
        ('road', 'Roads'),
        ('waves', 'Drainage'),
        ('trash', 'Waste'),
        ('megaphone', 'Announcement'),
    ]

    title = models.CharField(max_length=120)
    topic = models.CharField(max_length=40, help_text='Short label, e.g. "Water supply".')
    icon = models.CharField(max_length=20, choices=ICONS, default='megaphone')
    summary = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    published_on = models.DateField(default=timezone.localdate)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ['-published_on', '-id']

    def __str__(self):
        return self.title


class SupportEnquiry(models.Model):
    class Topic(models.TextChoices):
        DASHBOARD = 'dashboard', _('Using the dashboard')
        FILING = 'filing', _('Filing a complaint')
        TRACKING = 'tracking', _('Tracking a complaint')
        ACCESSIBILITY = 'accessibility', _('Accessibility or language')
        OTHER = 'other', _('Something else')

    reference = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    name = models.CharField(max_length=80)
    email = models.EmailField()
    topic = models.CharField(max_length=20, choices=Topic.choices)
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'support enquiries'

    def __str__(self):
        return f'{self.reference} {self.get_topic_display()}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f'SUP-{self.pk:05d}'
            type(self).objects.filter(pk=self.pk).update(reference=self.reference)
