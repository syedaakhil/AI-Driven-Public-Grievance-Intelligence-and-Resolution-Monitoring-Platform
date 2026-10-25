"""
Step 2 of 3: one Department per distinct Category.department text, and for every existing officer
their department (from their category), status (from is_active) and a new random employee ID.
Complaint.officer is not touched, so every complaint keeps its officer.
"""
import secrets

from django.db import migrations


def new_ids(taken):
    while True:
        candidate = f'CC-EMP-{secrets.randbelow(10 ** 6):06d}'
        if candidate not in taken:
            taken.add(candidate)
            yield candidate


def forwards(apps, schema_editor):
    Department = apps.get_model('grievances', 'Department')
    Category = apps.get_model('grievances', 'Category')
    Officer = apps.get_model('grievances', 'Officer')

    for category in Category.objects.all():
        category.department_ref, _ = Department.objects.get_or_create(name=category.department.strip())
        category.save(update_fields=['department_ref'])

    ids = new_ids(set(Officer.objects.exclude(employee_id=None).values_list('employee_id', flat=True)))
    for officer in Officer.objects.select_related('category'):
        officer.department_id = officer.category.department_ref_id
        officer.status = 'active' if officer.is_active else 'inactive'
        if not officer.employee_id:
            officer.employee_id = next(ids)
        officer.save(update_fields=['department', 'status', 'employee_id'])


def backwards(apps, schema_editor):
    Category = apps.get_model('grievances', 'Category')
    Officer = apps.get_model('grievances', 'Officer')
    for category in Category.objects.select_related('department_ref'):
        category.department = category.department_ref.name
        category.save(update_fields=['department'])
    for officer in Officer.objects.all():
        officer.is_active = officer.status != 'inactive'
        first = Category.objects.filter(department_ref_id=officer.department_id).order_by('order', 'name').first()
        if first is not None:
            officer.category = first
        officer.save(update_fields=['is_active', 'category'])


class Migration(migrations.Migration):

    dependencies = [
        ('grievances', '0006_department_employee_fields'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
