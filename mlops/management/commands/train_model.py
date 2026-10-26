from django.core.management.base import BaseCommand, CommandError

from mlops.ml import train
from mlops.models import TrainingRun


class Command(BaseCommand):
    help = 'Train the baseline TensorFlow text classifier on a collected dataset (needs requirements-ml.txt).'

    def add_arguments(self, parser):
        parser.add_argument('dataset', nargs='?', default='complaints', help='Dataset slug (default: complaints).')
        parser.add_argument('--epochs', type=int, help='Training epochs (default: ML_TRAIN_EPOCHS).')

    def handle(self, *args, **options):
        if options['epochs'] is not None and options['epochs'] < 1:
            raise CommandError('--epochs must be 1 or more.')
        try:
            run = train.train(options['dataset'], epochs=options['epochs'])
        except train.TrainingError as exc:
            raise CommandError(str(exc)) from None
        if run.status != TrainingRun.Status.SUCCEEDED:
            raise CommandError(f'Training run {run.pk} failed: {run.error}')
        metrics = run.metrics
        self.stdout.write(self.style.SUCCESS(
            f'Training run {run.pk} finished: accuracy {metrics["accuracy"]:.1%} on {metrics["validation_records"]} '
            f'held-out records (always guessing the most common label: {metrics["baseline_accuracy"]:.1%}).'))
        self.stdout.write(f'Model and metrics saved in {run.artifact_path}')
