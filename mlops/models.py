from django.conf import settings
from django.db import models
from django.utils import timezone


class Dataset(models.Model):
    """
    Records collected from one provider (see providers.py), keyed by the provider's slug. The row is
    also the lock that stops two "Collect data" jobs running for the same dataset at once.
    """
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    record_count = models.PositiveIntegerField(default=0)
    label_count = models.PositiveIntegerField(default=0)
    last_collected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['slug']

    def __str__(self):
        return self.name


class CollectionJob(models.Model):
    """One press of "Collect data": queued, then running, then succeeded or failed."""

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'

    ACTIVE = (Status.QUEUED, Status.RUNNING)

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name='jobs')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True, help_text='Last progress report.')
    records_total = models.PositiveIntegerField(null=True, blank=True, help_text='Empty when the source cannot count.')
    records_processed = models.PositiveIntegerField(default=0)
    records_new = models.PositiveIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'Job {self.pk} · {self.dataset.slug} · {self.status}'

    @property
    def is_active(self):
        return self.status in self.ACTIVE

    @property
    def progress(self):
        """0–100, or None while the total isn't known yet."""
        if self.status == self.Status.SUCCEEDED:
            return 100
        if not self.records_total:
            return None
        return min(100, round(self.records_processed * 100 / self.records_total))


class DatasetRecord(models.Model):
    """One collected example: cleaned text plus its label (e.g. a complaint's service category)."""
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name='records')
    source_id = models.CharField(max_length=100, help_text='The ID of the record in its source.')
    text = models.TextField()
    label = models.CharField(max_length=100, blank=True, db_index=True)
    job = models.ForeignKey(CollectionJob, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                            help_text='The last job that saw this record.')
    collected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['dataset', 'source_id']
        constraints = [
            models.UniqueConstraint(fields=['dataset', 'source_id'], name='unique_source_record_per_dataset'),
        ]

    def __str__(self):
        return f'{self.dataset.slug}:{self.source_id}'


class TrainingRun(models.Model):
    """One run of mlops.ml.train.train() ("manage.py train_model"): its metrics and where the model went."""

    class Status(models.TextChoices):
        RUNNING = 'running', 'Running'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name='training_runs')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    record_count = models.PositiveIntegerField(default=0)
    metrics = models.JSONField(default=dict, blank=True)
    artifact_path = models.CharField(max_length=500, blank=True)
    tensorflow_version = models.CharField(max_length=30, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at', '-id']

    def __str__(self):
        return f'Training {self.pk} · {self.dataset.slug} · {self.status}'
