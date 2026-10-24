"""Text messages to citizens about their complaints."""
import logging

from django.utils import translation

from accounts import sms
from accounts.otp import normalize_phone

logger = logging.getLogger(__name__)


def status_message(complaint):
    # Always English, whatever language the admin uses the site in (SMS templates are fixed English text).
    with translation.override('en'):
        return f'Your complaint {complaint.reference} is now {complaint.get_status_display()}. - City Corporation'


def status_sms_recipient(complaint):
    """The +91 mobile number to text, or None for staff-entered complaints and email/invalid contacts."""
    if not complaint.filed_by_citizen:
        return None
    return normalize_phone(complaint.citizen_contact)


def notify_status_change(complaint):
    """
    Text the citizen the complaint's new status. Returns True if the SMS was sent, False if there was
    no one to text or sending failed. Never raises: a failed SMS is logged and must not undo the
    admin's update, which is already saved.
    """
    phone = status_sms_recipient(complaint)
    if not phone:
        return False
    try:
        sms.send_message(phone, status_message(complaint))
    except Exception:
        # Reference only: the citizen's number stays out of the logs.
        logger.exception('Status SMS for complaint %s could not be sent.', complaint.reference)
        return False
    return True
