"""Turning one chat message into the assistant's answer (in the user's language; emails stay English)."""
import logging
import os

from django.utils import translation
from django.utils.translation import gettext as _, ngettext

from . import brevo
from .emails import render_table_email, result_rows
from .intents import detect_intent
from grievances.models import Complaint

from .models import EmailLog
from .search import EMAIL_ADDRESS_RE, Query
from .sources import search_everything

logger = logging.getLogger(__name__)

SESSION_KEY = 'assistant_last_query'
SHOWN_IN_CHAT = 10
MAX_EMAILED = 200


def help_text():
    return _('Hi! I can search complaints and email them to the corporation inbox.\n'
             '• Search by name, ID or words: “Lakshmi”, “CC-2026-00042”, “pothole Anna Nagar”\n'
             '• Filter: “open complaints”, “resolved today”, “latest complaints”\n'
             '• Email: “mail me this”, “email me the complaints of Ravi”\n'
             '• Files: “mail me CC-2026-00042” also attaches that complaint’s photo and documents')


def suggestions():
    return [_('Latest complaints'), _('Open complaints'), _('Mail me today\'s complaints')]


def _records(count):
    return ngettext('%(count)s record', '%(count)s records', count) % {'count': count}


def answer(request, message):
    intent = detect_intent(message)
    if intent.kind in ('empty', 'help'):
        return {'reply': help_text(), 'results': [], 'total': 0, 'suggestions': suggestions()}
    if intent.kind == 'email':
        return _email(request, intent.query)
    return _search(request, intent.query)


def _search(request, query):
    results, total = search_everything(query, limit=SHOWN_IN_CHAT)
    request.session[SESSION_KEY] = query.to_dict()
    described = query.describe()
    if not results:
        return {'reply': _('No records match %(search)s. Try fewer words or another spelling.') % {'search': described},
                'results': [], 'total': 0}
    if total > len(results):
        more = _('Showing the top %(shown)s. Say “mail me this” to get all %(total)s by email.') % {
            'shown': len(results), 'total': total}
    else:
        more = _('Say “mail me this” to get them by email.')
    found = _('Found %(records)s for %(search)s.') % {'records': _records(total), 'search': described}
    return {'reply': f'{found} {more}', 'results': [result.as_json() for result in results], 'total': total}


def _email(request, query):
    used_previous = False
    if query.is_empty():
        saved = request.session.get(SESSION_KEY)
        if saved:
            query, used_previous = Query.from_dict(saved), True
        else:
            query = Query(recent=True)   # "send the data to my email" with nothing searched yet
    described = query.describe()   # in the user's language, for the chat reply
    # The email itself is always in English, whatever language the site is shown in.
    with translation.override('en'):
        results, total = search_everything(query, limit=MAX_EMAILED)
        if results:
            heading = f'Complaints search: {query.describe()}'
            intro = (f'{total} record{"" if total == 1 else "s"} matched your search ({query.describe()})'
                     + (', your previous search in the chat.' if used_previous else '.'))
            note = f'Showing the first {len(results)} of {total}.' if total > len(results) else ''
            html, text = render_table_email(heading, intro, result_rows(results), [('Records found', total)], note)
            subject = f'City Corporation — {heading}'[:200]
    if not results:
        return {'reply': _('No records match %(search)s, so no email was sent.') % {'search': described},
                'results': [], 'total': 0, 'email': {'status': 'nothing'}}
    files, missing = complaint_files(query.reference) if query.reference else ([], [])
    try:
        brevo.send_email(subject, html, text, EmailLog.Kind.ON_DEMAND, record_count=len(results),
                         attachments=files)
    except brevo.EmailError as exc:
        return {'reply': _('❌ The email was not sent: %(reason)s') % {'reason': exc}, 'results': [], 'total': total,
                'email': {'status': 'failed'}}
    typed = {address.casefold() for address in EMAIL_ADDRESS_RE.findall(query.text)} - {brevo.ONLY_RECIPIENT}
    reply = _('✅ Sent %(records)s (%(search)s) to %(address)s.') % {
        'records': _records(len(results)), 'search': described, 'address': brevo.ONLY_RECIPIENT}
    if files:
        reply += ' ' + ngettext('📎 %(count)s file attached.', '📎 %(count)s files attached.', len(files)) % {
            'count': len(files)}
    if missing:
        reply += ' ' + _('These uploaded files are no longer stored, so they could not be attached: %(files)s.') % {
            'files': ', '.join(missing)}
    if typed:
        reply += ' ' + _('(Emails only go to %(address)s; other addresses are ignored.)') % {
            'address': brevo.ONLY_RECIPIENT}
    return {'reply': reply, 'results': [], 'total': total, 'email': {'status': 'sent', 'to': brevo.ONLY_RECIPIENT}}


def complaint_files(reference):
    """
    The uploaded photo and earlier complaint copy of one complaint, as ([(file name, bytes)], [missing
    labels]). A file can be missing when the server's disk doesn't keep uploads (such as Vercel).
    """
    complaint = Complaint.objects.filter(reference__iexact=reference).first()
    files, missing = [], []
    if complaint is None:
        return files, missing
    for label, slug, upload in ((_('photo'), 'photo', complaint.attachment),
                                (_('earlier complaint copy'), 'previous-copy', complaint.previous_copy)):
        if not upload:
            continue
        try:
            with upload.open('rb') as handle:
                data = handle.read()
        except OSError:
            logger.warning('File of complaint %s is missing from storage: %s', complaint.reference, slug)
            missing.append(label)
            continue
        files.append((f'{complaint.reference}-{slug}{os.path.splitext(upload.name)[1].lower()}', data))
    return files, missing
