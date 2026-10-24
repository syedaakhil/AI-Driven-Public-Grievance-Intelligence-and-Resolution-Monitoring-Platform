"""
The daily complaints report, due at 9:00 AM India time (Asia/Kolkata), whatever the server's own
timezone is.

`manage.py send_daily_report` runs send_daily_report(). An outside scheduler starts that command
(Task Scheduler on Windows, a systemd timer or cron on Linux; see DEPLOY.md). Running it more than
once a day is safe: the first run records the day in ReportRun, and later runs see it and stop.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone, translation

from . import brevo
from .emails import render_table_email
from .models import EmailLog, ReportRun

logger = logging.getLogger(__name__)

INDIA = ZoneInfo('Asia/Kolkata')
SEND_AT = time(9, 0)
# A run still "sending" after this long crashed half-way; the next run may try again.
STALE_AFTER = timedelta(minutes=30)
MAX_ROWS = 500


def india_now(now=None):
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValueError('Pass a timezone-aware datetime.')
    return now.astimezone(INDIA)


def is_due(now=None):
    """True from 9:00 AM India time onwards (the rest of that day)."""
    return india_now(now).time() >= SEND_AT


def next_run(now=None):
    """The next 9:00 AM India time after `now`."""
    local = india_now(now)
    today_nine = datetime.combine(local.date(), SEND_AT, tzinfo=INDIA)
    return today_nine if local < today_nine else today_nine + timedelta(days=1)


def report_window(report_date):
    """The 24 hours the report covers: 9:00 AM the day before -> 9:00 AM on report_date (India time)."""
    end = datetime.combine(report_date, SEND_AT, tzinfo=INDIA)
    return end - timedelta(days=1), end


@dataclass
class Outcome:
    status: str          # "sent", "failed", "already_sent", "in_progress", "not_due", "dry_run"
    message: str
    run: ReportRun = None


def build_report(report_date):
    """(subject, html, text, complaint_count) for the day. Always in English."""
    with translation.override('en'):
        return _build_report(report_date)


def _build_report(report_date):
    from grievances.models import Complaint

    start, end = report_window(report_date)
    new = (Complaint.objects.filter(created_at__gte=start, created_at__lt=end)
           .select_related('category', 'ward').order_by('-created_at'))
    count = new.count()
    by_status = dict(Complaint.objects.values_list('status').annotate(total=Count('id')))
    labels = dict(Complaint.Status.choices)
    summary = [('New in the last 24 hours', count), ('All complaints', sum(by_status.values()))]
    summary += [(labels[value], by_status.get(value, 0)) for value in labels]

    rows = []
    for complaint in new[:MAX_ROWS]:
        area = ', '.join(part for part in (complaint.location, complaint.taluk) if part)
        rows.append([complaint.reference, complaint.title,
                     ' · '.join(part for part in (complaint.citizen_name, area) if part),
                     complaint.get_status_display(),
                     timezone.localtime(complaint.created_at).strftime('%d %b %Y, %I:%M %p')])
    window = f'{start:%d %b %Y, %I:%M %p} to {end:%d %b %Y, %I:%M %p} (IST)'
    intro = (f'{count} new complaint{"" if count == 1 else "s"} between {window}.' if count
             else f'No new complaints between {window}.')
    note = f'Showing the first {MAX_ROWS} of {count}.' if count > MAX_ROWS else ''
    subject = f'Daily complaints report — {report_date:%d %b %Y} ({count} new)'
    html, text = render_table_email(f'Daily complaints report — {report_date:%d %b %Y}', intro, rows,
                                    summary, note)
    return subject, html, text, count


def _claim(report_date, force):
    """Record today's run before sending. Returns (run, None) to go ahead, or (None, Outcome) to stop."""
    with transaction.atomic():
        try:
            run, created = ReportRun.objects.select_for_update().get_or_create(report_date=report_date)
        except IntegrityError:
            # Another process created today's row between our read and write.
            return None, Outcome('in_progress', 'Another run is already sending today\'s report.')
        if created:
            return run, None
        if run.status == ReportRun.Status.SENT and not force:
            return None, Outcome('already_sent', f'Today\'s report was already sent at '
                                                 f'{timezone.localtime(run.finished_at):%I:%M %p}.', run)
        if (run.status == ReportRun.Status.SENDING and not force
                and timezone.now() - run.started_at < STALE_AFTER):
            return None, Outcome('in_progress', 'Another run is already sending today\'s report.', run)
        run.status, run.error = ReportRun.Status.SENDING, ''
        run.started_at, run.finished_at = timezone.now(), None
        run.attempts += 1
        run.save()
        return run, None


def send_daily_report(now=None, force=False, dry_run=False):
    """
    Send today's report if it is due and not sent yet. force=True sends even before 9 AM and even
    if it was already sent (for testing). dry_run=True only builds it.
    """
    local = india_now(now)
    report_date = local.date()
    if dry_run:
        subject, _, text, count = build_report(report_date)
        return Outcome('dry_run', f'{subject}\n\n{text}')
    if not force and not is_due(now):
        return Outcome('not_due', f'Not 9:00 AM India time yet (it is {local:%I:%M %p}). '
                                  f'Next report: {next_run(now):%d %b %Y, %I:%M %p IST}.')

    run, stop = _claim(report_date, force)
    if stop:
        logger.info('Daily report %s skipped: %s', report_date, stop.message)
        return stop
    try:
        subject, html, text, count = build_report(report_date)
        run.complaint_count = count
        brevo.send_email(subject, html, text, EmailLog.Kind.DAILY_REPORT, record_count=count)
    except Exception as exc:  # noqa: BLE001  (any failure is recorded, then reported)
        run.status, run.error, run.finished_at = ReportRun.Status.FAILED, str(exc)[:2000], timezone.now()
        run.save()
        logger.error('Daily report %s failed: %s', report_date, exc)
        return Outcome('failed', f'The report was not sent: {exc}', run)
    run.status, run.finished_at = ReportRun.Status.SENT, timezone.now()
    run.save()
    logger.info('Daily report %s sent with %s complaints.', report_date, run.complaint_count)
    return Outcome('sent', f'Report sent to {brevo.ONLY_RECIPIENT} ({run.complaint_count} new complaints).', run)
