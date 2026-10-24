"""Service categories and wards the portal works with.

These are reference lists, not sample complaints: complaints, officers and notices start empty
and are added through the portal and /admin/.
"""
from decimal import Decimal

from django.db import migrations

CATEGORIES = [
    ('water', 'Water supply', 'Water Supply Department', '48 hours',
     'No supply, low pressure, leaking pipelines or contaminated water.', 'droplet'),
    ('roads', 'Roads & potholes', 'Roads & Bridges Department', '7 days',
     'Potholes, damaged roads, broken footpaths and faulty streetlights.', 'road'),
    ('sewage', 'Sewage & drainage', 'Sewerage & Storm Water Department', '72 hours',
     'Sewage overflow, blocked drains, open manholes and waterlogging.', 'waves'),
    ('sanitation', 'Sanitation & waste', 'Solid Waste Management Department', '24 hours',
     'Missed garbage collection, overflowing bins, debris and open burning.', 'trash'),
]

# Chennai, Tamil Nadu
WARDS = [
    (1, 'Central', 'Egmore', '13.07320', '80.26090'),
    (2, 'North', 'Tondiarpet', '13.12600', '80.28800'),
    (3, 'East', 'Mylapore', '13.03680', '80.26760'),
    (4, 'South', 'Adyar', '13.00120', '80.25650'),
    (5, 'West', 'Anna Nagar', '13.08500', '80.21010'),
    (6, 'South-West', 'T. Nagar', '13.04180', '80.23410'),
    (7, 'Lake View', 'Velachery', '12.98150', '80.21800'),
    (8, 'IT Corridor', 'Perungudi', '12.96540', '80.24610'),
]


def add_reference_data(apps, schema_editor):
    Category = apps.get_model('grievances', 'Category')
    Ward = apps.get_model('grievances', 'Ward')
    for order, (slug, name, department, target, description, icon) in enumerate(CATEGORIES, start=1):
        Category.objects.get_or_create(slug=slug, defaults={
            'name': name, 'department': department, 'target_time': target,
            'description': description, 'icon': icon, 'order': order,
        })
    for number, name, area, lat, lng in WARDS:
        Ward.objects.get_or_create(number=number, defaults={
            'name': name, 'area': area, 'latitude': Decimal(lat), 'longitude': Decimal(lng),
        })


class Migration(migrations.Migration):

    dependencies = [
        ('grievances', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(add_reference_data, migrations.RunPython.noop),
    ]
