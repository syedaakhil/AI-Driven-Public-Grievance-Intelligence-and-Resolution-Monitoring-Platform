from django.core.management.base import BaseCommand, CommandError

from mlops import jobs
from mlops.models import CollectionJob


class Command(BaseCommand):
    help = ('Run queued "Collect data" jobs (for ML_JOB_RUNNER=command, from a timer), '
            'or start and run one with --dataset.')

    def add_arguments(self, parser):
        parser.add_argument('--dataset', help='Start a new collection for this dataset slug and run it now.')

    def handle(self, *args, **options):
        slug = options['dataset']
        if not slug:
            ran = jobs.run_queued_jobs()
            self.stdout.write(f'Ran {ran} queued collection job(s).')
            return
        try:
            job = jobs.start_collection(slug)
        except jobs.UnknownDataset:
            raise CommandError(f'There is no dataset called "{slug}".') from None
        except jobs.JobConflict as conflict:
            raise CommandError(f'Dataset "{slug}" already has job {conflict.job.pk} {conflict.job.status}.') from None
        jobs.run_job(job.pk)   # no-op if the configured runner already took it
        job.refresh_from_db()
        if job.status == CollectionJob.Status.FAILED:
            raise CommandError(f'Job {job.pk} failed: {job.error}')
        self.stdout.write(self.style.SUCCESS(
            f'Job {job.pk} {job.status}: {job.records_processed} records ({job.records_new} new) in "{slug}".'))
