import csv
import io
import os
import shutil
import tempfile
from contextlib import redirect_stdout
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.decorators import CITIZEN_SESSION_KEY

from . import services
from .forms import CitizenComplaintForm

from .models import Category, Complaint, ComplaintUpdate, Notice, Officer, SupportEnquiry, Ward


def sign_in_admin(client):
    # force_login: the demo password 1234 is refused outside debug mode (accounts/backends.py).
    client.force_login(get_user_model().objects.get(username='admin'))


class DashboardTestCase(TestCase):
    def setUp(self):
        sign_in_admin(self.client)
        self.water = Category.objects.get(slug='water')
        self.roads = Category.objects.get(slug='roads')
        self.ward = Ward.objects.get(number=1)

    def make_complaint(self, **overrides):
        data = {
            'citizen_name': 'Priya S',
            'citizen_contact': '+919876543210',
            'title': 'Sewage overflowing near bus stop',
            'description': 'Sewage has been overflowing on the main road since Monday morning.',
            'category': self.water,
            'ward': self.ward,
            'location': 'Pantheon Road',
            'channel': 'whatsapp',
            'priority': 'high',
        }
        data.update(overrides)
        return Complaint.objects.create(**data)


class PageTests(DashboardTestCase):
    def test_every_dashboard_page_opens(self):
        for name in ('home', 'complaint_create', 'complaint_list', 'officers', 'help_desk', 'help_support'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f'grievances:{name}')).status_code, 200)

    def test_reference_data_is_present_but_no_sample_records(self):
        self.assertEqual(Category.objects.count(), 4)
        self.assertEqual(Ward.objects.count(), 8)
        self.assertEqual(Complaint.objects.count(), 0)
        self.assertEqual(Officer.objects.count(), 0)
        self.assertEqual(Notice.objects.count(), 0)

    def test_empty_states(self):
        self.assertContains(self.client.get(reverse('grievances:complaint_list')), 'No complaints yet.')
        self.assertContains(self.client.get(reverse('grievances:officers')), 'No officers have been added yet.')
        self.assertContains(self.client.get(reverse('grievances:home')), 'No notices published yet.')


class ComplaintFlowTests(DashboardTestCase):
    def test_filed_complaint_page_lists_only_citizen_complaints(self):
        self.assertContains(self.client.get(reverse('grievances:complaint_create')),
                            'No complaints have been filed by citizens yet.')
        staff_entry = self.make_complaint()
        citizen_entry = self.make_complaint(title='Streetlight not working', filed_by_citizen=True,
                                            category=None, ward=None, taluk='Egmore')
        response = self.client.get(reverse('grievances:complaint_create'))
        # (The admin entry still appears in the top bar's notifications, so check the listed rows.)
        self.assertEqual(list(response.context['page']), [citizen_entry])
        self.assertNotIn(staff_entry, response.context['page'])
        self.assertEqual(response.context['summary']['pending'], 1)
        pending = self.client.get(reverse('grievances:complaint_create'), {'review': 'pending', 'q': 'egmore'})
        self.assertEqual(pending.context['page'].paginator.count, 1)
        done = self.client.get(reverse('grievances:complaint_create'), {'review': 'done'})
        self.assertEqual(done.context['page'].paginator.count, 0)

    def test_reference_is_generated(self):
        self.assertRegex(self.make_complaint().reference, r'^CC-\d{4}-00001$')

    def test_status_update_sets_resolved_time_and_history(self):
        complaint = self.make_complaint()
        self.client.post(complaint.get_absolute_url(), {'status': 'resolved', 'note': 'Drain cleared.'})
        complaint.refresh_from_db()
        self.assertEqual(complaint.status, Complaint.Status.RESOLVED)
        self.assertIsNotNone(complaint.resolved_at)
        self.assertIn('Drain cleared.', complaint.updates.last().note)
        page = self.client.get(reverse('grievances:home'))
        self.assertEqual(page.context['stats']['resolved'], 1)
        self.assertEqual(page.context['stats']['reported'], 1)

    def test_filters_and_export(self):
        self.make_complaint()
        self.make_complaint(title='Large pothole near school gate', category=self.roads,
                            channel='phone', priority='medium')
        listing = self.client.get(reverse('grievances:complaint_list'), {'category': self.roads.pk})
        self.assertEqual(listing.context['page'].paginator.count, 1)
        self.assertEqual(self.client.get(reverse('grievances:complaint_list'),
                                         {'q': 'sewage'}).context['page'].paginator.count, 1)
        export = self.client.get(reverse('grievances:complaint_export'))
        self.assertEqual(export['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('Large pothole near school gate', export.content.decode('utf-8'))

    def test_channel_chart_counts_channels(self):
        self.make_complaint()
        chart = self.client.get(reverse('grievances:home')).context['stats']['chart']['channels']
        self.assertEqual(chart['labels'], ['WhatsApp'])
        self.assertEqual(chart['counts'], [1])

    def test_track_complaint_on_help_desk(self):
        reference = self.make_complaint().reference
        self.assertContains(self.client.get(reverse('grievances:help_desk'), {'ref': reference}), 'Open full details')
        self.assertContains(self.client.get(reverse('grievances:help_desk'), {'ref': 'abc'}), 'Complaint IDs look like')


class SupportTests(DashboardTestCase):
    def test_enquiry_is_saved(self):
        response = self.client.post(reverse('grievances:help_support'), {
            'name': 'Arjun', 'email': 'arjun@example.com', 'topic': 'filing',
            'message': 'How do I attach a photo to a complaint?',
        }, follow=True)
        enquiry = SupportEnquiry.objects.get()
        self.assertEqual(enquiry.reference, 'SUP-00001')
        self.assertContains(response, 'SUP-00001')


# A 1x1 PNG
PNG_BYTES = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15'
             b'\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='citycorp-test-media-'))
class CitizenRegistrationTests(TestCase):
    PHONE = '+919876543210'

    def setUp(self):
        session = self.client.session
        session[CITIZEN_SESSION_KEY] = self.PHONE
        session.save()
        self.url = reverse('grievances:complaint_registration')

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def complaint_data(self, **overrides):
        data = {
            'citizen_name': 'Lakshmi Narayanan',
            'citizen_address': '12, Gandhi Street, Anna Nagar',
            'taluk': 'Egmore',
            'description': 'A large pothole has developed on the main road near the bus stop.',
            'location': 'Gandhi Street, near the bus stop',
            'impact': 'Vehicles slow down and pedestrians find it hard to walk.',
            'reported_before': 'no',
            'declaration': 'on',
        }
        data.update(overrides)
        return data

    def test_page_shows_the_form(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Register a Public Complaint')
        self.assertContains(response, '+91 98XXXXXX10')

    def test_citizen_registers_a_complaint_with_files(self):
        response = self.client.post(self.url, self.complaint_data(
            reported_before='yes', previous_reference='GCC/2026/1182',
            attachment=SimpleUploadedFile('pothole.png', PNG_BYTES, content_type='image/png'),
            previous_copy=SimpleUploadedFile('earlier.pdf', b'%PDF-1.4', content_type='application/pdf'),
        ))
        complaint = Complaint.objects.get()
        self.assertRedirects(response, self.url + '?submitted=1')
        self.assertRegex(complaint.reference, r'^CC-\d{4}-00001$')
        self.assertEqual(complaint.citizen_contact, self.PHONE)
        self.assertEqual(complaint.channel, Complaint.Channel.PORTAL)
        self.assertIsNone(complaint.category)
        self.assertIsNone(complaint.ward)
        self.assertTrue(complaint.previously_reported)
        self.assertEqual(complaint.previous_reference, 'GCC/2026/1182')
        self.assertTrue(complaint.attachment.name.endswith('.png'))
        self.assertTrue(complaint.previous_copy.name.endswith('.pdf'))
        self.assertEqual(complaint.updates.count(), 1)

        done = self.client.get(response['Location'])
        self.assertContains(done, complaint.reference)
        self.assertContains(done, 'Your complaint has been registered')

    def test_answering_no_drops_earlier_complaint_details(self):
        self.client.post(self.url, self.complaint_data(reported_before='no', previous_reference='OLD-1'))
        complaint = Complaint.objects.get()
        self.assertFalse(complaint.previously_reported)
        self.assertEqual(complaint.previous_reference, '')

    def test_missing_fields_are_reported(self):
        response = self.client.post(self.url, {})
        for message in ('Enter your full name.', 'Enter your residential address.', 'Enter your taluk.',
                        'Describe the problem you are facing.', 'Enter the area affected by the problem.',
                        'Explain how this problem affects you.', 'Select Yes or No.',
                        'Please confirm that the information you have provided is accurate.'):
            self.assertContains(response, message)
        self.assertEqual(Complaint.objects.count(), 0)

    def test_wrong_file_type_is_refused(self):
        response = self.client.post(self.url, self.complaint_data(
            attachment=SimpleUploadedFile('notes.txt', b'hello', content_type='text/plain')))
        self.assertContains(response, 'is not an accepted format')
        self.assertEqual(Complaint.objects.count(), 0)

    def test_each_validation_rule(self):
        cases = [
            ('citizen_name', 'L', 'at least 2 characters'),
            ('citizen_name', 'Lakshmi 123', 'Use letters, spaces'),
            ('citizen_address', 'Short st', 'Please enter your full address'),
            ('citizen_address', 'x' * 501, 'under 500 characters'),
            ('taluk', '12', 'Enter a valid taluk name.'),
            ('description', 'x' * 2001, 'under 2000 characters'),
            ('location', 'ab', 'Please enter the locality'),
            ('impact', 'Too bad', 'at least 10 characters'),
            ('impact', 'x' * 1001, 'under 1000 characters'),
        ]
        for field, value, message in cases:
            with self.subTest(field=field, value=value[:20]):
                form = CitizenComplaintForm(self.complaint_data(**{field: value}))
                self.assertFalse(form.is_valid())
                self.assertIn(message, form.errors[field][0])
        bad_reference = CitizenComplaintForm(self.complaint_data(reported_before='yes', previous_reference='#12;DROP'))
        self.assertIn('Use only letters, numbers', bad_reference.errors['previous_reference'][0])
        # Tamil and Hindi names are allowed.
        self.assertTrue(CitizenComplaintForm(self.complaint_data(citizen_name='லட்சுமி நாராயணன்')).is_valid())
        self.assertTrue(CitizenComplaintForm(self.complaint_data(citizen_name='लक्ष्मी')).is_valid())

    def post_with(self, **files):
        return self.client.post(self.url, self.complaint_data(reported_before='yes', **files))

    def test_photo_over_5_mb_is_refused(self):
        too_big = SimpleUploadedFile('huge.png', b'0' * (5 * 1024 * 1024 + 1), content_type='image/png')
        self.assertContains(self.post_with(attachment=too_big), 'is larger than the 5 MB limit')
        self.assertEqual(Complaint.objects.count(), 0)

    def test_photo_of_exactly_5_mb_is_accepted(self):
        just_fits = SimpleUploadedFile('big.png', b'0' * (5 * 1024 * 1024), content_type='image/png')
        self.assertEqual(self.post_with(attachment=just_fits).status_code, 302)
        self.assertEqual(Complaint.objects.count(), 1)

    def test_previous_copy_over_5_mb_is_refused(self):
        too_big = SimpleUploadedFile('copy.pdf', b'0' * (5 * 1024 * 1024 + 1), content_type='application/pdf')
        self.assertContains(self.post_with(previous_copy=too_big), 'is larger than the 5 MB limit')
        self.assertEqual(Complaint.objects.count(), 0)

    def test_wrong_file_types_are_refused(self):
        cases = {
            'attachment': [('notes.pdf', 'application/pdf'), ('clip.gif', 'image/gif'), ('page.html', 'text/html')],
            'previous_copy': [('letter.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
                              ('photo.webp', 'image/webp')],
        }
        for field, files in cases.items():
            for name, content_type in files:
                with self.subTest(field=field, name=name):
                    upload = SimpleUploadedFile(name, b'data', content_type=content_type)
                    self.assertContains(self.post_with(**{field: upload}), 'is not an accepted format')
        self.assertEqual(Complaint.objects.count(), 0)

    def test_allowed_type_with_a_wrong_extension_is_refused(self):
        # The browser-sent type says PNG, but the name ends in .exe: the model's extension check catches it.
        disguised = SimpleUploadedFile('setup.exe', PNG_BYTES, content_type='image/png')
        response = self.post_with(attachment=disguised)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.context['form'].errors)
        self.assertEqual(Complaint.objects.count(), 0)

    def test_api_refuses_large_and_wrong_files_with_field_errors(self):
        too_big = SimpleUploadedFile('huge.jpg', b'0' * (5 * 1024 * 1024 + 1), content_type='image/jpeg')
        wrong = SimpleUploadedFile('copy.txt', b'data', content_type='text/plain')
        response = self.client.post(reverse('grievances:api_complaints'), self.complaint_data(
            reported_before='yes', attachment=too_big, previous_copy=wrong))
        self.assertEqual(response.status_code, 400)
        errors = response.json()['errors']
        self.assertIn('larger than the 5 MB limit', errors['attachment'][0])
        self.assertIn('is not an accepted format', errors['previous_copy'][0])

    def test_citizen_complaint_appears_for_admins(self):
        self.client.post(self.url, self.complaint_data())
        complaint = Complaint.objects.get()
        self.client.post(reverse('accounts:logout'))
        sign_in_admin(self.client)
        listing = self.client.get(reverse('grievances:complaint_list'))
        self.assertContains(listing, complaint.reference)
        self.assertContains(listing, 'Egmore taluk')
        detail = self.client.get(complaint.get_absolute_url())
        self.assertContains(detail, '12, Gandhi Street, Anna Nagar')
        self.assertContains(detail, 'Not assigned yet')
        self.assertEqual(self.client.get(reverse('grievances:complaint_export')).status_code, 200)

        # Dashboard: counted in the cards, the trend bars, the channel chart and the "no ward yet" bar
        stats = self.client.get(reverse('grievances:home')).context['stats']
        self.assertEqual(stats['reported'], 1)
        self.assertEqual(stats['citizen_filed'], 1)
        self.assertEqual(stats['waiting'], 1)
        self.assertEqual(sum(stats['chart']['monthly']['reported']), 1)
        self.assertEqual(stats['chart']['channels']['labels'], ['Portal'])
        self.assertEqual(stats['unassigned']['open'], 1)

        # Filed complaint page, with the menu badge counting it as waiting for review
        filed = self.client.get(reverse('grievances:complaint_create'))
        self.assertContains(filed, complaint.reference)
        self.assertContains(filed, 'Assign category &amp; ward')
        file_item = next(item for item in filed.context['menu'] if item['key'] == 'file')
        self.assertEqual(file_item['badge'], '1')

    def test_reviewing_routes_the_complaint_to_the_officer(self):
        self.client.post(self.url, self.complaint_data())
        complaint = Complaint.objects.get()
        self.assertTrue(complaint.filed_by_citizen)
        roads, ward = Category.objects.get(slug='roads'), Ward.objects.get(number=1)
        officer = Officer.objects.create(name='Kavitha R', designation='Assistant Engineer', category=roads, ward=ward)
        self.client.post(reverse('accounts:logout'))
        sign_in_admin(self.client)

        self.client.post(complaint.get_absolute_url(), {'category': roads.pk, 'ward': ward.pk, 'status': 'submitted'})
        complaint.refresh_from_db()
        self.assertEqual((complaint.category, complaint.ward, complaint.officer), (roads, ward, officer))
        self.assertEqual(complaint.status, Complaint.Status.ASSIGNED)
        self.assertFalse(complaint.needs_review)
        self.assertIn('Category set to Roads', complaint.updates.last().note)

        stats = self.client.get(reverse('grievances:home')).context['stats']
        self.assertEqual(stats['unassigned']['open'], 0)
        self.assertEqual(next(w for w in stats['wards'] if w.pk == ward.pk).open_count, 1)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='citycorp-test-media-'))
class ComplaintApiTests(TestCase):
    """POST /api/complaints/, used by nagar-seva/js/complaint-form.js."""

    PHONE = '+919876543210'

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.url = reverse('grievances:api_complaints')

    def verify_citizen(self, client=None):
        client = client or self.client
        session = client.session
        session[CITIZEN_SESSION_KEY] = self.PHONE
        session.save()

    def complaint_data(self, **overrides):
        return CitizenRegistrationTests.complaint_data(None, **overrides)

    def test_registers_a_complaint_with_both_files(self):
        self.verify_citizen()
        response = self.client.post(self.url, self.complaint_data(
            reported_before='yes', previous_reference='GCC/2026/1182',
            attachment=SimpleUploadedFile('pothole.png', PNG_BYTES, content_type='image/png'),
            previous_copy=SimpleUploadedFile('earlier.pdf', b'%PDF-1.4', content_type='application/pdf'),
        ))
        self.assertEqual(response.status_code, 201)
        body = response.json()['complaint']
        complaint = Complaint.objects.get()
        self.assertRegex(body['reference'], r'^CC-\d{4}-\d{5}$')
        self.assertEqual(body['reference'], complaint.reference)
        self.assertEqual(body['attachment'], 'pothole.png')
        self.assertEqual(body['previous_copy'], 'earlier.pdf')
        self.assertEqual(body['status'], 'Submitted')
        self.assertTrue(body['previously_reported'])
        # Same record the server-rendered form would make
        self.assertEqual(complaint.citizen_contact, self.PHONE)
        self.assertTrue(complaint.filed_by_citizen)
        self.assertEqual(complaint.channel, Complaint.Channel.PORTAL)
        self.assertEqual(complaint.previous_reference, 'GCC/2026/1182')
        self.assertTrue(complaint.attachment.name.endswith('.png'))
        self.assertTrue(complaint.previous_copy.name.endswith('.pdf'))
        self.assertEqual(complaint.updates.count(), 1)

    def test_contact_comes_from_the_verified_session_not_the_request(self):
        self.verify_citizen()
        self.client.post(self.url, self.complaint_data(citizen_contact='+919000000000'))
        self.assertEqual(Complaint.objects.get().citizen_contact, self.PHONE)

    def test_invalid_form_returns_field_errors(self):
        self.verify_citizen()
        response = self.client.post(self.url, self.complaint_data(
            citizen_name='', description='Too short',
            attachment=SimpleUploadedFile('notes.txt', b'hello', content_type='text/plain')))
        self.assertEqual(response.status_code, 400)
        errors = response.json()['errors']
        self.assertEqual(errors['citizen_name'], ['Enter your full name.'])
        self.assertIn('at least 20 characters', errors['description'][0])
        self.assertIn('is not an accepted format', errors['attachment'][0])
        self.assertEqual(Complaint.objects.count(), 0)

    def test_missing_declaration_and_answer_are_reported(self):
        self.verify_citizen()
        data = self.complaint_data()
        del data['declaration'], data['reported_before']
        errors = self.client.post(self.url, data).json()['errors']
        self.assertEqual(errors['reported_before'], ['Select Yes or No.'])
        self.assertIn('declaration', errors)

    def test_answering_no_drops_previous_details(self):
        self.verify_citizen()
        self.client.post(self.url, self.complaint_data(
            reported_before='no', previous_reference='OLD-1',
            previous_copy=SimpleUploadedFile('earlier.pdf', b'%PDF-1.4', content_type='application/pdf')))
        complaint = Complaint.objects.get()
        self.assertFalse(complaint.previously_reported)
        self.assertEqual(complaint.previous_reference, '')
        self.assertFalse(complaint.previous_copy)

    def test_not_signed_in_returns_401_with_login_url(self):
        response = self.client.post(self.url, self.complaint_data())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['login_url'], reverse('accounts:login'))
        self.assertEqual(Complaint.objects.count(), 0)

    def test_only_post_is_allowed(self):
        self.verify_citizen()
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_csrf_token_is_required(self):
        client = Client(enforce_csrf_checks=True)
        self.verify_citizen(client)
        with self.assertLogs('django.security.csrf', 'WARNING'):
            self.assertEqual(client.post(self.url, self.complaint_data()).status_code, 403)
        # The page hands out the token, and the JS sends it back in X-CSRFToken.
        page = client.get(reverse('grievances:citizen_complaint_page'))
        token = page.context['csrf_token']
        response = client.post(self.url, self.complaint_data(), headers={'X-CSRFToken': str(token)})
        self.assertEqual(response.status_code, 201)


class CitizenComplaintPageTests(TestCase):
    """nagar-seva/filed-complaint.html, served by Django at /citizen/complaint/."""

    def setUp(self):
        self.url = reverse('grievances:citizen_complaint_page')

    def test_needs_a_verified_citizen(self):
        self.assertRedirects(self.client.get(self.url), reverse('accounts:login'))

    def test_page_posts_to_the_api_with_a_csrf_token(self):
        session = self.client.session
        session[CITIZEN_SESSION_KEY] = '+919876543210'
        session.save()
        Complaint.objects.create(citizen_name='Ravi K', citizen_contact='+919876500000', taluk='Egmore',
                                 title='Streetlight not working', description='Streetlight has been off for a week.')
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'filed-complaint.html')
        self.assertContains(response, 'Register a Public Complaint')
        self.assertContains(response, f'action="{reverse("grievances:api_complaints")}"')
        self.assertContains(response, f'data-login-url="{reverse("accounts:login")}"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, '<option value="Egmore">')
        for asset in ('nagar-seva/css/style.css', 'nagar-seva/js/complaint-form.js',
                      'nagar-seva/assets/images/emblem.svg'):
            with self.subTest(asset=asset):
                self.assertContains(response, settings.STATIC_URL + asset)
                self.assertIsNotNone(finders.find(asset))
        self.assertNotContains(response, 'localStorage')

    def test_page_template_itself_is_not_a_static_file(self):
        self.assertIsNone(finders.find('nagar-seva/filed-complaint.html'))

    def test_script_no_longer_saves_to_local_storage(self):
        script = open(finders.find('nagar-seva/js/complaint-form.js'), encoding='utf-8').read()
        self.assertNotIn('localStorage', script)
        self.assertIn('/api/complaints/', script)  # documented in its header


class CitizenTrackingTests(TestCase):
    """/track/ and /track/<reference>/: a citizen sees only complaints filed with their verified phone."""

    MINE = '+919876543210'
    OTHER = '+919123456789'

    def setUp(self):
        self.verify(self.MINE)

    def verify(self, phone):
        session = self.client.session
        session[CITIZEN_SESSION_KEY] = phone
        session.save()

    def make(self, contact, title='Pothole near the bus stop', **extra):
        return Complaint.objects.create(
            citizen_name='Lakshmi Narayanan', citizen_contact=contact, title=title, filed_by_citizen=True,
            description='A large pothole near the bus stop on the main road.', **extra)

    def test_list_shows_only_my_complaints(self):
        mine = self.make(self.MINE, title='Streetlight not working')
        theirs = self.make(self.OTHER, title='Garbage not collected')
        emailed = self.make('lakshmi@example.com', title='Water leak on Gandhi Street')
        response = self.client.get(reverse('grievances:track_list'))
        self.assertEqual(list(response.context['page']), [mine])
        self.assertContains(response, mine.reference)
        for hidden in (theirs, emailed):
            self.assertNotContains(response, hidden.reference)
            self.assertNotContains(response, hidden.title)

    def test_list_shows_all_complaints_across_pages(self):
        for number in range(17):
            self.make(self.MINE, title=f'Broken drain cover number {number}')
        self.make(self.OTHER)
        first = self.client.get(reverse('grievances:track_list'))
        self.assertEqual(first.context['page'].paginator.count, 17)
        self.assertEqual(len(first.context['page']), 15)
        self.assertContains(first, 'Showing 1–15 of 17')
        second = self.client.get(reverse('grievances:track_list'), {'page': 2})
        self.assertEqual(len(second.context['page']), 2)
        self.assertTrue(all(c.citizen_contact == self.MINE for c in second.context['page']))

    def test_empty_list(self):
        self.assertContains(self.client.get(reverse('grievances:track_list')),
                            "haven't registered any complaints with this mobile number")

    def test_citizen_cannot_open_another_citizens_complaint(self):
        theirs = self.make(self.OTHER, title='Garbage not collected near my house',
                           citizen_address='45, Lake Road, Velachery')
        ComplaintUpdate.objects.create(complaint=theirs, status='submitted', note='Private remark', is_public=True)
        response = self.client.get(reverse('grievances:track_detail', args=[theirs.reference]))
        self.assertEqual(response.status_code, 404)
        for secret in (theirs.title, theirs.citizen_address, 'Private remark'):
            self.assertNotContains(response, secret, status_code=404)
        # The owner can open the same URL.
        self.verify(self.OTHER)
        self.assertEqual(self.client.get(reverse('grievances:track_detail', args=[theirs.reference])).status_code, 200)

    def test_unknown_reference_is_404(self):
        self.assertEqual(self.client.get(reverse('grievances:track_detail', args=['CC-2026-99999'])).status_code, 404)

    def test_pages_need_a_verified_citizen(self):
        complaint = self.make(self.MINE)
        self.client.session.flush()
        self.client.cookies.clear()
        for url in (reverse('grievances:track_list'), reverse('grievances:track_detail', args=[complaint.reference])):
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), reverse('accounts:login'))

    def test_staff_login_does_not_grant_citizen_access(self):
        complaint = self.make(self.OTHER)
        self.client.session.flush()
        self.client.cookies.clear()
        sign_in_admin(self.client)
        url = reverse('grievances:track_detail', args=[complaint.reference])
        self.assertRedirects(self.client.get(url), reverse('accounts:login'))

    def test_detail_shows_timeline_and_only_public_notes(self):
        complaint = self.make(self.MINE, category=Category.objects.get(slug='roads'), ward=Ward.objects.get(number=1))
        ComplaintUpdate.objects.create(complaint=complaint, status='submitted', note='Registered online.', is_public=True)
        ComplaintUpdate.objects.create(complaint=complaint, status='assigned',
                                       note='Assigned to Kavitha R. Contractor slow, escalate.', is_public=False)
        ComplaintUpdate.objects.create(complaint=complaint, status='in_progress',
                                       note='Repair work will start on Monday.', is_public=True)
        complaint.status = Complaint.Status.IN_PROGRESS
        complaint.save()

        response = self.client.get(reverse('grievances:track_detail', args=[complaint.reference]))
        self.assertEqual([step['state'] for step in response.context['timeline']],
                         ['done', 'done', 'current', 'pending'])
        self.assertContains(response, 'Registered online.')
        self.assertContains(response, 'Repair work will start on Monday.')
        self.assertNotContains(response, 'Contractor slow')
        self.assertNotContains(response, 'Kavitha')
        self.assertContains(response, 'Status updated to assigned.')  # the private step still shows
        self.assertContains(response, 'Roads')

    def test_registration_note_is_public(self):
        self.client.post(reverse('grievances:complaint_registration'),
                         CitizenRegistrationTests.complaint_data(None))
        complaint = Complaint.objects.get()
        self.assertTrue(complaint.updates.get().is_public)
        response = self.client.get(reverse('grievances:track_detail', args=[complaint.reference]))
        self.assertContains(response, 'Complaint registered by the citizen through the portal')

    def test_registration_page_links_to_tracking(self):
        complaint = self.make(self.MINE)
        response = self.client.get(reverse('grievances:complaint_registration'))
        self.assertContains(response, f'href="{reverse("grievances:track_list")}"')
        self.assertContains(response, 'Track all 1 complaint')
        self.assertContains(response, f'href="{reverse("grievances:track_detail", args=[complaint.reference])}"')

    def test_success_panel_links_to_the_new_complaint(self):
        response = self.client.post(reverse('grievances:complaint_registration'),
                                    CitizenRegistrationTests.complaint_data(None), follow=True)
        reference = Complaint.objects.get().reference
        self.assertContains(response, f'href="{reverse("grievances:track_detail", args=[reference])}"')


@override_settings(SMS_BACKEND='console')
class StatusSmsTests(DashboardTestCase):
    """complaint_detail texts the citizen when an admin changes the status of a citizen-filed complaint."""

    PHONE = '+919876543210'

    def citizen_complaint(self, **overrides):
        return self.make_complaint(**{'filed_by_citizen': True, 'citizen_contact': self.PHONE,
                                      'channel': 'portal', **overrides})

    def update(self, complaint, **data):
        """POST the update form; returns (response after redirect, console output)."""
        with redirect_stdout(io.StringIO()) as out:
            response = self.client.post(complaint.get_absolute_url(), data, follow=True)
        return response, out.getvalue()

    def test_status_change_texts_the_citizen(self):
        complaint = self.citizen_complaint()
        response, sent = self.update(complaint, status='in_progress', category=self.water.pk, ward=self.ward.pk)
        self.assertEqual(sent.strip(), f'[SMS to {self.PHONE}] Your complaint {complaint.reference} '
                                       'is now In progress. - City Corporation')
        self.assertContains(response, 'The citizen was sent an SMS about the new status.')

    def test_automatic_assignment_counts_as_a_status_change(self):
        complaint = self.citizen_complaint(category=None, ward=None, status='submitted')
        Officer.objects.create(name='Kavitha R', designation='Assistant Engineer', category=self.water, ward=self.ward)
        _, sent = self.update(complaint, status='submitted', category=self.water.pk, ward=self.ward.pk)
        self.assertIn(f'Your complaint {complaint.reference} is now Assigned. - City Corporation', sent)

    def test_no_sms_without_a_status_change(self):
        complaint = self.citizen_complaint(status='assigned')
        response, sent = self.update(complaint, status='assigned', category=self.water.pk, ward=self.ward.pk,
                                     note='Inspection on Monday.')
        self.assertEqual(sent, '')
        self.assertEqual(complaint.updates.count(), 1)  # the note is still saved
        self.assertNotContains(response, 'SMS')

    def test_no_sms_for_staff_entered_complaints(self):
        complaint = self.make_complaint()  # filed_by_citizen=False, phone contact
        _, sent = self.update(complaint, status='resolved', category=self.water.pk, ward=self.ward.pk)
        self.assertEqual(sent, '')

    def test_no_sms_without_a_valid_mobile_number(self):
        for contact in ('priya@example.com', '12345', ''):
            with self.subTest(contact=contact):
                complaint = self.citizen_complaint(citizen_contact=contact)
                _, sent = self.update(complaint, status='resolved', category=self.water.pk, ward=self.ward.pk)
                self.assertEqual(sent, '')
                complaint.refresh_from_db()
                self.assertEqual(complaint.status, 'resolved')

    @override_settings(SMS_BACKEND='fast2sms', FAST2SMS_API_KEY='')
    def test_failed_sms_is_logged_and_the_update_is_kept(self):
        complaint = self.citizen_complaint()
        with self.assertLogs('grievances.notify', 'ERROR') as logs:
            response, _ = self.update(complaint, status='resolved', category=self.water.pk, ward=self.ward.pk)
        complaint.refresh_from_db()
        self.assertEqual(complaint.status, Complaint.Status.RESOLVED)
        self.assertIsNotNone(complaint.resolved_at)
        self.assertEqual(complaint.updates.last().status, 'resolved')
        self.assertContains(response, 'the SMS to the citizen could not be sent')
        self.assertIn(complaint.reference, logs.output[0])
        self.assertNotIn('9876543210', '\n'.join(logs.output))  # no phone numbers in the log

    def test_unexpected_error_while_sending_does_not_block_the_save(self):
        complaint = self.citizen_complaint()
        with mock.patch('accounts.sms.send_message', side_effect=RuntimeError('provider changed its reply')), \
                self.assertLogs('grievances.notify', 'ERROR'):
            response, _ = self.update(complaint, status='in_progress', category=self.water.pk, ward=self.ward.pk)
        self.assertEqual(response.status_code, 200)
        complaint.refresh_from_db()
        self.assertEqual(complaint.status, Complaint.Status.IN_PROGRESS)


class DashboardFilterTests(DashboardTestCase):
    """Search, filters and sort orders on the Dataset, Filed complaint and Officers pages."""

    def listed(self, name, **params):
        return list(self.client.get(reverse(f'grievances:{name}'), params).context['page'])

    def test_dataset_filters_and_sorts(self):
        low = self.make_complaint(title='Streetlight flickering at night', priority='low', status='resolved',
                                  ward=Ward.objects.get(number=3), channel='email')
        high = self.make_complaint(title='Sewage overflow near the market', priority='high', status='submitted')
        medium = self.make_complaint(title='Pothole near the school gate', priority='medium', status='in_progress',
                                     category=self.roads, ward=Ward.objects.get(number=2))
        self.assertEqual(self.listed('complaint_list', status='resolved'), [low])
        self.assertEqual(self.listed('complaint_list', priority='high'), [high])
        self.assertEqual(self.listed('complaint_list', channel='email'), [low])
        self.assertEqual(self.listed('complaint_list', ward=self.ward.pk), [high])
        self.assertEqual(self.listed('complaint_list', q=medium.reference), [medium])
        self.assertEqual(self.listed('complaint_list', sort='oldest'), [low, high, medium])
        self.assertEqual(self.listed('complaint_list', sort='priority'), [high, medium, low])
        self.assertEqual(self.listed('complaint_list', sort='status'), [high, medium, low])
        self.assertEqual(self.listed('complaint_list', sort='ward'), [high, medium, low])
        # An invalid value is ignored, not an error.
        self.assertEqual(len(self.listed('complaint_list', status='nonsense')), 3)

    def test_export_follows_the_filters(self):
        self.make_complaint(title='Sewage overflow near the market')
        self.make_complaint(title='Pothole near the school gate', category=self.roads)
        content = self.client.get(reverse('grievances:complaint_export'), {'category': self.roads.pk}).content.decode()
        self.assertIn('Pothole near the school gate', content)
        self.assertNotIn('Sewage overflow', content)

    def test_filed_complaint_filters(self):
        first = self.make_complaint(filed_by_citizen=True, category=None, ward=None, taluk='Egmore')
        second = self.make_complaint(filed_by_citizen=True, status='resolved', citizen_name='Ravi K')
        self.assertEqual(self.listed('complaint_create', status='resolved'), [second])
        self.assertEqual(self.listed('complaint_create', q='ravi'), [second])
        self.assertEqual(self.listed('complaint_create', sort='oldest'), [first, second])

    def test_officers_page_filters_and_sorts(self):
        ward2 = Ward.objects.get(number=2)
        busy = Officer.objects.create(name='Zara P', designation='Assistant Engineer', category=self.water, ward=self.ward)
        idle = Officer.objects.create(name='Arun V', designation='Junior Engineer', category=self.roads, ward=ward2)
        self.make_complaint(officer=busy, status='resolved')
        self.make_complaint(officer=busy)
        officers = lambda **params: [o.name for o in self.client.get(reverse('grievances:officers'), params).context['officers']]
        self.assertEqual(officers(), ['Zara P', 'Arun V'])               # resolution rate, unassigned last
        self.assertEqual(officers(sort='name'), ['Arun V', 'Zara P'])
        self.assertEqual(officers(sort='assigned'), ['Zara P', 'Arun V'])
        self.assertEqual(officers(sort='open'), ['Zara P', 'Arun V'])
        self.assertEqual(officers(category=self.roads.pk), ['Arun V'])
        self.assertEqual(officers(ward=self.ward.pk), ['Zara P'])
        self.assertEqual(officers(q='junior'), ['Arun V'])
        self.assertEqual(officers(sort='bogus'), ['Zara P', 'Arun V'])   # invalid sort falls back
        page = self.client.get(reverse('grievances:officers'), {'view': 'table'})
        self.assertEqual(page.context['view'], 'table')
        self.assertEqual(page.context['average_rate'], 50)
        self.assertEqual(next(o for o in page.context['officers'] if o == busy).rate, 50)
        self.assertEqual(next(o for o in page.context['officers'] if o == idle).rate, 0)

    def test_invalid_status_update_shows_the_form_again(self):
        complaint = self.make_complaint()
        response = self.client.post(complaint.get_absolute_url(), {'status': 'archived'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        complaint.refresh_from_db()
        self.assertEqual(complaint.updates.count(), 0)

    def test_saving_with_no_changes_says_so(self):
        complaint = self.make_complaint(status='assigned')
        response = self.client.post(complaint.get_absolute_url(), {
            'status': 'assigned', 'category': self.water.pk, 'ward': self.ward.pk}, follow=True)
        self.assertContains(response, 'Nothing changed.')
        self.assertEqual(complaint.updates.count(), 0)

    def test_help_desk_unknown_reference(self):
        response = self.client.get(reverse('grievances:help_desk'), {'ref': 'cc-2026-99999'})
        self.assertContains(response, 'No complaint with ID CC-2026-99999 was found.')

    def test_invalid_support_enquiry(self):
        response = self.client.post(reverse('grievances:help_support'),
                                    {'name': 'A', 'email': 'not-an-email', 'topic': 'filing', 'message': 'short'})
        self.assertContains(response, 'Please fix the highlighted fields.')
        self.assertEqual(SupportEnquiry.objects.count(), 0)

    def test_dashboard_three_month_range_and_trend(self):
        self.make_complaint()
        page = self.client.get(reverse('grievances:home'), {'range': '3'})
        self.assertEqual(page.context['range_months'], 3)
        self.assertEqual(len(page.context['stats']['chart']['monthly']['labels']), 3)
        self.assertIsNone(page.context['stats']['reported_change'])  # nothing earlier to compare with
        self.assertEqual(services.percent_change(15, 10), 50.0)
        self.assertEqual(services.percent_change(5, 10), -50.0)


class OfficerAutoAssignmentTests(DashboardTestCase):
    """complaint_detail routes a complaint to the active officer for its category and ward."""

    def setUp(self):
        super().setUp()
        self.ward2 = Ward.objects.get(number=2)
        self.water_ward1 = Officer.objects.create(name='Anitha M', designation='AE', category=self.water, ward=self.ward)
        self.roads_ward1 = Officer.objects.create(name='Kavitha R', designation='AE', category=self.roads, ward=self.ward)

    def review(self, complaint, category, ward, status='submitted', officer=''):
        self.client.post(complaint.get_absolute_url(), {
            'category': category.pk if category else '', 'ward': ward.pk if ward else '',
            'status': status, 'officer': officer.pk if officer else ''})
        complaint.refresh_from_db()
        return complaint

    def unreviewed(self):
        return self.make_complaint(filed_by_citizen=True, category=None, ward=None)

    def test_matching_officer_is_assigned_and_status_moves_to_assigned(self):
        complaint = self.review(self.unreviewed(), self.water, self.ward)
        self.assertEqual((complaint.officer, complaint.status), (self.water_ward1, 'assigned'))
        self.assertIn('Assigned to Anitha M.', complaint.updates.last().note)

    def test_no_officer_for_that_ward_leaves_it_unassigned(self):
        complaint = self.review(self.unreviewed(), self.water, self.ward2)
        self.assertEqual((complaint.officer, complaint.status), (None, 'submitted'))

    def test_inactive_officer_is_skipped(self):
        self.water_ward1.is_active = False
        self.water_ward1.save()
        complaint = self.review(self.unreviewed(), self.water, self.ward)
        self.assertIsNone(complaint.officer)

    def test_later_status_is_kept(self):
        complaint = self.review(self.unreviewed(), self.water, self.ward, status='in_progress')
        self.assertEqual((complaint.officer, complaint.status), (self.water_ward1, 'in_progress'))

    def test_officer_chosen_by_the_admin_is_kept(self):
        complaint = self.review(self.unreviewed(), self.water, self.ward, officer=self.roads_ward1)
        self.assertEqual(complaint.officer, self.roads_ward1)

    def test_needs_both_category_and_ward(self):
        complaint = self.review(self.unreviewed(), self.water, None)
        self.assertIsNone(complaint.officer)

    def test_correcting_the_category_reroutes_to_the_right_officer(self):
        complaint = self.review(self.unreviewed(), self.water, self.ward)
        self.assertEqual(complaint.officer, self.water_ward1)
        # The admin corrects the category; the officer field still shows the old officer.
        complaint = self.review(complaint, self.roads, self.ward, status='assigned', officer=self.water_ward1)
        self.assertEqual(complaint.officer, self.roads_ward1)
        self.assertIn('Assigned to Kavitha R.', complaint.updates.last().note)

    def test_correcting_the_ward_with_no_officer_there_unassigns(self):
        complaint = self.review(self.unreviewed(), self.water, self.ward)
        complaint = self.review(complaint, self.water, self.ward2, status='assigned', officer=self.water_ward1)
        self.assertIsNone(complaint.officer)
        self.assertEqual(complaint.status, 'submitted')
        self.assertIn('Officer removed.', complaint.updates.last().note)


class CsvExportInjectionTests(DashboardTestCase):
    """Cells that a spreadsheet would run as a formula are exported as text (leading apostrophe)."""

    def export_rows(self):
        content = self.client.get(reverse('grievances:complaint_export')).content.decode('utf-8-sig')
        return list(csv.reader(io.StringIO(content)))

    def test_formula_cells_are_neutralised(self):
        payloads = ['=HYPERLINK("http://evil.example","click")', '+1+1', '-2+3', '@SUM(A1)',
                    '\t=1+1', '\r=1+1']
        for payload in payloads:
            self.make_complaint(title=f'Pothole {payload}'[:120], location=payload)
        rows = self.export_rows()
        locations = [row[5] for row in rows[1:]]
        self.assertEqual(sorted(locations), sorted("'" + payload for payload in payloads))
        for row in rows[1:]:
            self.assertFalse(any(cell[:1] in '=+-@\t\r' for cell in row if cell), row)

    def test_ordinary_text_is_unchanged(self):
        self.make_complaint(location='Near 4th Cross, Anna Nagar')
        self.assertEqual(self.export_rows()[1][5], 'Near 4th Cross, Anna Nagar')


class PublicNoteTests(DashboardTestCase):
    """Staff choose per update whether its note is shown on the citizen's tracking page."""

    def test_share_note_flag_is_saved(self):
        complaint = self.make_complaint()
        url = complaint.get_absolute_url()
        self.client.post(url, {'status': 'assigned', 'note': 'Inspection on Monday.', 'share_note': 'on'})
        self.client.post(url, {'status': 'in_progress', 'note': 'Internal: vendor delayed.'})
        self.client.post(url, {'status': 'resolved', 'share_note': 'on'})  # nothing to share without a note
        self.assertEqual(
            [(u.status, u.is_public) for u in complaint.updates.order_by('id')],
            [('assigned', True), ('in_progress', False), ('resolved', False)])
        page = self.client.get(url)
        self.assertContains(page, 'Show this note to the citizen')
        self.assertContains(page, 'Shown to citizen')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='citycorp-test-media-'))
class ComplaintDeleteTests(DashboardTestCase):
    """Admins with the delete permission can permanently remove a complaint, its history and its files."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.complaint = self.make_complaint(
            filed_by_citizen=True, previously_reported=True,
            attachment=SimpleUploadedFile('pothole.png', PNG_BYTES, content_type='image/png'),
            previous_copy=SimpleUploadedFile('earlier.pdf', b'%PDF-1.4', content_type='application/pdf'))
        ComplaintUpdate.objects.create(complaint=self.complaint, status='submitted', note='Registered.')
        self.url = reverse('grievances:complaint_delete', args=[self.complaint.reference])
        self.files = [self.complaint.attachment.path, self.complaint.previous_copy.path]

    def delete(self, client=None, **data):
        with self.captureOnCommitCallbacks(execute=True):
            return (client or self.client).post(self.url, {'confirm': 'yes', **data}, follow=True)

    def test_admin_deletes_complaint_history_and_files(self):
        reference = self.complaint.reference
        with self.assertLogs('grievances.views', 'WARNING') as logs:
            response = self.delete()
        self.assertRedirects(response, reverse('grievances:complaint_list'))
        self.assertContains(response, f'Complaint {reference} was deleted.')
        self.assertFalse(Complaint.objects.filter(reference=reference).exists())
        self.assertFalse(ComplaintUpdate.objects.exists())
        for path in self.files:
            self.assertFalse(os.path.exists(path), path)
        self.assertIn(f'Complaint {reference} was deleted by admin.', logs.output[0])
        self.assertEqual(self.client.get(self.complaint.get_absolute_url()).status_code, 404)

    def test_confirmation_box_is_required(self):
        response = self.delete(confirm='')
        self.assertContains(response, 'I understand this cannot be undone')
        self.assertTrue(Complaint.objects.filter(pk=self.complaint.pk).exists())
        for path in self.files:
            self.assertTrue(os.path.exists(path))

    def test_detail_page_shows_the_delete_form_to_admins(self):
        page = self.client.get(self.complaint.get_absolute_url())
        self.assertContains(page, f'action="{self.url}"')
        self.assertContains(page, 'name="confirm" value="yes" required')

    def test_only_post_is_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertTrue(Complaint.objects.filter(pk=self.complaint.pk).exists())

    def test_unknown_reference_is_404(self):
        response = self.client.post(reverse('grievances:complaint_delete', args=['CC-2026-99999']), {'confirm': 'yes'})
        self.assertEqual(response.status_code, 404)

    def test_staff_without_delete_permission_cannot_delete(self):
        clerk = get_user_model().objects.create_user('clerk', password='Ward-Office-Lamp-2026', is_staff=True)
        self.client.force_login(clerk)
        page = self.client.get(self.complaint.get_absolute_url())
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, self.url)                      # no button
        self.assertEqual(self.client.post(self.url, {'confirm': 'yes'}).status_code, 403)
        self.assertTrue(Complaint.objects.filter(pk=self.complaint.pk).exists())
        # Granted the permission in /admin/, the same clerk can delete.
        clerk.user_permissions.add(Permission.objects.get(codename='delete_complaint'))
        self.client.force_login(get_user_model().objects.get(pk=clerk.pk))
        with self.assertLogs('grievances.views', 'WARNING') as logs:
            self.delete()
        self.assertIn('deleted by clerk', logs.output[0])
        self.assertFalse(Complaint.objects.filter(pk=self.complaint.pk).exists())

    def test_citizens_and_visitors_cannot_delete(self):
        visitor = Client()
        self.assertRedirects(visitor.post(self.url, {'confirm': 'yes'}),
                             f"{reverse('accounts:login')}?next={self.url}", fetch_redirect_response=False)
        session = visitor.session
        session[CITIZEN_SESSION_KEY] = self.complaint.citizen_contact  # even the citizen who filed it
        session.save()
        self.assertEqual(visitor.post(self.url, {'confirm': 'yes'}).status_code, 302)
        self.assertTrue(Complaint.objects.filter(pk=self.complaint.pk).exists())

    def test_deleting_in_django_admin_also_removes_the_files(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('admin:grievances_complaint_delete', args=[self.complaint.pk]), {'post': 'yes'})
        self.assertFalse(Complaint.objects.filter(pk=self.complaint.pk).exists())
        for path in self.files:
            self.assertFalse(os.path.exists(path), path)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='citycorp-test-media-'))
class ComplaintFileTests(TestCase):
    """Uploaded photos and earlier-complaint copies are only for corporation staff."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.complaint = Complaint.objects.create(
            citizen_name='Lakshmi Narayanan', citizen_contact='+919876543210', filed_by_citizen=True,
            title='Pothole near the bus stop', description='A large pothole near the bus stop on the main road.',
            previously_reported=True,
            attachment=SimpleUploadedFile('pothole.png', PNG_BYTES, content_type='image/png'),
            previous_copy=SimpleUploadedFile('earlier.pdf', b'%PDF-1.4', content_type='application/pdf'),
        )
        self.photo_url = reverse('grievances:complaint_file', args=[self.complaint.reference, 'photo'])
        self.copy_url = reverse('grievances:complaint_file', args=[self.complaint.reference, 'previous-copy'])

    def test_staff_can_open_both_files(self):
        sign_in_admin(self.client)
        photo = self.client.get(self.photo_url)
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo['Content-Type'], 'image/png')
        self.assertEqual(b''.join(photo.streaming_content), PNG_BYTES)
        self.assertIn('no-store', photo['Cache-Control'])
        copy = self.client.get(self.copy_url)
        self.assertEqual(copy['Content-Type'], 'application/pdf')
        self.assertEqual(b''.join(copy.streaming_content), b'%PDF-1.4')

    def test_visitors_are_sent_to_login(self):
        for url in (self.photo_url, self.copy_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f"{reverse('accounts:login')}?next={url}", fetch_redirect_response=False)

    def verify_citizen(self, phone):
        session = self.client.session
        session[CITIZEN_SESSION_KEY] = phone
        session.save()

    def test_the_citizen_who_filed_it_can_open_both_files(self):
        self.verify_citizen('+919876543210')
        photo = self.client.get(self.photo_url)
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(b''.join(photo.streaming_content), PNG_BYTES)
        self.assertEqual(self.client.get(self.copy_url).status_code, 200)

    def test_another_citizen_gets_404(self):
        self.verify_citizen('+919123456789')
        for url in (self.photo_url, self.copy_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_signed_in_non_staff_user_is_sent_to_login(self):
        user = get_user_model().objects.create_user('clerk', password='Ward-Office-Lamp-2026')
        self.client.force_login(user)
        response = self.client.get(self.photo_url)
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={self.photo_url}",
                             fetch_redirect_response=False)

    def test_tracking_page_links_the_citizen_to_their_files(self):
        self.verify_citizen('+919876543210')
        response = self.client.get(reverse('grievances:track_detail', args=[self.complaint.reference]))
        self.assertContains(response, f'href="{self.photo_url}"')
        self.assertContains(response, f'href="{self.copy_url}"')
        self.assertNotContains(response, settings.MEDIA_URL + 'complaints/')

    def test_files_are_not_served_from_media_url(self):
        sign_in_admin(self.client)
        self.assertEqual(self.client.get(self.complaint.attachment.url).status_code, 404)

    def test_unknown_kind_or_missing_file_is_404(self):
        sign_in_admin(self.client)
        bad_kind = reverse('grievances:complaint_file', args=[self.complaint.reference, 'citizen_address'])
        self.assertEqual(self.client.get(bad_kind).status_code, 404)
        other = Complaint.objects.create(citizen_name='Ravi K', citizen_contact='+919876500000',
                                         title='Streetlight not working', description='Streetlight has been off for a week.')
        no_photo = reverse('grievances:complaint_file', args=[other.reference, 'photo'])
        self.assertEqual(self.client.get(no_photo).status_code, 404)

    def test_pages_link_to_the_protected_urls(self):
        sign_in_admin(self.client)
        for page in (self.complaint.get_absolute_url(), reverse('grievances:complaint_create')):
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertContains(response, self.photo_url)
                self.assertContains(response, self.copy_url)
                self.assertNotContains(response, settings.MEDIA_URL + 'complaints/')
