"""Email bodies: an HTML table plus a plain-text version of the same content."""
from django.template.loader import render_to_string
from django.utils import timezone

COLUMNS = ['ID', 'Title', 'Details', 'Status', 'Filed']


def result_rows(results):
    return [[result.ref, result.title, result.details, result.status, result.date] for result in results]


def render_table_email(heading, intro, rows, summary=(), note=''):
    """
    rows: lists matching COLUMNS. summary: (label, value) pairs shown above the table.
    Returns (html, text). Values are escaped by the template.
    """
    context = {
        'heading': heading, 'intro': intro, 'columns': COLUMNS, 'rows': rows, 'summary': summary,
        'note': note, 'generated': timezone.localtime().strftime('%d %b %Y, %I:%M %p IST'),
    }
    return (render_to_string('assistant/email_table.html', context),
            render_to_string('assistant/email_table.txt', context).strip() + '\n')
