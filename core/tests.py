import json
import re
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone, translation

from accounts.decorators import CITIZEN_SESSION_KEY
from assistant.reports import build_report
from assistant.tests import BrevoReply, sent_payload
from grievances.models import Category, Complaint
from grievances.notify import status_message

PHONE = '+919876543210'
LOCALE_DIR = Path(settings.BASE_DIR) / 'locale' / 'hi' / 'LC_MESSAGES'


def use_hindi(client):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'hi'


def make_complaint(**overrides):
    data = {'citizen_name': 'TEST Lakshmi', 'citizen_contact': PHONE, 'title': 'TEST pothole',
            'description': 'A large pothole on Gandhi Street near the bus stop.', 'location': 'Gandhi Street',
            'taluk': 'Egmore', 'filed_by_citizen': True}
    data.update(overrides)
    return Complaint.objects.create(**data)


class LanguageChoiceTests(TestCase):
    def test_english_is_the_default_even_when_the_browser_prefers_hindi(self):
        response = self.client.get(reverse('accounts:login'), HTTP_ACCEPT_LANGUAGE='hi')
        self.assertContains(response, '<html lang="en">')
        self.assertContains(response, 'Sign in')
        self.assertEqual(response['Content-Language'], 'en')
        self.assertIn('Cookie', response['Vary'])

    def test_choosing_hindi_sets_a_cookie_and_goes_back(self):
        login = reverse('accounts:login')
        response = self.client.post(reverse('set_language'), {'language': 'hi', 'next': login})
        self.assertRedirects(response, login, fetch_redirect_response=False)
        cookie = response.cookies[settings.LANGUAGE_COOKIE_NAME]
        self.assertEqual(cookie.value, 'hi')
        self.assertEqual(cookie['samesite'], 'Lax')
        page = self.client.get(login)
        self.assertContains(page, '<html lang="hi">')
        self.assertContains(page, 'साइन इन करें')

    def test_switching_back_to_english(self):
        use_hindi(self.client)
        self.client.post(reverse('set_language'), {'language': 'en', 'next': '/login/'})
        self.assertContains(self.client.get(reverse('accounts:login')), '<html lang="en">')

    def test_unknown_language_cookie_falls_back_to_english(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ta'
        response = self.client.get(reverse('accounts:login'))
        self.assertContains(response, '<html lang="en">')

    def test_the_switch_offers_only_english_and_hindi(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertContains(response, 'value="en"')
        self.assertContains(response, 'value="hi"')
        self.assertContains(response, 'हिंदी')
        self.assertNotContains(response, 'value="ta"')

    def test_each_language_is_named_in_its_own_script(self):
        use_hindi(self.client)
        response = self.client.get(reverse('accounts:login'))
        self.assertContains(response, '>English</button>')
        self.assertContains(response, '>हिंदी</button>')

    def test_javascript_catalog_serves_hindi(self):
        use_hindi(self.client)
        response = self.client.get(reverse('javascript-catalog'))
        # The catalog is JSON, so Hindi letters arrive as \u escapes.
        self.assertContains(response, json.dumps('जमा हो रहा है')[1:-1])


class HindiPageTests(TestCase):
    def setUp(self):
        use_hindi(self.client)

    def test_dashboard_pages_render_in_hindi(self):
        self.client.force_login(get_user_model().objects.get(username='admin'))
        complaint = make_complaint(category=Category.objects.get(slug='water'))
        pages = {
            'grievances:home': 'आपका निगम एक नज़र में',
            'grievances:complaint_list': 'शिकायत डेटासेट',
            'grievances:complaint_create': 'दर्ज शिकायतें',
            'grievances:officers': 'प्रभारी अधिकारी',
            'grievances:help_desk': 'नागरिक सहायता केंद्र',
            'grievances:help_support': 'सहायता और समर्थन',
        }
        for name, text in pages.items():
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, '<html lang="hi">')
                self.assertContains(response, text)
        detail = self.client.get(complaint.get_absolute_url())
        self.assertContains(detail, 'जल आपूर्ति')      # seeded category name, through |tr
        self.assertContains(detail, 'दर्ज')            # status label

    def test_citizen_pages_render_in_hindi(self):
        session = self.client.session
        session[CITIZEN_SESSION_KEY] = PHONE
        session.save()
        complaint = make_complaint()
        pages = {
            reverse('grievances:complaint_registration'): 'सार्वजनिक शिकायत दर्ज करें',
            reverse('grievances:citizen_complaint_page'): 'सार्वजनिक शिकायत दर्ज करें',
            reverse('grievances:track_list'): 'आपकी शिकायतें',
            reverse('grievances:track_detail', args=[complaint.reference]): 'निगम की ओर से अपडेट',
        }
        for url, text in pages.items():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, '<html lang="hi">')
                self.assertContains(response, text)

    def test_form_errors_are_in_hindi(self):
        session = self.client.session
        session[CITIZEN_SESSION_KEY] = PHONE
        session.save()
        response = self.client.post(reverse('grievances:complaint_registration'), {})
        self.assertContains(response, 'अपना पूरा नाम दर्ज करें।')
        api = self.client.post(reverse('grievances:api_complaints'), {})
        self.assertEqual(api.status_code, 400)
        self.assertIn('अपना पूरा नाम दर्ज करें।', api.json()['errors']['citizen_name'])

    def test_otp_messages_are_in_hindi(self):
        with redirect_stdout(StringIO()):
            response = self.client.post(reverse('accounts:send_otp'), {'phone': '9876543210'}, follow=True)
        self.assertContains(response, 'OTP +91')
        self.assertContains(response, 'पर भेजा गया')


class EnglishOnlyMessagesTests(TestCase):
    """SMS and email stay English whatever language the admin is using."""

    def test_status_sms_is_english_under_hindi(self):
        complaint = make_complaint(status=Complaint.Status.IN_PROGRESS)
        with translation.override('hi'):
            self.assertEqual(status_message(complaint),
                             f'Your complaint {complaint.reference} is now In progress. - City Corporation')

    def test_daily_report_is_english_under_hindi(self):
        make_complaint()
        with translation.override('hi'):
            subject, html, text, count = build_report(timezone.localdate())
        self.assertTrue(subject.startswith('Daily complaints report'))
        self.assertNotRegex(text, '[ऀ-ॿ]')


class HindiChatTests(TestCase):
    def setUp(self):
        cache.clear()
        use_hindi(self.client)
        self.client.force_login(get_user_model().objects.get(username='admin'))

    def say(self, message):
        return self.client.post(reverse('assistant:chat'), json.dumps({'message': message}),
                                content_type='application/json').json()

    def test_hindi_greeting_gets_a_hindi_reply(self):
        self.assertIn('नमस्ते', self.say('नमस्ते')['reply'])

    def test_search_finds_a_devanagari_name(self):
        complaint = make_complaint(citizen_name='राजेश कुमार')
        data = self.say('राजेश')
        self.assertEqual([r['ref'] for r in data['results']], [complaint.reference])
        self.assertIn('मिले', data['reply'])

    def test_hindi_mail_request_still_sends_an_english_email(self):
        complaint = make_complaint()
        self.say('TEST Lakshmi')
        with self.settings(BREVO_API_KEY='test-brevo-key', SENDER_EMAIL='sender@example.com',
                           ADMIN_EMAIL='govermentowncoporation@gmail.com'), \
                mock.patch('urllib.request.urlopen', return_value=BrevoReply()) as urlopen:
            data = self.say('मुझे ईमेल भेजो')
        self.assertEqual(data['email']['status'], 'sent')
        _, payload = sent_payload(urlopen)
        self.assertIn(complaint.reference, payload['htmlContent'])
        self.assertNotRegex(payload['subject'], '[ऀ-ॿ]')


class TranslationFileTests(TestCase):
    def entries(self, name):
        blocks = (LOCALE_DIR / name).read_text(encoding='utf-8').split('\n\n')[1:]
        for block in blocks:
            msgid = re.search(r'^msgid "(.*)"$', block, re.M)
            msgstrs = re.findall(r'^msgstr(?:\[\d\])? "(.*)"((?:\n".*")*)', block, re.M)
            yield block, msgid, msgstrs

    def test_every_message_has_a_hindi_translation(self):
        for name in ('django.po', 'djangojs.po'):
            for block, msgid, msgstrs in self.entries(name):
                with self.subTest(file=name, block=block[:80]):
                    self.assertNotIn('#, fuzzy', block)
                    self.assertTrue(msgstrs)
                    for first, rest in msgstrs:
                        self.assertTrue(first or rest, 'empty msgstr')

    def test_compiled_files_are_loaded(self):
        with translation.override('hi'):
            self.assertEqual(translation.gettext('Sign in'), 'साइन इन करें')
            self.assertEqual(translation.pgettext('brand, first word', 'City'), 'नगर')
            self.assertEqual(translation.ngettext('%(count)s record', '%(count)s records', 2) % {'count': 2},
                             '2 रिकॉर्ड')
