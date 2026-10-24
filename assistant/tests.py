import io
import json
import urllib.error
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from grievances.models import Complaint

from . import brevo, reports
from .intents import detect_intent, is_email_request
from .models import EmailLog, KnowledgeRecord, ReportRun
from .search import parse_query
from .sources import search_everything

INDIA = ZoneInfo('Asia/Kolkata')
EMAIL_SETTINGS = dict(BREVO_API_KEY='test-brevo-key', SENDER_EMAIL='sender@example.com',
                      ADMIN_EMAIL='govermentowncoporation@gmail.com')
INGEST_KEY = 'test-ingest-key-0123456789-abcdefghijklmnop'


def make_complaint(**overrides):
    data = {'citizen_name': 'Lakshmi Narayanan', 'citizen_contact': '+919876543210',
            'title': 'Pothole near the bus stop', 'description': 'A large pothole on Gandhi Street near the bus stop.',
            'location': 'Gandhi Street', 'taluk': 'Egmore', 'filed_by_citizen': True}
    data.update(overrides)
    return Complaint.objects.create(**data)


class BrevoReply:
    """A fake urlopen() response from Brevo."""

    def __init__(self, body=b'{"messageId": "<abc@smtp-relay>"}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def sent_payload(urlopen):
    request = urlopen.call_args.args[0]
    return request, json.loads(request.data)


# ---------- Search ----------

class SearchTests(TestCase):
    def setUp(self):
        self.lakshmi = make_complaint()
        self.ravi = make_complaint(citizen_name='Ravi Kumar', title='Streetlight not working',
                                   description='The streetlight on Lake Road has been off for a week.',
                                   location='Lake Road', taluk='Velachery', status='resolved')
        self.other = make_complaint(citizen_name='Meena S', title='Garbage not collected',
                                    description='Garbage bins overflowing near the market.', location='Market Street')

    def find(self, message):
        results, total = search_everything(parse_query(message), limit=10)
        return [result.ref for result in results], total

    def test_name_any_case_and_partial(self):
        for message in ('Lakshmi', 'LAKSHMI', 'lakshmi narayanan', 'laksh', 'show complaints of lakshmi'):
            with self.subTest(message=message):
                self.assertEqual(self.find(message)[0], [self.lakshmi.reference])

    def test_spelling_differences(self):
        for message in ('Laksmi', 'Lakshmy', 'Ravvi Kumaar', 'streetlite'):
            with self.subTest(message=message):
                refs, _ = self.find(message)
                self.assertTrue(refs, message)
        self.assertEqual(self.find('Laksmi')[0][0], self.lakshmi.reference)
        self.assertEqual(self.find('Ravvi Kumaar')[0][0], self.ravi.reference)

    def test_complaint_id_and_description_words(self):
        self.assertEqual(self.find(self.ravi.reference.lower())[0], [self.ravi.reference])
        self.assertEqual(self.find('garbage market')[0], [self.other.reference])
        self.assertEqual(self.find('pothole')[0], [self.lakshmi.reference])

    def test_multiple_matches_and_none(self):
        refs, total = self.find('street')     # Gandhi Street, Lake Road streetlight, Market Street
        self.assertEqual(total, 3)
        self.assertEqual(self.find('xylophone')[1], 0)

    def test_status_and_recent_filters(self):
        self.assertEqual(self.find('resolved complaints')[0], [self.ravi.reference])
        self.assertEqual(set(self.find('open complaints')[0]), {self.lakshmi.reference, self.other.reference})
        self.assertEqual(len(self.find('latest complaints')[0]), 3)

    def test_unrelated_short_words_do_not_fuzzy_match(self):
        self.assertEqual(self.find('Rani')[1], 0)   # "Ravi" is close, but too different

    def test_ingested_records_are_searched_too(self):
        KnowledgeRecord.objects.create(dataset='ward-news', title='Water supply cut in Ward 4',
                                       content='title: Water supply cut in Ward 4', fingerprint='x',
                                       search_text='water supply cut in ward 4')
        results, _ = search_everything(parse_query('water supply'), limit=10)
        self.assertEqual([(r.source, r.title) for r in results], [('ward-news', 'Water supply cut in Ward 4')])


# ---------- Intent ----------

class IntentTests(TestCase):
    def test_email_requests_are_understood(self):
        for message in ('send the data to my email', 'mail me this', 'email me the complaints of Ravi',
                        'Email this list', 'can you mail it to me', 'pls send to my gmail',
                        'emial me the results', 'forward these to my inbox', 'e-mail me open complaints',
                        'Send me an email with Lakshmi complaints', 'MAIL ME TODAY\'S COMPLAINTS'):
            with self.subTest(message=message):
                self.assertEqual(detect_intent(message).kind, 'email')

    def test_searches_are_not_mistaken_for_email(self):
        for message in ('main road pothole', 'complaints of Ravi', 'send', 'mali street', 'Lakshmi',
                        'show the email column'):
            with self.subTest(message=message):
                self.assertFalse(is_email_request(message) and message != 'show the email column', message)
        self.assertEqual(detect_intent('main road pothole').kind, 'search')

    def test_email_target_is_extracted(self):
        self.assertEqual(detect_intent('email me the complaints of Ravi').query.words, ['ravi'])
        self.assertEqual(detect_intent('mail me open complaints').query.statuses,
                         ['submitted', 'assigned', 'in_progress'])
        self.assertTrue(detect_intent('mail me this').query.is_empty())
        self.assertEqual(detect_intent('email CC-2026-00042 to me').query.reference, 'CC-2026-00042')

    def test_greetings_and_empty(self):
        self.assertEqual(detect_intent('hi').kind, 'help')
        self.assertEqual(detect_intent('what can you do').kind, 'help')
        self.assertEqual(detect_intent('   ').kind, 'empty')


# ---------- Brevo ----------

@override_settings(**EMAIL_SETTINGS)
class BrevoTests(TestCase):
    def test_sends_only_to_the_locked_address(self):
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            log = brevo.send_email('Subject', '<p>Hi</p>', 'Hi', EmailLog.Kind.ON_DEMAND, record_count=3)
        request, payload = sent_payload(urlopen)
        self.assertEqual(request.full_url, 'https://api.brevo.com/v3/smtp/email')
        self.assertEqual(request.get_header('Api-key'), 'test-brevo-key')
        self.assertEqual(payload['to'], [{'email': 'govermentowncoporation@gmail.com'}])
        self.assertEqual(payload['sender'], {'name': 'City Corporation', 'email': 'sender@example.com'})
        self.assertEqual((payload['htmlContent'], payload['textContent']), ('<p>Hi</p>', 'Hi'))
        self.assertNotIn('cc', payload)
        self.assertNotIn('bcc', payload)
        self.assertEqual((log.status, log.recipient, log.provider_message_id, log.record_count),
                         ('sent', 'govermentowncoporation@gmail.com', '<abc@smtp-relay>', 3))

    def test_send_email_has_no_recipient_parameter(self):
        with self.assertRaises(TypeError):
            brevo.send_email('S', 'h', 't', EmailLog.Kind.ON_DEMAND, to='someone@else.com')

    @override_settings(ADMIN_EMAIL='attacker@example.com')
    def test_other_admin_email_is_refused_without_any_request(self):
        with mock.patch('urllib.request.urlopen') as urlopen, self.assertLogs('assistant.brevo', 'ERROR'):
            with self.assertRaisesMessage(brevo.EmailError, 'govermentowncoporation@gmail.com'):
                brevo.send_email('S', 'h', 't', EmailLog.Kind.ON_DEMAND)
        urlopen.assert_not_called()
        self.assertEqual(EmailLog.objects.get().status, 'failed')

    def test_guard_rejects_tampered_payloads(self):
        good = brevo.build_payload('S', 'h', 't')
        for tampered in ({**good, 'to': [{'email': 'x@example.com'}]},
                         {**good, 'to': good['to'] + [{'email': 'x@example.com'}]},
                         {**good, 'bcc': [{'email': 'x@example.com'}]}, {**good, 'cc': []}):
            with self.subTest(payload=tampered), self.assertRaises(brevo.EmailError):
                brevo.check_only_recipient(tampered)
        brevo.check_only_recipient(good)

    @override_settings(BREVO_API_KEY='')
    def test_missing_key_is_reported(self):
        with self.assertLogs('assistant.brevo', 'ERROR'), self.assertRaisesMessage(brevo.EmailError, 'BREVO_API_KEY'):
            brevo.send_email('S', 'h', 't', EmailLog.Kind.ON_DEMAND)

    def test_brevo_errors_become_email_errors(self):
        cases = [(urllib.error.HTTPError(brevo.BREVO_URL, 401, 'Unauthorized', {}, io.BytesIO(b'bad key')), 'API key'),
                 (urllib.error.HTTPError(brevo.BREVO_URL, 400, 'Bad', {}, io.BytesIO(b'sender')), 'SENDER_EMAIL'),
                 (urllib.error.HTTPError(brevo.BREVO_URL, 500, 'Oops', {}, io.BytesIO(b'')), 'HTTP 500'),
                 (urllib.error.URLError('no network'), 'Could not reach Brevo')]
        for problem, message in cases:
            with self.subTest(message=message), mock.patch('urllib.request.urlopen', side_effect=problem), \
                    self.assertLogs('assistant.brevo', 'ERROR'), self.assertRaisesMessage(brevo.EmailError, message):
                brevo.send_email('S', 'h', 't', EmailLog.Kind.ON_DEMAND)
        self.assertEqual(EmailLog.objects.filter(status='failed').count(), 4)


# ---------- Daily 9 AM report ----------

def ist(*args):
    return datetime(*args, tzinfo=INDIA)


@override_settings(**EMAIL_SETTINGS)
class DailyReportTests(TestCase):
    def test_due_from_9_am_india_time(self):
        self.assertFalse(reports.is_due(ist(2026, 10, 24, 8, 59, 59)))
        self.assertTrue(reports.is_due(ist(2026, 10, 24, 9, 0)))
        self.assertTrue(reports.is_due(ist(2026, 10, 24, 23, 59)))
        # The server clock's timezone doesn't matter: 03:30 UTC is 09:00 in India.
        self.assertTrue(reports.is_due(datetime(2026, 10, 24, 3, 30, tzinfo=dt_timezone.utc)))
        self.assertFalse(reports.is_due(datetime(2026, 10, 24, 3, 29, tzinfo=dt_timezone.utc)))
        with self.assertRaises(ValueError):
            reports.is_due(datetime(2026, 10, 24, 9, 0))   # naive times are refused

    def test_next_run_and_window(self):
        self.assertEqual(reports.next_run(ist(2026, 10, 24, 8, 0)), ist(2026, 10, 24, 9, 0))
        self.assertEqual(reports.next_run(ist(2026, 10, 24, 9, 0, 1)), ist(2026, 10, 25, 9, 0))
        self.assertEqual(reports.next_run(ist(2026, 10, 24, 9, 0)).utcoffset(), timedelta(hours=5, minutes=30))
        start, end = reports.report_window(date(2026, 10, 24))
        self.assertEqual((start, end), (ist(2026, 10, 23, 9, 0), ist(2026, 10, 24, 9, 0)))

    def test_not_sent_before_9(self):
        with mock.patch('urllib.request.urlopen') as urlopen:
            outcome = reports.send_daily_report(now=ist(2026, 10, 24, 8, 30))
        self.assertEqual(outcome.status, 'not_due')
        urlopen.assert_not_called()
        self.assertFalse(ReportRun.objects.exists())

    def test_sends_once_per_day_even_if_run_again(self):
        inside = make_complaint(created_at=ist(2026, 10, 24, 7, 0))
        make_complaint(title='Old complaint from last week', created_at=ist(2026, 10, 17, 10, 0))
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            first = reports.send_daily_report(now=ist(2026, 10, 24, 9, 0))
            again = reports.send_daily_report(now=ist(2026, 10, 24, 9, 5))     # e.g. a server restart
            later = reports.send_daily_report(now=ist(2026, 10, 24, 18, 0))
        self.assertEqual([first.status, again.status, later.status], ['sent', 'already_sent', 'already_sent'])
        self.assertEqual(urlopen.call_count, 1)
        _, payload = sent_payload(urlopen)
        self.assertEqual(payload['to'], [{'email': 'govermentowncoporation@gmail.com'}])
        self.assertIn(inside.reference, payload['htmlContent'])
        self.assertIn(inside.reference, payload['textContent'])
        self.assertNotIn('Old complaint from last week', payload['htmlContent'])
        self.assertIn('(1 new)', payload['subject'])
        run = ReportRun.objects.get()
        self.assertEqual((run.report_date, run.status, run.complaint_count), (date(2026, 10, 24), 'sent', 1))
        self.assertEqual(EmailLog.objects.get().kind, 'daily_report')

    def test_next_day_sends_again(self):
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            reports.send_daily_report(now=ist(2026, 10, 24, 9, 0))
            reports.send_daily_report(now=ist(2026, 10, 25, 9, 0))
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(ReportRun.objects.count(), 2)

    def test_failure_is_logged_and_retried_by_the_next_run(self):
        failure = urllib.error.URLError('down')
        with mock.patch('urllib.request.urlopen', side_effect=failure), self.assertLogs('assistant', 'ERROR'):
            failed = reports.send_daily_report(now=ist(2026, 10, 24, 9, 0))
        self.assertEqual(failed.status, 'failed')
        self.assertEqual(ReportRun.objects.get().status, 'failed')
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()):
            retried = reports.send_daily_report(now=ist(2026, 10, 24, 10, 0))
        run = ReportRun.objects.get()
        self.assertEqual((retried.status, run.status, run.attempts), ('sent', 'sent', 2))

    def test_a_run_in_progress_blocks_a_second_one(self):
        ReportRun.objects.create(report_date=date(2026, 10, 24), status='sending', started_at=timezone.now())
        with mock.patch('urllib.request.urlopen') as urlopen:
            outcome = reports.send_daily_report(now=ist(2026, 10, 24, 9, 1))
        self.assertEqual(outcome.status, 'in_progress')
        urlopen.assert_not_called()

    def test_empty_day_still_sends_a_short_report(self):
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            reports.send_daily_report(now=ist(2026, 10, 24, 9, 0))
        _, payload = sent_payload(urlopen)
        self.assertIn('No new complaints', payload['textContent'])

    def test_command_force_and_dry_run(self):
        out = io.StringIO()
        with mock.patch('urllib.request.urlopen') as urlopen:
            call_command('send_daily_report', '--dry-run', stdout=out)
        urlopen.assert_not_called()
        self.assertIn('[dry_run] Daily complaints report', out.getvalue())
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            call_command('send_daily_report', '--force', stdout=out)
            call_command('send_daily_report', '--force', stdout=out)   # --force sends even if already sent
        self.assertEqual(urlopen.call_count, 2)

    @override_settings(BREVO_API_KEY='')
    def test_command_fails_loudly_when_not_sent(self):
        with self.assertLogs('assistant', 'ERROR'), self.assertRaisesMessage(CommandError, 'BREVO_API_KEY'):
            call_command('send_daily_report', '--force', stdout=io.StringIO())


# ---------- Chat API ----------

@override_settings(**EMAIL_SETTINGS)
class ChatApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.get(username='admin')
        self.client.force_login(self.admin)
        self.url = reverse('assistant:chat')
        self.complaint = make_complaint()

    def say(self, message, client=None):
        return (client or self.client).post(self.url, json.dumps({'message': message}), content_type='application/json')

    def test_search_returns_readable_results(self):
        data = self.say('laksmi').json()
        self.assertIn('Found 1 record', data['reply'])
        result = data['results'][0]
        self.assertEqual(result['ref'], self.complaint.reference)
        self.assertEqual(result['title'], 'Pothole near the bus stop')
        self.assertIn('Lakshmi Narayanan', result['details'])
        self.assertEqual(result['status'], 'Submitted')
        self.assertEqual(result['url'], self.complaint.get_absolute_url())

    def test_no_match_says_so(self):
        data = self.say('xylophone').json()
        self.assertEqual(data['results'], [])
        self.assertIn('No records match', data['reply'])

    def test_mail_me_this_emails_the_previous_search(self):
        self.say('lakshmi')
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            data = self.say('mail me this').json()
        self.assertEqual(data['email'], {'status': 'sent', 'to': 'govermentowncoporation@gmail.com'})
        self.assertIn('✅ Sent 1 record', data['reply'])
        _, payload = sent_payload(urlopen)
        self.assertIn(self.complaint.reference, payload['htmlContent'])
        self.assertIn('previous search', payload['textContent'])

    def test_email_me_the_complaints_of_a_name(self):
        make_complaint(citizen_name='Ravi Kumar', title='Streetlight not working')
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            data = self.say('email me the complaints of ravi').json()
        self.assertEqual(data['email']['status'], 'sent')
        _, payload = sent_payload(urlopen)
        self.assertIn('Streetlight not working', payload['htmlContent'])
        self.assertNotIn('Pothole near the bus stop', payload['htmlContent'])

    def test_typed_address_cannot_change_the_recipient(self):
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            data = self.say('email lakshmi complaints to attacker@example.com').json()
        _, payload = sent_payload(urlopen)
        self.assertEqual(payload['to'], [{'email': 'govermentowncoporation@gmail.com'}])
        self.assertNotIn('attacker@example.com', json.dumps(payload))
        self.assertIn(self.complaint.reference, payload['htmlContent'])   # the search still worked
        self.assertIn('other addresses are ignored', data['reply'])

    def test_email_failure_is_reported_in_chat(self):
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('down')), \
                self.assertLogs('assistant.brevo', 'ERROR'):
            data = self.say('mail me lakshmi complaints').json()
        self.assertEqual(data['email']['status'], 'failed')
        self.assertIn('❌ The email was not sent', data['reply'])

    def test_nothing_to_email(self):
        with mock.patch('urllib.request.urlopen') as urlopen:
            data = self.say('email me the complaints of xylophone').json()
        urlopen.assert_not_called()
        self.assertEqual(data['email']['status'], 'nothing')

    def test_email_requests_are_limited_per_hour(self):
        with mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            for _ in range(10):
                self.say('mail me lakshmi complaints')
            data = self.say('mail me lakshmi complaints').json()
        self.assertEqual(urlopen.call_count, 10)
        self.assertEqual(data['email']['status'], 'limited')

    def test_help(self):
        self.assertIn('I can search complaints', self.say('hi').json()['reply'])

    def test_input_validation(self):
        for body, error in ((json.dumps({'message': ''}), 'Type a message'), (json.dumps({}), 'Type a message'),
                            (json.dumps({'message': 5}), 'Type a message'), ('not json', 'could not be read'),
                            (json.dumps({'message': 'x' * 501}), 'under 500')):
            with self.subTest(body=body[:20]):
                response = self.client.post(self.url, body, content_type='application/json')
                self.assertEqual(response.status_code, 400)
                self.assertIn(error, response.json()['error'])
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_only_staff_can_chat(self):
        visitor = Client()
        response = self.say('lakshmi', client=visitor)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['login_url'], reverse('accounts:login'))
        session = visitor.session
        session['citizen_phone'] = '+919876543210'   # a signed-in citizen is not staff either
        session.save()
        self.assertEqual(self.say('lakshmi', client=visitor).status_code, 401)

    def test_csrf_is_required(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        with self.assertLogs('django.security.csrf', 'WARNING'):
            self.assertEqual(self.say('lakshmi', client=strict).status_code, 403)

    def test_rate_limit(self):
        for _ in range(30):
            self.say('lakshmi')
        response = self.say('lakshmi')
        self.assertEqual(response.status_code, 429)
        self.assertIn('Too many messages', response.json()['error'])

    def test_database_down_gives_a_friendly_error(self):
        with mock.patch('assistant.chat.search_everything', side_effect=DatabaseError('gone')), \
                self.assertLogs('assistant.views', 'ERROR'), self.assertLogs('django.request', 'ERROR'):
            response = self.say('lakshmi')
        self.assertEqual(response.status_code, 503)
        self.assertIn('database is not reachable', response.json()['error'])

    def test_unexpected_error_gives_a_friendly_error(self):
        with mock.patch('assistant.chat.search_everything', side_effect=RuntimeError('bug')), \
                self.assertLogs('assistant.views', 'ERROR'), self.assertLogs('django.request', 'ERROR'):
            response = self.say('lakshmi')
        self.assertEqual(response.status_code, 500)
        self.assertIn('Something went wrong', response.json()['error'])


# ---------- Ingestion API ----------

@override_settings(INGEST_API_KEY=INGEST_KEY)
class IngestApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('assistant:ingest')

    def post_json(self, body, key=INGEST_KEY):
        return self.client.post(self.url, json.dumps(body) if not isinstance(body, str) else body,
                                content_type='application/json', HTTP_X_API_KEY=key)

    def test_json_records_are_stored_and_searchable_at_once(self):
        response = self.post_json({'dataset': 'ward-news', 'records': [
            {'id': 'n1', 'title': 'Water supply cut in Ward 4', 'body': 'No water on Monday 9-5.', 'ward': 4},
            {'id': 'n2', 'title': 'Road relaying on Lake Road', 'body': 'Expect delays.'},
        ]})
        self.assertEqual(response.status_code, 201)
        self.assertEqual({k: response.json()[k] for k in ('received', 'created', 'duplicates', 'rejected')},
                         {'received': 2, 'created': 2, 'duplicates': 0, 'rejected': 0})
        record = KnowledgeRecord.objects.get(external_id='n1')
        self.assertEqual((record.dataset, record.title, record.data['ward']), ('ward-news', 'Water supply cut in Ward 4', 4))
        results, _ = search_everything(parse_query('water supply ward'), limit=5)
        self.assertEqual(results[0].title, 'Water supply cut in Ward 4')

    def test_duplicates_are_cleaned(self):
        records = [{'id': 'n1', 'title': 'A'}, {'id': 'n1', 'title': 'A'}, {'title': 'Same text'}, {'title': '  same   text '}]
        first = self.post_json({'dataset': 'news', 'records': records}).json()
        self.assertEqual((first['created'], first['duplicates']), (2, 2))
        again = self.post_json({'dataset': 'news', 'records': records}).json()
        self.assertEqual((again['created'], again['updated'], again['duplicates']), (0, 0, 4))
        changed = self.post_json({'dataset': 'news', 'records': [{'id': 'n1', 'title': 'A (corrected)'}]})
        self.assertEqual(changed.json()['updated'], 1)
        self.assertEqual(KnowledgeRecord.objects.get(external_id='n1').title, 'A (corrected)')
        self.assertEqual(KnowledgeRecord.objects.count(), 2)

    def test_csv_body(self):
        csv_text = 'id,title,area\nr1,Drain blocked,Egmore\nr2,Tree fallen,Adyar\n'
        response = self.client.post(self.url + '?dataset=field-survey', csv_text, content_type='text/csv',
                                    HTTP_X_API_KEY=INGEST_KEY)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['created'], 2)
        self.assertEqual(KnowledgeRecord.objects.get(external_id='r2').data, {'id': 'r2', 'title': 'Tree fallen', 'area': 'Adyar'})

    def test_csv_and_json_file_upload(self):
        csv_file = SimpleUploadedFile('survey.csv', b'title,area\nDrain blocked,Egmore\n', content_type='text/csv')
        response = self.client.post(self.url, {'dataset': 'survey', 'file': csv_file}, HTTP_X_API_KEY=INGEST_KEY)
        self.assertEqual(response.status_code, 201)
        json_file = SimpleUploadedFile('news.json', json.dumps({'dataset': 'news', 'records': [{'title': 'Hi'}]}).encode())
        response = self.client.post(self.url, {'file': json_file}, HTTP_X_API_KEY=INGEST_KEY)
        self.assertEqual(response.status_code, 201)
        bad_file = SimpleUploadedFile('data.xlsx', b'binary')
        response = self.client.post(self.url, {'dataset': 'x1', 'file': bad_file}, HTTP_X_API_KEY=INGEST_KEY)
        self.assertEqual(response.status_code, 400)
        self.assertIn('.json or .csv', response.json()['error'])

    def test_api_key_is_required(self):
        for key in ('', 'wrong-key'):
            with self.subTest(key=key):
                response = self.post_json({'dataset': 'news', 'records': [{'title': 'x'}]}, key=key)
                self.assertEqual(response.status_code, 401)
                self.assertIn('X-API-Key', response.json()['error'])
        self.assertFalse(KnowledgeRecord.objects.exists())

    def test_too_many_wrong_keys_are_blocked(self):
        for _ in range(20):
            self.post_json({'dataset': 'news', 'records': []}, key='guess')
        self.assertEqual(self.post_json({'dataset': 'news', 'records': []}, key='guess').status_code, 429)

    @override_settings(INGEST_API_KEY='')
    def test_disabled_without_a_key(self):
        with self.assertLogs('django.request', 'ERROR'):
            response = self.post_json({'dataset': 'news', 'records': [{'title': 'x'}]})
        self.assertEqual(response.status_code, 503)

    def test_malformed_requests_get_clear_errors(self):
        cases = [
            ('{not json', 'not valid JSON'),
            ([{'title': 'x'}], 'Send a JSON object'),
            ({'dataset': 'news', 'records': 'x'}, 'must be a list'),
            ({'records': [{'title': 'x'}]}, '"dataset" is required'),
            ({'dataset': 'Bad Name!', 'records': [{'title': 'x'}]}, '"dataset" is required'),
            ({'dataset': 'news', 'records': []}, 'at least one record'),
            ({'dataset': 'news', 'records': [{'n': 1}, 'text', {'a': {'b': 1}}]}, 'No valid records'),
        ]
        for body, error in cases:
            with self.subTest(body=str(body)[:30]):
                response = self.post_json(body)
                self.assertEqual(response.status_code, 400)
                self.assertIn(error, response.json()['error'])
        self.assertFalse(KnowledgeRecord.objects.exists())

    def test_bad_records_are_skipped_with_reasons(self):
        response = self.post_json({'dataset': 'news', 'records': [
            {'title': 'Good one'}, {'title': {'nested': True}}, {'count': 3}, 'not an object', {'x' * 70: 'y'},
            {'title': 'y' * 10001},
        ]})
        data = response.json()
        self.assertEqual((response.status_code, data['created'], data['rejected']), (201, 1, 5))
        reasons = {item['record']: item['error'] for item in data['errors']}
        self.assertIn('nested', reasons[1])
        self.assertIn('no text', reasons[2])
        self.assertIn('must be an object', reasons[3])
        self.assertIn('field name', reasons[4])
        self.assertIn('longer than', reasons[5])

    def test_bad_csv(self):
        for body, error in (('\n', 'header row'), ('a,b\n1,2,3\n', 'more values'), (b'\xff\xfe\x00bad', 'UTF-8')):
            with self.subTest(body=body):
                response = self.client.post(self.url + '?dataset=survey', body, content_type='text/csv',
                                            HTTP_X_API_KEY=INGEST_KEY)
                self.assertEqual(response.status_code, 400)
                self.assertIn(error, response.json()['error'])

    def test_size_and_count_limits(self):
        response = self.client.post(self.url, 'x', content_type='application/json', HTTP_X_API_KEY=INGEST_KEY,
                                    CONTENT_LENGTH=str(6 * 1024 * 1024))
        self.assertEqual(response.status_code, 413)
        response = self.post_json({'dataset': 'news', 'records': [{'title': f't{i}'} for i in range(5001)]})
        self.assertEqual(response.status_code, 400)
        self.assertIn('at most 5000', response.json()['error'])

    def test_unsupported_type_and_method(self):
        response = self.client.post(self.url, 'x', content_type='text/plain', HTTP_X_API_KEY=INGEST_KEY)
        self.assertEqual(response.status_code, 415)
        self.assertEqual(self.client.get(self.url, HTTP_X_API_KEY=INGEST_KEY).status_code, 405)

    def test_database_error_never_crashes(self):
        with mock.patch('assistant.ingest.KnowledgeRecord.objects.create', side_effect=DatabaseError('down')), \
                self.assertLogs('assistant.views', 'ERROR'), self.assertLogs('django.request', 'ERROR'):
            response = self.post_json({'dataset': 'news', 'records': [{'title': 'x'}]})
        self.assertEqual(response.status_code, 503)
        self.assertIn('nothing was saved', response.json()['error'])
        self.assertFalse(KnowledgeRecord.objects.exists())

    def test_upload_rate_limit(self):
        for _ in range(120):
            self.post_json({'dataset': 'news', 'records': [{'title': 'x'}]})
        self.assertEqual(self.post_json({'dataset': 'news', 'records': [{'title': 'x'}]}).status_code, 429)


# ---------- Deploy checks ----------

class DeployCheckTests(TestCase):
    def ids(self):
        from .checks import assistant_settings
        return {message.id for message in assistant_settings(None)}

    @override_settings(BREVO_API_KEY='', SENDER_EMAIL='', ADMIN_EMAIL='someone@else.com', INGEST_API_KEY='short')
    def test_missing_or_wrong_settings_are_flagged(self):
        self.assertEqual(self.ids(), {'assistant.W001', 'assistant.E001', 'assistant.W002'})

    @override_settings(INGEST_API_KEY=INGEST_KEY, **EMAIL_SETTINGS)
    def test_live_settings_pass(self):
        self.assertEqual(self.ids(), set())


# ---------- Widget ----------

class WidgetTests(TestCase):
    def test_widget_on_dashboard_pages_for_staff(self):
        self.client.force_login(get_user_model().objects.get(username='admin'))
        page = self.client.get(reverse('grievances:complaint_list'))
        self.assertContains(page, 'data-assistant')
        self.assertContains(page, 'Citizen help')
        self.assertContains(page, f'data-url="{reverse("assistant:chat")}"')
        self.assertContains(page, 'js/assistant.js')
        self.assertContains(page, 'css/assistant.css')

    def test_widget_not_shown_to_citizens_or_visitors(self):
        self.assertNotContains(self.client.get(reverse('accounts:login')), 'data-assistant')
        session = self.client.session
        session['citizen_phone'] = '+919876543210'
        session.save()
        self.assertNotContains(self.client.get(reverse('grievances:track_list')), 'data-assistant')
