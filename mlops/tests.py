import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
from datetime import timedelta
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from assistant.models import KnowledgeRecord
from citycorp.settings import env_int
from grievances.models import Category, Complaint

from . import jobs, providers
from .ml import data, tensorflow_available, train
from .models import CollectionJob, Dataset, DatasetRecord, TrainingRun
from .providers import CollectedRecord, ProviderError

Status = CollectionJob.Status


def sign_in_admin(client):
    # force_login: the demo password 1234 is refused outside debug mode (accounts/backends.py).
    client.force_login(get_user_model().objects.get(username='admin'))


def make_complaint(category=None, title='Streetlight not working', description='Dark road at night.'):
    return Complaint.objects.create(citizen_name='Ravi K', citizen_contact='+919876500000', title=title,
                                    description=description, category=category)


class FakeProvider(providers.Provider):
    """Stands in for a real source in tests; its batches and errors are set per test."""
    slug = 'complaints'
    name = 'Fake'

    def __init__(self, batches=(), errors=(), total=None):
        self._batches = list(batches)
        self._errors = list(errors)
        self._total = total

    def count(self):
        return self._total

    def batches(self):
        if self._errors:
            raise self._errors.pop(0)
        yield from self._batches


def fake_response(body):
    response = mock.MagicMock()
    response.read.return_value = body if isinstance(body, bytes) else json.dumps(body).encode()
    response.__enter__.return_value = response
    return response


class ProviderTests(TestCase):
    def test_clean_text_hides_contact_details(self):
        text = providers.clean_text('Call  +91 98765 43210 or', 'mail ravi.k@example.com\nnow', None)
        self.assertEqual(text, 'Call [phone] or mail [email] now')
        self.assertEqual(len(providers.clean_text('x' * 5000)), providers.MAX_TEXT_LENGTH)

    def test_complaint_provider_reads_only_categorised_complaints(self):
        water = Category.objects.get(slug='water')
        labelled = make_complaint(water, title='No water', description='Tap dry, call 9876543210')
        make_complaint(None, title='Not sorted yet')
        provider = providers.get_provider('complaints')
        self.assertEqual(provider.count(), 1)
        batches = list(provider.batches())
        self.assertEqual(batches, [[CollectedRecord(labelled.reference, 'No water Tap dry, call [phone]', 'water')]])

    def test_complaint_provider_pages_through_batches(self):
        water = Category.objects.get(slug='water')
        for number in range(5):
            make_complaint(water, title=f'Leak {number}')
        with mock.patch.object(providers, 'BATCH_SIZE', 2):
            sizes = [len(batch) for batch in providers.get_provider('complaints').batches()]
        self.assertEqual(sizes, [2, 2, 1])

    def test_ingested_provider_uses_category_field(self):
        common = {'content': 'Ward 4 drain blocked', 'fingerprint': 'a'}
        first = KnowledgeRecord.objects.create(dataset='scraped', title='Drain', data={'category': 'drainage'}, **common)
        KnowledgeRecord.objects.create(dataset='scraped', title='Other', data=['not a dict'], content='x', fingerprint='b')
        provider = providers.get_provider('ingested')
        self.assertEqual(provider.count(), 2)
        records = [record for batch in provider.batches() for record in batch]
        self.assertEqual(records[0], CollectedRecord(f'scraped:{first.pk}', 'Drain Ward 4 drain blocked', 'drainage'))
        self.assertEqual(records[1].label, '')

    def test_unknown_provider(self):
        self.assertIsNone(providers.get_provider('nope'))
        self.assertEqual([provider.slug for provider in providers.providers()], ['complaints', 'ingested'])
        with self.assertRaises(NotImplementedError):
            providers.Provider().batches()
        self.assertIsNone(providers.Provider().count())


@override_settings(ML_HTTP_SOURCE_URL='https://data.example.org/records.json', ML_HTTP_SOURCE_TOKEN='secret-token',
                   ML_HTTP_SOURCE_NAME='City sensors')
class HttpProviderTests(TestCase):
    def provider(self):
        provider = providers.get_provider('external')
        self.assertEqual(provider.name, 'City sensors')
        return provider

    def collect(self):
        return [record for batch in self.provider().batches() for record in batch]

    @mock.patch('urllib.request.urlopen')
    def test_reads_records_and_sends_token(self, urlopen):
        urlopen.return_value = fake_response({'records': [
            {'id': 7, 'text': 'Pothole on 5th street', 'label': 'roads'},
            {'title': 'Only a title'},
            {'text': ''},
            'not an object',
        ]})
        self.assertEqual(self.collect(), [CollectedRecord('7', 'Pothole on 5th street', 'roads'),
                                          CollectedRecord('1', 'Only a title', '')])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-token')
        self.assertEqual(urlopen.call_args.kwargs['timeout'], providers.HTTP_TIMEOUT_SECONDS)

    @mock.patch('urllib.request.urlopen')
    def test_errors(self, urlopen):
        cases = [
            (urllib.error.HTTPError('u', 401, 'no', {}, None), 'HTTP 401', False),
            (urllib.error.HTTPError('u', 503, 'busy', {}, None), 'HTTP 503', True),
            (urllib.error.URLError('down'), 'could not be reached', True),
            (fake_response(b'not json'), 'valid JSON', False),
            (fake_response({'records': 'x'}), 'JSON list', False),
            (fake_response(b'[' + b'1,' * (3 * 1024 * 1024) + b'1]'), 'more than 5 MB', False),
            (fake_response([{'text': 'a'}] * (providers.HTTP_MAX_RECORDS + 1)), 'more than 5000', False),
        ]
        for outcome, message, temporary in cases:
            with self.subTest(message=message):
                urlopen.side_effect = outcome if isinstance(outcome, Exception) else None
                urlopen.return_value = None if isinstance(outcome, Exception) else outcome
                with self.assertRaises(ProviderError) as caught:
                    self.collect()
                self.assertIn(message, str(caught.exception))
                self.assertEqual(caught.exception.temporary, temporary)

    @override_settings(ML_HTTP_SOURCE_URL='file:///etc/passwd')
    def test_only_http_urls(self):
        with self.assertRaisesMessage(ProviderError, 'must start with https://'):
            self.collect()


@override_settings(ML_JOB_RUNNER='inline')
class JobTests(TestCase):
    def setUp(self):
        self.water = Category.objects.get(slug='water')
        self.roads = Category.objects.get(slug='roads')

    def test_inline_collection_stores_records(self):
        first = make_complaint(self.water, title='No water')
        make_complaint(self.roads, title='Pothole')
        job = jobs.start_collection('complaints')
        job.refresh_from_db()
        self.assertEqual((job.status, job.records_total, job.records_processed, job.records_new, job.attempts),
                         (Status.SUCCEEDED, 2, 2, 2, 1))
        self.assertEqual(job.progress, 100)
        dataset = Dataset.objects.get(slug='complaints')
        self.assertEqual((dataset.record_count, dataset.label_count), (2, 2))
        self.assertIsNotNone(dataset.last_collected_at)
        record = dataset.records.get(source_id=first.reference)
        self.assertEqual((record.text, record.label), ('No water Dark road at night.', 'water'))

    def test_running_again_is_idempotent_and_mirrors_the_source(self):
        keep = make_complaint(self.water, title='No water')
        gone = make_complaint(self.roads, title='Pothole')
        jobs.start_collection('complaints')
        gone.delete()
        keep.title = 'No water at all'
        keep.save()
        job = jobs.start_collection('complaints')
        job.refresh_from_db()
        self.assertEqual((job.records_processed, job.records_new), (1, 0))
        self.assertEqual(list(DatasetRecord.objects.values_list('text', flat=True)), ['No water at all Dark road at night.'])
        self.assertEqual(Dataset.objects.get().record_count, 1)

    def test_unknown_dataset(self):
        with self.assertRaises(jobs.UnknownDataset):
            jobs.start_collection('nope')

    @override_settings(ML_JOB_RUNNER='command')
    def test_only_one_active_job_per_dataset(self):
        queued = jobs.start_collection('complaints')
        self.assertEqual(queued.status, Status.QUEUED)
        with self.assertRaises(jobs.JobConflict) as caught:
            jobs.start_collection('complaints')
        self.assertEqual(caught.exception.job, queued)
        jobs.start_collection('ingested')   # other datasets are not blocked
        self.assertEqual(jobs.run_queued_jobs(), 2)
        self.assertFalse(CollectionJob.objects.filter(status__in=CollectionJob.ACTIVE).exists())

    @override_settings(ML_JOB_RUNNER='command')
    def test_a_claimed_job_is_not_run_twice(self):
        job = jobs.start_collection('complaints')
        self.assertIsNotNone(jobs.run_job(job.pk))
        self.assertIsNone(jobs.run_job(job.pk))

    @override_settings(ML_JOB_RUNNER='command', ML_JOB_STALE_SECONDS=60, ML_JOB_QUEUE_TIMEOUT=600)
    def test_stale_jobs_are_failed_and_unblock_the_dataset(self):
        running = jobs.start_collection('complaints')
        CollectionJob.objects.filter(pk=running.pk).update(
            status=Status.RUNNING, heartbeat_at=timezone.now() - timedelta(minutes=5))
        queued = jobs.start_collection('ingested')
        CollectionJob.objects.filter(pk=queued.pk).update(created_at=timezone.now() - timedelta(hours=1))
        fresh = jobs.start_collection('complaints')   # the stale ones no longer block
        jobs.start_collection('ingested')
        for job in (running, queued):
            job.refresh_from_db()
            self.assertEqual(job.status, Status.FAILED)
            self.assertIn('stopped responding', job.error)
        self.assertEqual(fresh.status, Status.QUEUED)

    @override_settings(ML_JOB_RUNNER='thread')
    def test_thread_runner_starts_after_commit(self):
        make_complaint(self.water)
        started = []

        class SyncThread:
            def __init__(self, target, args, name, daemon):
                started.append(name)
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with mock.patch.object(jobs.threading, 'Thread', SyncThread), \
                mock.patch.object(jobs, 'connections') as connections, \
                self.captureOnCommitCallbacks(execute=True):
            job = jobs.start_collection('complaints')
            self.assertEqual(started, [])          # nothing runs before the commit
        job.refresh_from_db()
        self.assertEqual(started, [f'collect-{job.pk}'])
        self.assertEqual(job.status, Status.SUCCEEDED)
        connections.close_all.assert_called_once()

    def run_with(self, provider):
        with mock.patch.object(jobs, 'get_provider', return_value=provider), mock.patch.object(jobs.time, 'sleep') as sleep:
            job = jobs.start_collection('complaints')
        job.refresh_from_db()
        return job, sleep

    @override_settings(ML_JOB_MAX_ATTEMPTS=3)
    def test_temporary_errors_are_retried(self):
        provider = FakeProvider(batches=[[CollectedRecord('1', 'text', 'a')]],
                                errors=[ProviderError('busy', temporary=True), OperationalError('locked')])
        job, sleep = self.run_with(provider)
        self.assertEqual((job.status, job.attempts, job.records_processed), (Status.SUCCEEDED, 3, 1))
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    @override_settings(ML_JOB_MAX_ATTEMPTS=2)
    def test_gives_up_after_the_last_attempt(self):
        job, _sleep = self.run_with(FakeProvider(errors=[OperationalError('x'), OperationalError('x')]))
        self.assertEqual((job.status, job.attempts), (Status.FAILED, 2))
        self.assertIn('database was not reachable', job.error)
        self.assertIsNotNone(job.finished_at)

    def test_permanent_errors_fail_at_once_with_their_message(self):
        job, sleep = self.run_with(FakeProvider(errors=[ProviderError('Wrong token.')]))
        self.assertEqual((job.status, job.error, job.attempts), (Status.FAILED, 'Wrong token.', 1))
        sleep.assert_not_called()

    def test_unexpected_errors_fail_with_a_generic_message(self):
        with self.assertLogs('mlops.jobs', 'ERROR'):
            job, _sleep = self.run_with(FakeProvider(errors=[ValueError('boom')]))
        self.assertEqual(job.status, Status.FAILED)
        self.assertIn('unexpected error (ValueError)', job.error)

    def test_source_that_cannot_count(self):
        batch = [CollectedRecord('1', 'a', 'x'), CollectedRecord('1', 'a', 'x'), CollectedRecord('2', '', 'x')]
        job, _sleep = self.run_with(FakeProvider(batches=[batch, []]))
        self.assertEqual((job.status, job.records_total, job.records_processed), (Status.SUCCEEDED, 1, 1))

    def test_progress(self):
        job = CollectionJob(status=Status.RUNNING, records_total=None)
        self.assertIsNone(job.progress)
        job.records_total, job.records_processed = 8, 2
        self.assertEqual(job.progress, 25)

    def test_provider_removed_while_queued(self):
        with override_settings(ML_JOB_RUNNER='command'):
            job = jobs.start_collection('complaints')
        with mock.patch.object(jobs, 'get_provider', return_value=None):
            jobs.run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, Status.FAILED)
        self.assertIn('no longer set up', job.error)

    def test_job_stopped_by_someone_else_stays_failed(self):
        def stop_midway():
            CollectionJob.objects.filter(status=Status.RUNNING).update(status=Status.FAILED)
            yield [CollectedRecord('1', 'a', 'x')]

        provider = FakeProvider()
        provider.batches = stop_midway
        job, _sleep = self.run_with(provider)
        self.assertEqual(job.status, Status.FAILED)
        self.assertIn('stopped before it finished', job.error)
        self.assertEqual(Dataset.objects.get().record_count, 0)

    def test_labels_and_strings(self):
        dataset = Dataset.objects.create(slug='x', name='X')
        job = CollectionJob.objects.create(dataset=dataset)
        record = DatasetRecord.objects.create(dataset=dataset, source_id='1', text='t')
        run = TrainingRun.objects.create(dataset=dataset)
        self.assertEqual((str(dataset), str(job), str(record), str(run)),
                         ('X', f'Job {job.pk} · x · queued', 'x:1', f'Training {run.pk} · x · running'))


class CommandTests(TestCase):
    @override_settings(ML_JOB_RUNNER='command')
    def test_run_collection_jobs(self):
        make_complaint(Category.objects.get(slug='water'))
        out = io.StringIO()
        call_command('run_collection_jobs', stdout=out)
        self.assertIn('Ran 0 queued', out.getvalue())
        call_command('run_collection_jobs', dataset='complaints', stdout=out)
        self.assertIn('succeeded: 1 records (1 new)', out.getvalue())

    @override_settings(ML_JOB_RUNNER='command')
    def test_run_collection_jobs_errors(self):
        with self.assertRaisesMessage(CommandError, 'no dataset called'):
            call_command('run_collection_jobs', dataset='nope')
        job = jobs.start_collection('complaints')
        with self.assertRaisesMessage(CommandError, f'already has job {job.pk}'):
            call_command('run_collection_jobs', dataset='complaints')
        jobs.run_job(job.pk)
        with mock.patch.object(jobs, 'get_provider', side_effect=[providers.get_provider('complaints'), None]):
            with self.assertRaisesMessage(CommandError, 'failed: This data source is no longer set up'):
                call_command('run_collection_jobs', dataset='complaints')

    def test_train_model_errors(self):
        with self.assertRaisesMessage(CommandError, 'has not been collected yet'):
            call_command('train_model', 'complaints')
        with self.assertRaisesMessage(CommandError, '--epochs must be 1 or more'):
            call_command('train_model', 'complaints', epochs=0)
        Dataset.objects.create(slug='complaints', name='Complaint texts')
        with self.assertRaisesMessage(CommandError, 'Only 0 labelled records'):
            call_command('train_model', 'complaints')
        self.assertEqual(TrainingRun.objects.get().status, TrainingRun.Status.FAILED)


@override_settings(ML_JOB_RUNNER='inline')
class ApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('mlops:api_collect')

    def post(self, body, raw=False):
        return self.client.post(self.url, body if raw else json.dumps(body), content_type='application/json')

    def test_needs_a_staff_login(self):
        self.assertEqual(self.post({'dataset': 'complaints'}).status_code, 401)
        get_user_model().objects.create_user('clerk', password='x-long-password')
        self.client.force_login(get_user_model().objects.get(username='clerk'))
        response = self.post({'dataset': 'complaints'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['login_url'], reverse('accounts:login'))
        self.assertEqual(self.client.get(reverse('mlops:api_job', args=[1])).status_code, 401)

    def test_validation(self):
        sign_in_admin(self.client)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        cases = [(b'{bad', True, 'could not be read'), ({}, False, 'Choose a dataset'),
                 ({'dataset': 5}, False, 'Choose a dataset'), (['x'], False, 'Choose a dataset'),
                 ({'dataset': ' nope '}, False, 'no dataset called “nope”')]
        for body, raw, message in cases:
            with self.subTest(body=body):
                response = self.post(body, raw)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.json()['error'])

    def test_csrf_is_required(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        sign_in_admin(client)
        response = client.post(self.url, '{"dataset": "complaints"}', content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_start_and_poll(self):
        sign_in_admin(self.client)
        make_complaint(Category.objects.get(slug='water'))
        response = self.post({'dataset': 'complaints'})
        self.assertEqual(response.status_code, 202)
        job = response.json()['job']
        self.assertEqual((job['status'], job['records_processed'], job['progress']), ('succeeded', 1, 100))
        self.assertEqual(job['message'], 'Collected 1 record (1 new).')
        self.assertIn('at least 20 labelled records', job['next_action'])
        self.assertTrue(job['last_collected'].startswith('Last collected '))
        polled = self.client.get(job['status_url']).json()['job']
        self.assertEqual(polled['id'], job['id'])
        self.assertEqual(self.client.get(reverse('mlops:api_job', args=[999])).status_code, 404)

    @override_settings(ML_JOB_RUNNER='command')
    def test_duplicate_request_gets_409_with_the_running_job(self):
        sign_in_admin(self.client)
        first = self.post({'dataset': 'complaints'}).json()['job']
        self.assertEqual((first['status'], first['message']), ('queued', 'Waiting to start…'))
        response = self.post({'dataset': 'complaints'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['job']['id'], first['id'])

    def test_rate_limit(self):
        sign_in_admin(self.client)
        with override_settings(ML_JOB_RUNNER='command'):
            for _attempt in range(10):
                self.post({'dataset': 'complaints'})
        self.assertEqual(self.post({'dataset': 'complaints'}).status_code, 429)

    def test_database_error(self):
        sign_in_admin(self.client)
        with mock.patch.object(jobs, 'start_collection', side_effect=DatabaseError), self.assertLogs('mlops.views', 'ERROR'):
            self.assertEqual(self.post({'dataset': 'complaints'}).status_code, 503)

    @override_settings(ML_JOB_RUNNER='command', ML_JOB_STALE_SECONDS=60)
    def test_status_messages(self):
        sign_in_admin(self.client)
        job = jobs.start_collection('complaints')
        url = reverse('mlops:api_job', args=[job.pk])
        CollectionJob.objects.filter(pk=job.pk).update(status=Status.RUNNING, heartbeat_at=timezone.now(),
                                                       records_total=4, records_processed=1)
        body = self.client.get(url).json()['job']
        self.assertEqual((body['message'], body['progress'], body['status_label']), ('Collecting… 1 of 4 records', 25, 'Collecting'))
        self.assertIn('carries on in the background', body['next_action'])
        CollectionJob.objects.filter(pk=job.pk).update(records_total=None)
        self.assertEqual(self.client.get(url).json()['job']['message'], 'Collecting… 1 records so far')
        CollectionJob.objects.filter(pk=job.pk).update(heartbeat_at=timezone.now() - timedelta(minutes=5))
        body = self.client.get(url).json()['job']
        self.assertEqual(body['status'], 'failed')
        self.assertIn('stopped responding', body['message'])
        self.assertIn('Try again', body['next_action'])
        CollectionJob.objects.filter(pk=job.pk).update(error='')
        self.assertEqual(self.client.get(url).json()['job']['message'], 'Collection failed.')

    @override_settings(ML_MIN_TRAIN_RECORDS=2)
    def test_next_action_suggests_training_when_ready(self):
        sign_in_admin(self.client)
        make_complaint(Category.objects.get(slug='water'))
        make_complaint(Category.objects.get(slug='roads'))
        job = self.post({'dataset': 'complaints'}).json()['job']
        self.assertEqual(job['next_action'], 'Next: train the model with “manage.py train_model complaints”.')

    def test_failed_job_messages_are_translated(self):
        sign_in_admin(self.client)
        with mock.patch.object(jobs, 'get_provider', return_value=FakeProvider(errors=[OperationalError()])), \
                override_settings(ML_JOB_MAX_ATTEMPTS=1), self.assertLogs('mlops.jobs', 'ERROR'):
            job = jobs.start_collection('complaints')
        self.client.cookies['django_language'] = 'hi'
        body = self.client.get(reverse('mlops:api_job', args=[job.pk])).json()['job']
        self.assertNotIn('database', body['message'])
        self.assertEqual(body['message'], body['error'])


class PageTests(TestCase):
    url = reverse('mlops:agent_work')

    def test_staff_only(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f'{reverse("accounts:login")}?next={self.url}', fetch_redirect_response=False)

    def test_menu_and_empty_cards(self):
        sign_in_admin(self.client)
        response = self.client.get(self.url)
        self.assertContains(response, '<h1 class="head__title">Agent Work</h1>', html=True)
        self.assertContains(response, 'aria-current="page"')
        self.assertEqual(response.context['menu_active'], 'agent_work')
        self.assertEqual([card['provider'].slug for card in response.context['cards']], ['complaints', 'ingested'])
        self.assertContains(response, 'data-collect data-state="idle"', count=2)
        self.assertContains(response, 'aria-describedby="ds-complaints-status"')
        self.assertContains(response, 'id="ds-complaints-status" role="status" aria-live="polite"')
        self.assertContains(response, 'Not collected yet', count=2)
        self.assertContains(response, '<span data-last-collected>Never collected</span>', count=2)
        self.assertContains(response, 'No model has been trained yet.')
        self.assertContains(response, 'js/agent-work.js')
        self.assertContains(response, f'data-collect-url="{reverse("mlops:api_collect")}"')

    def test_menu_link_on_other_pages(self):
        sign_in_admin(self.client)
        self.assertContains(self.client.get(reverse('grievances:home')), f'href="{self.url}"')

    @override_settings(ML_JOB_RUNNER='inline')
    def test_cards_after_collection_and_training(self):
        sign_in_admin(self.client)
        make_complaint(Category.objects.get(slug='water'))
        job = jobs.start_collection('complaints')
        dataset = Dataset.objects.get(slug='complaints')
        TrainingRun.objects.create(dataset=dataset, status=TrainingRun.Status.SUCCEEDED,
                                   metrics={'accuracy': 0.875, 'baseline_accuracy': 0.5}, record_count=40)
        TrainingRun.objects.create(dataset=dataset, status=TrainingRun.Status.FAILED, error='Too few')
        response = self.client.get(self.url)
        card = response.context['cards'][0]
        self.assertEqual(json.loads(card['job_json'])['id'], job.pk)
        self.assertContains(response, 'data-job="{&quot;id&quot;: ')
        self.assertContains(response, '<span class="badge badge--resolved">Collected</span>', html=True)
        self.assertContains(response, '87.5%')
        self.assertContains(response, '50.0%')
        self.assertContains(response, 'title="Too few"')
        self.assertEqual(response.context['totals'], {'datasets': 2, 'records': 1, 'running': 0, 'models': 1})

    @override_settings(ML_JOB_RUNNER='command')
    def test_running_and_failed_cards(self):
        sign_in_admin(self.client)
        running = jobs.start_collection('complaints')
        failed = jobs.start_collection('ingested')
        CollectionJob.objects.filter(pk=failed.pk).update(status=Status.FAILED, error='Wrong token.')
        response = self.client.get(self.url)
        self.assertContains(response, '<span class="badge badge--medium">Collecting</span>', html=True)
        self.assertContains(response, '<span class="badge badge--high">Last run failed</span>', html=True)
        self.assertContains(response, 'Wrong token.')
        self.assertEqual(response.context['totals']['running'], 1)
        self.assertEqual(response.context['cards'][0]['job'], running)

    def test_admin_cannot_add_jobs_or_training_runs(self):
        sign_in_admin(self.client)
        for model in ('collectionjob', 'trainingrun'):
            with self.subTest(model=model):
                self.assertEqual(self.client.get(f'/admin/mlops/{model}/add/').status_code, 403)
        self.assertEqual(self.client.get('/admin/mlops/dataset/').status_code, 200)

    def test_tensorflow_notice(self):
        sign_in_admin(self.client)
        with mock.patch('mlops.views.tensorflow_available', return_value=False):
            self.assertContains(self.client.get(self.url), 'TensorFlow is not installed on this server.')
        with mock.patch('mlops.views.tensorflow_available', return_value=True):
            self.assertContains(self.client.get(self.url), 'TensorFlow is installed on this server.')


@skipUnless(shutil.which('node'), 'Node.js is not installed')
class ButtonStateJsTests(TestCase):
    def test_button_states(self):
        script = Path(settings.BASE_DIR, 'mlops', 'tests_js', 'agent-work.test.js')
        result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True,
                                encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DataPrepTests(TestCase):
    def test_load_examples_skips_unlabelled(self):
        dataset = Dataset.objects.create(slug='complaints', name='C')
        DatasetRecord.objects.create(dataset=dataset, source_id='2', text='  Pothole  ON road', label='roads')
        DatasetRecord.objects.create(dataset=dataset, source_id='1', text='no label', label='')
        DatasetRecord.objects.create(dataset=dataset, source_id='3', text='   ', label='roads')
        self.assertEqual(data.load_examples(dataset), (['pothole on road'], ['roads']))

    def test_split_is_stratified_and_repeatable(self):
        labels = ['a'] * 10 + ['b'] * 5 + ['c']
        texts = [f't{i}' for i in range(len(labels))]
        train_texts, train_labels, val_texts, val_labels = data.split(texts, labels)
        self.assertEqual(sorted(val_labels), ['a', 'a', 'b'])
        self.assertIn('c', train_labels)
        self.assertEqual(len(train_texts) + len(val_texts), len(texts))
        self.assertEqual(data.split(texts, labels), (train_texts, train_labels, val_texts, val_labels))

    def test_check_trainable_and_baseline(self):
        with self.assertRaisesMessage(data.TrainingError, 'Only 1 labelled records'):
            data.check_trainable(['a'], 2)
        with self.assertRaisesMessage(data.TrainingError, 'same label'):
            data.check_trainable(['a', 'a'], 2)
        data.check_trainable(['a', 'b'], 2)
        self.assertEqual(data.majority_accuracy(['a', 'a', 'b'], ['a', 'b']), 0.5)
        self.assertEqual(data.majority_accuracy(['a'], []), 0.0)

    def test_tensorflow_missing(self):
        dataset = Dataset.objects.create(slug='complaints', name='C')
        for number, label in enumerate(['a', 'b'] * 2):
            DatasetRecord.objects.create(dataset=dataset, source_id=str(number), text=f'word {label}', label=label)
        with mock.patch.dict(sys.modules, {'tensorflow': None}), override_settings(ML_MIN_TRAIN_RECORDS=2):
            run = train.train('complaints')
            with self.assertRaisesMessage(data.TrainingError, 'TensorFlow is not installed'):
                train.predict(['x'])
        self.assertEqual(run.status, TrainingRun.Status.FAILED)
        self.assertIn('requirements-ml.txt', run.error)

    def test_unexpected_training_error_is_recorded(self):
        Dataset.objects.create(slug='complaints', name='C')
        with mock.patch.object(train.data, 'load_examples', side_effect=RuntimeError('disk full')), \
                self.assertLogs('mlops.ml.train', 'ERROR'):
            run = train.train('complaints')
        self.assertEqual(run.error, 'Unexpected error (RuntimeError): disk full')

    def test_no_model_yet(self):
        with tempfile.TemporaryDirectory() as folder, \
                self.assertRaisesMessage(data.TrainingError, 'No trained model for "complaints" yet'):
            train.latest_model_dir('complaints', folder)

    def test_env_int(self):
        with mock.patch.dict('os.environ', {'ML_X': ' 12 ', 'ML_BAD': 'ten'}):
            self.assertEqual(env_int('ML_X', 1), 12)
            self.assertEqual(env_int('ML_MISSING', 3), 3)
            with self.assertRaises(ImproperlyConfigured):
                env_int('ML_BAD', 1)


# Three clearly separated vocabularies, so even a few epochs learn them. Test fixtures only.
WORDS = {
    'water': ['tap', 'pipe', 'leak', 'supply', 'tank', 'drinking'],
    'roads': ['pothole', 'asphalt', 'crack', 'pavement', 'traffic', 'tar'],
    'garbage': ['waste', 'bin', 'litter', 'dump', 'smell', 'collection'],
}


@skipUnless(tensorflow_available(), 'TensorFlow is not installed (requirements-ml.txt)')
@override_settings(ML_MIN_TRAIN_RECORDS=20)
class TensorFlowTrainingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dataset = Dataset.objects.create(slug='complaints', name='Complaint texts')
        records = []
        for label, words in WORDS.items():
            for number in range(15):
                text = ' '.join(words[(number + offset) % len(words)] for offset in range(4))
                records.append(DatasetRecord(dataset=dataset, source_id=f'{label}-{number}', text=text, label=label))
        DatasetRecord.objects.bulk_create(records)

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def test_train_evaluate_save_and_predict(self):
        run = train.train('complaints', epochs=40, artifacts_dir=self.folder)
        self.assertEqual(run.status, TrainingRun.Status.SUCCEEDED, run.error)
        metrics = run.metrics
        self.assertEqual(metrics['labels'], ['garbage', 'roads', 'water'])
        self.assertEqual((metrics['training_records'], metrics['validation_records']), (36, 9))
        self.assertGreater(metrics['accuracy'], metrics['baseline_accuracy'])
        self.assertEqual(set(metrics['per_label']['water']), {'support', 'precision', 'recall'})
        folder = Path(run.artifact_path)
        for name in ('model.keras', 'labels.json', 'metrics.json'):
            self.assertTrue((folder / name).is_file(), name)
        self.assertEqual(train.latest_model_dir('complaints', self.folder), folder)
        self.assertTrue(run.tensorflow_version)
        predictions = train.predict(['Leak in the PIPE near the tank', 'Big pothole and crack'],
                                    artifacts_dir=self.folder)
        self.assertEqual([label for label, _confidence in predictions], ['water', 'roads'])
        self.assertTrue(all(0 < confidence <= 1 for _label, confidence in predictions))

    def test_train_model_command(self):
        out = io.StringIO()
        with override_settings(ML_ARTIFACTS_DIR=Path(self.folder)):
            call_command('train_model', 'complaints', epochs=3, stdout=out)
        self.assertIn('held-out records', out.getvalue())
        self.assertIn(self.folder, out.getvalue())
