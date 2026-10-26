import json
import logging

from django.conf import settings
from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import staff_required
from assistant.ratelimit import allow

from . import jobs
from .ml import tensorflow_available
from .models import CollectionJob, Dataset, TrainingRun
from .providers import providers

logger = logging.getLogger(__name__)

COLLECT_PER_MINUTE = 10


def _error(message, status, **extra):
    return JsonResponse({'error': message, **extra}, status=status)


def _staff_or_401(request):
    user = request.user
    if user.is_authenticated and user.is_active and user.is_staff:
        return None
    return _error(_('Please sign in as a corporation admin.'), 401, login_url=reverse('accounts:login'))


def job_message(job):
    if job.status == CollectionJob.Status.QUEUED:
        return _('Waiting to start…')
    if job.status == CollectionJob.Status.RUNNING:
        if job.records_total:
            return _('Collecting… %(done)s of %(total)s records') % {
                'done': job.records_processed, 'total': job.records_total}
        return _('Collecting… %(done)s records so far') % {'done': job.records_processed}
    if job.status == CollectionJob.Status.SUCCEEDED:
        return ngettext('Collected %(count)s record (%(new)s new).', 'Collected %(count)s records (%(new)s new).',
                        job.records_processed) % {'count': job.records_processed, 'new': job.records_new}
    # Errors are saved in English; translate the ones that have a Hindi version.
    return _(job.error) if job.error else _('Collection failed.')


def next_action(dataset, job):
    if job is None:
        return _('Press Collect data to copy the latest records into this dataset.')
    if job.is_active:
        return _('You can leave this page; collection carries on in the background.')
    if job.status == CollectionJob.Status.FAILED:
        return _('Fix the problem above if it names one, then press Try again.')
    labelled = dataset.records.exclude(label='').count()
    if labelled >= settings.ML_MIN_TRAIN_RECORDS and dataset.label_count >= 2:
        return _('Next: train the model with “manage.py train_model %(slug)s”.') % {'slug': dataset.slug}
    return _('Training needs at least %(count)s labelled records in 2 or more labels; '
             'collect again when more arrive.') % {'count': settings.ML_MIN_TRAIN_RECORDS}


def last_collected(dataset):
    if not dataset.last_collected_at:
        return _('Never collected')
    when = date_format(timezone.localtime(dataset.last_collected_at), 'd M Y, H:i')
    return _('Last collected %(when)s') % {'when': when}


def job_to_dict(job):
    dataset = job.dataset
    return {
        'id': job.pk,
        'dataset': dataset.slug,
        'status': job.status,
        'status_label': {
            CollectionJob.Status.QUEUED: _('Queued'),
            CollectionJob.Status.RUNNING: _('Collecting'),
            CollectionJob.Status.SUCCEEDED: _('Collected'),
            CollectionJob.Status.FAILED: _('Failed'),
        }[job.status],
        'is_active': job.is_active,
        'progress': job.progress,
        'records_total': job.records_total,
        'records_processed': job.records_processed,
        'records_new': job.records_new,
        'attempts': job.attempts,
        'error': _(job.error) if job.error else '',
        'message': job_message(job),
        'next_action': next_action(dataset, job),
        'dataset_record_count': dataset.record_count,
        'dataset_label_count': dataset.label_count,
        'last_collected': last_collected(dataset),
        'created_at': job.created_at.isoformat(),
        'started_at': job.started_at.isoformat() if job.started_at else None,
        'finished_at': job.finished_at.isoformat() if job.finished_at else None,
        'status_url': reverse('mlops:api_job', args=[job.pk]),
    }


@staff_required
def agent_work(request):
    jobs.expire_stale_jobs()
    saved = {dataset.slug: dataset for dataset in Dataset.objects.all()}
    cards = []
    for provider in providers():
        dataset = saved.get(provider.slug)
        job = dataset.jobs.select_related('dataset').first() if dataset else None
        cards.append({
            'provider': provider,
            'dataset': dataset,
            'job': job,
            'job_data': job_to_dict(job) if job else None,
            'job_json': json.dumps(job_to_dict(job)) if job else '',
            'next_action': next_action(dataset, job),
            'last_collected': last_collected(dataset) if dataset else _('Never collected'),
        })
    latest_runs = list(TrainingRun.objects.select_related('dataset')[:5])
    for run in latest_runs:
        run.accuracy_pct = _percent(run.metrics.get('accuracy'))
        run.baseline_pct = _percent(run.metrics.get('baseline_accuracy'))
    return render(request, 'mlops/agent_work.html', {
        'cards': cards,
        'totals': {
            'datasets': len(cards),
            'records': sum(card['dataset'].record_count for card in cards if card['dataset']),
            'running': sum(1 for card in cards if card['job'] and card['job'].is_active),
            'models': TrainingRun.objects.filter(status=TrainingRun.Status.SUCCEEDED).count(),
        },
        'training_runs': latest_runs,
        'tensorflow_ready': tensorflow_available(),
        'min_train_records': settings.ML_MIN_TRAIN_RECORDS,
        'artifacts_dir': settings.ML_ARTIFACTS_DIR,
    })


def _percent(value):
    return round(value * 100, 1) if isinstance(value, (int, float)) else None


@require_POST
def api_collect(request):
    """POST {"dataset": "<slug>"} → 202 {"job"}; 409 {"error", "job"} when one is already active."""
    denied = _staff_or_401(request)
    if denied:
        return denied
    if not allow(f'collect:{request.user.pk}', COLLECT_PER_MINUTE, 60):
        return _error(_('Too many requests. Please wait a minute and try again.'), 429)
    try:
        body = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return _error(_('The request could not be read.'), 400)
    slug = body.get('dataset') if isinstance(body, dict) else None
    if not isinstance(slug, str) or not slug.strip():
        return _error(_('Choose a dataset to collect.'), 400)
    try:
        job = jobs.start_collection(slug.strip(), request.user)
    except jobs.UnknownDataset:
        return _error(_('There is no dataset called “%(slug)s”.') % {'slug': slug.strip()[:50]}, 400)
    except jobs.JobConflict as conflict:
        return _error(_('This dataset is already being collected.'), 409, job=job_to_dict(conflict.job))
    except DatabaseError:
        logger.exception('Agent Work: database error starting %s', slug[:50])
        return _error(_('The database is not reachable right now. Please try again in a moment.'), 503)
    job.refresh_from_db()   # the inline runner may already have finished it
    return JsonResponse({'job': job_to_dict(job)}, status=202)


@require_GET
def api_job(request, pk):
    denied = _staff_or_401(request)
    if denied:
        return denied
    job = CollectionJob.objects.select_related('dataset').filter(pk=pk).first()
    if job is None:
        return _error(_('This job does not exist.'), 404)
    if job.is_active and jobs.expire_stale_jobs(job.dataset):
        job.refresh_from_db()
    return JsonResponse({'job': job_to_dict(job)})
