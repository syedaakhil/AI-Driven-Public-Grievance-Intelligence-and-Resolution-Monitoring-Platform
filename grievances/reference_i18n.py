"""
English text that lives in the database (seeded by migration 0002_reference_data) but is shown in
the user's language. Listing it here with gettext_noop lets `makemessages` put it in the Hindi
translation file; pages translate it with the `|tr` filter (core/templatetags/ui.py) or gettext().
Add new categories or wards here too if they should appear in Hindi.
"""
from django.utils.translation import gettext_noop

REFERENCE_TEXT = [
    # Categories: name, department, target time, description
    gettext_noop('Water supply'), gettext_noop('Water Supply Department'), gettext_noop('48 hours'),
    gettext_noop('No supply, low pressure, leaking pipelines or contaminated water.'),
    gettext_noop('Roads & potholes'), gettext_noop('Roads & Bridges Department'), gettext_noop('7 days'),
    gettext_noop('Potholes, damaged roads, broken footpaths and faulty streetlights.'),
    gettext_noop('Sewage & drainage'), gettext_noop('Sewerage & Storm Water Department'), gettext_noop('72 hours'),
    gettext_noop('Sewage overflow, blocked drains, open manholes and waterlogging.'),
    gettext_noop('Sanitation & waste'), gettext_noop('Solid Waste Management Department'), gettext_noop('24 hours'),
    gettext_noop('Missed garbage collection, overflowing bins, debris and open burning.'),
    # Ward names and areas
    gettext_noop('Central'), gettext_noop('North'), gettext_noop('East'), gettext_noop('South'),
    gettext_noop('West'), gettext_noop('South-West'), gettext_noop('Lake View'), gettext_noop('IT Corridor'),
    gettext_noop('Egmore'), gettext_noop('Tondiarpet'), gettext_noop('Mylapore'), gettext_noop('Adyar'),
    gettext_noop('Anna Nagar'), gettext_noop('T. Nagar'), gettext_noop('Velachery'), gettext_noop('Perungudi'),
]
