"""
"Collect data" jobs: start one (at most one active job per dataset), run it in the background, and
mark jobs that stopped responding as failed.

Running a job again is always safe: records are upserted by (dataset, source_id), and records the
source no longer has are removed only after a job finishes successfully.
"""
import logging
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import OperationalError, connection, connections, transaction
from django.db.models import F
from django.utils import timezone
from django.utils import translation
from django.utils.translation import gettext as _

from .models import CollectionJob, Dataset, DatasetRecord
from .providers import ProviderError, get_provider

logger = logging.getLogger(__name__)

Status = CollectionJob.Status


class JobConflict(Exception):
    """A job for this dataset is already queued or running."""

    def __init__(self, job):
        super().__init__(f'Job {job.pk} is already {job.status}.')
        self.job = job


class UnknownDataset(Exception):
    pass


def start_collection(slug, user=None):
    """Queue a job for the dataset `slug` and hand it to the configured runner. Returns the job."""
    provider = get_provider(slug)
    if provider is None:
        raise UnknownDataset(slug)
    with transaction.atomic():
        dataset, _created = Dataset.objects.get_or_create(slug=slug, defaults={'name': str(provider.name)[:120]})
        # Lock the dataset row, so two clicks at the same moment can't both see "no active job".
        dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
        expire_stale_jobs(dataset)
        active = dataset.jobs.filter(status__in=CollectionJob.ACTIVE).first()
        if active:
            raise JobConflict(active)
        job = CollectionJob.objects.create(
            dataset=dataset, requested_by=user if user and user.is_authenticated else None)
    logger.info('Collection job %s queued for %s by %s', job.pk, slug, getattr(user, 'username', '-'))
    dispatch(job.pk)
    return job


def dispatch(job_pk):
    runner = settings.ML_JOB_RUNNER
    if runner == 'inline':
        run_job(job_pk)
    elif runner == 'thread':
        # After commit, so the thread's own database connection can see the new job.
        transaction.on_commit(lambda: threading.Thread(
            target=_run_in_thread, args=(job_pk,), name=f'collect-{job_pk}', daemon=True).start())
    # "command": left queued for "manage.py run_collection_jobs".


def _run_in_thread(job_pk):
    try:
        run_job(job_pk)
    finally:
        connections.close_all()


def run_queued_jobs():
    """Run every queued job, oldest first. Returns how many this call ran."""
    expire_stale_jobs()
    ran = 0
    for job_pk in CollectionJob.objects.filter(status=Status.QUEUED).order_by('created_at', 'pk').values_list('pk', flat=True):
        if run_job(job_pk) is not None:
            ran += 1
    return ran


def run_job(job_pk):
    """
    Claim a queued job and collect its dataset. Returns the finished job, or None when another
    runner claimed it first. Temporary failures (database hiccup, source busy) are retried up to
    ML_JOB_MAX_ATTEMPTS times.
    """
    now = timezone.now()
    claimed = CollectionJob.objects.filter(pk=job_pk, status=Status.QUEUED).update(
        status=Status.RUNNING, started_at=now, heartbeat_at=now)
    if not claimed:
        return None
    job = CollectionJob.objects.select_related('dataset').get(pk=job_pk)
    # Saved messages are English (msgids); the status API translates them for each viewer.
    with translation.override('en'):
        return _run_claimed(job)


def _run_claimed(job):
    max_attempts = max(1, settings.ML_JOB_MAX_ATTEMPTS)
    while True:
        CollectionJob.objects.filter(pk=job.pk).update(attempts=F('attempts') + 1)
        job.refresh_from_db(fields=['attempts'])
        try:
            _collect(job)
        except (ProviderError, OperationalError) as exc:
            temporary = isinstance(exc, OperationalError) or exc.temporary
            if temporary and job.attempts < max_attempts:
                logger.warning('Collection job %s: attempt %s failed (%s); trying again', job.pk, job.attempts, exc)
                time.sleep(2 ** job.attempts)
                continue
            if isinstance(exc, ProviderError):
                _fail(job, str(exc))
            else:
                logger.exception('Collection job %s: database error', job.pk)
                _fail(job, _('The database was not reachable. Press Collect data to try again.'))
        except Exception as exc:  # noqa: BLE001  (a job must always end in a clear state)
            logger.exception('Collection job %s failed', job.pk)
            _fail(job, _('Collection stopped because of an unexpected error (%(type)s). '
                         'The details are in the server log.') % {'type': type(exc).__name__})
        return job


def _collect(job):
    provider = get_provider(job.dataset.slug)
    if provider is None:
        raise ProviderError(_('This data source is no longer set up on the server.'))
    job.records_total = provider.count()
    job.records_processed = job.records_new = job.candidates_new = 0
    job.error = ''
    _save_progress(job, 'records_total', 'records_processed', 'records_new', 'candidates_new', 'error')
    for batch in provider.batches():
        _store_batch(job, batch)
        _save_progress(job, 'records_processed', 'records_new')
    # Sources that need a review first (the news agent) report how many new stories they found.
    job.candidates_new = getattr(provider, 'found_new', 0)
    _save_progress(job, 'candidates_new')
    with transaction.atomic():
        dataset = Dataset.objects.select_for_update().get(pk=job.dataset_id)
        # The source no longer has these: drop them, so the dataset mirrors the source.
        dataset.records.exclude(job=job).delete()
        dataset.record_count = dataset.records.count()
        dataset.label_count = dataset.records.exclude(label='').values('label').distinct().count()
        dataset.last_collected_at = timezone.now()
        dataset.save(update_fields=['record_count', 'label_count', 'last_collected_at'])
        job.status = Status.SUCCEEDED
        job.finished_at = timezone.now()
        if job.records_total is None:
            job.records_total = job.records_processed
        # Only if still running: a job marked stale meanwhile stays failed, and this rolls back.
        if not CollectionJob.objects.filter(pk=job.pk, status=Status.RUNNING).update(
                status=job.status, finished_at=job.finished_at, records_total=job.records_total):
            raise ProviderError(_('The job was stopped before it finished. Press Collect data to try again.'))
    logger.info('Collection job %s: %s records (%s new) in %s', job.pk, job.records_processed,
                job.records_new, job.dataset.slug)


def _store_batch(job, batch):
    unique = {record.source_id: record for record in batch if record.source_id and record.text}
    if not unique:
        return
    now = timezone.now()
    existing = set(DatasetRecord.objects.filter(dataset_id=job.dataset_id, source_id__in=unique)
                   .values_list('source_id', flat=True))
    rows = [DatasetRecord(dataset_id=job.dataset_id, source_id=record.source_id, text=record.text,
                          label=record.label, job=job, collected_at=now) for record in unique.values()]
    options = {'update_conflicts': True, 'update_fields': ['text', 'label', 'job', 'collected_at']}
    if connection.features.supports_update_conflicts_with_target:   # not MySQL
        options['unique_fields'] = ['dataset', 'source_id']
    DatasetRecord.objects.bulk_create(rows, **options)
    job.records_processed += len(unique)
    job.records_new += len(unique.keys() - existing)


def _save_progress(job, *fields):
    job.heartbeat_at = timezone.now()
    job.save(update_fields=[*fields, 'heartbeat_at'])


def _fail(job, message):
    job.status = Status.FAILED
    job.error = message[:1000]
    job.finished_at = timezone.now()
    job.save(update_fields=['status', 'error', 'finished_at'])


def expire_stale_jobs(dataset=None):
    """Mark jobs that stopped reporting progress (e.g. the server restarted mid-job) as failed."""
    now = timezone.now()
    jobs = CollectionJob.objects.all() if dataset is None else dataset.jobs.all()
    with translation.override('en'):
        message = _('The job stopped responding (the server may have restarted). Press Collect data to try again.')
    stale_running = jobs.filter(status=Status.RUNNING,
                                heartbeat_at__lt=now - timedelta(seconds=settings.ML_JOB_STALE_SECONDS))
    stale_queued = jobs.filter(status=Status.QUEUED,
                               created_at__lt=now - timedelta(seconds=settings.ML_JOB_QUEUE_TIMEOUT))
    expired = 0
    for stale in (stale_running, stale_queued):
        expired += stale.update(status=Status.FAILED, error=message, finished_at=now)
    if expired:
        logger.warning('Marked %s stale collection job(s) as failed', expired)
    return expired
