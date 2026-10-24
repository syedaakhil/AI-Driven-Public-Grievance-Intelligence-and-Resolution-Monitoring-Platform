from django.db import models
from django.utils import timezone


class KnowledgeRecord(models.Model):
    """
    One row of data sent through the ingestion API (POST /api/ingest/), e.g. a scraped page or a
    row of a CSV file. The chatbot searches these alongside the complaints (sources.IngestedDataSource).
    """

    dataset = models.SlugField(max_length=50, db_index=True,
                               help_text='Which upload this came from, e.g. "ward-news".')
    external_id = models.CharField(max_length=100, blank=True,
                                   help_text='The sender\'s own ID for the record, if it had one.')
    title = models.CharField(max_length=200, blank=True)
    content = models.TextField(blank=True, help_text='All text values of the record, joined.')
    data = models.JSONField(default=dict, help_text='The record exactly as received (after cleaning).')
    # sha256 of the external ID, or of the cleaned data: the same record sent twice is stored once.
    fingerprint = models.CharField(max_length=64)
    search_text = models.TextField(blank=True, help_text='Lower-case text used for searching.')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['dataset', 'fingerprint'], name='unique_record_per_dataset'),
        ]

    def __str__(self):
        return f'{self.dataset}: {self.title or self.external_id or self.pk}'


class EmailLog(models.Model):
    """Every email the assistant tried to send, sent or not."""

    class Kind(models.TextChoices):
        DAILY_REPORT = 'daily_report', 'Daily report'
        ON_DEMAND = 'on_demand', 'Requested in chat'

    class Status(models.TextChoices):
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    kind = models.CharField(max_length=20, choices=Kind.choices)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices)
    error = models.TextField(blank=True)
    provider_message_id = models.CharField(max_length=200, blank=True)
    record_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.get_kind_display()} · {self.get_status_display()} · {self.created_at:%d %b %Y %H:%M}'


class ReportRun(models.Model):
    """
    One row per day for the 9 AM report. The date is unique, so a second run on the same day (server
    restart, a double trigger) sees the row and does not send again.
    """

    class Status(models.TextChoices):
        SENDING = 'sending', 'Sending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    report_date = models.DateField(unique=True, help_text='The day in India (Asia/Kolkata).')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENDING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    complaint_count = models.PositiveIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=1)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-report_date']

    def __str__(self):
        return f'{self.report_date:%d %b %Y}: {self.get_status_display()}'
