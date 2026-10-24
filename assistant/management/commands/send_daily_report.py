from django.core.management.base import BaseCommand, CommandError

from assistant.reports import send_daily_report


class Command(BaseCommand):
    help = ('Email the daily complaints report (due 9:00 AM Asia/Kolkata). Safe to run often: it sends '
            'at most once a day. Start it from Task Scheduler, a systemd timer or cron.')

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Send now, even before 9 AM or if today\'s report was already sent (for testing).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Only print the report; send nothing.')

    def handle(self, *args, force=False, dry_run=False, **options):
        outcome = send_daily_report(force=force, dry_run=dry_run)
        if outcome.status == 'failed':
            raise CommandError(outcome.message)
        style = self.style.SUCCESS if outcome.status in ('sent', 'dry_run') else self.style.WARNING
        self.stdout.write(style(f'[{outcome.status}] {outcome.message}'))
