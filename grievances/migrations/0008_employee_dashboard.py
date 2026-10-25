"""
Step 3 of 3: Category.department and the employee columns become required, the old text and
category/is_active columns go, and the work-assignment and employee-report tables are added.
"""
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('grievances', '0007_fill_departments_and_employee_ids'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveField(model_name='category', name='department'),
        migrations.RenameField(model_name='category', old_name='department_ref', new_name='department'),
        migrations.AlterField(
            model_name='category',
            name='department',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='categories',
                                    to='grievances.department'),
        ),
        migrations.RemoveField(model_name='officer', name='category'),
        migrations.RemoveField(model_name='officer', name='is_active'),
        migrations.AlterField(
            model_name='officer',
            name='employee_id',
            field=models.CharField(editable=False, max_length=13, unique=True),
        ),
        migrations.AlterField(
            model_name='officer',
            name='department',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employees',
                                    to='grievances.department'),
        ),
        migrations.AlterModelOptions(
            name='officer',
            options={'ordering': ['name'], 'verbose_name': 'employee'},
        ),
        migrations.CreateModel(
            name='EmployeeReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(choices=[
                    ('absent', 'Absent from duty'), ('delay', 'Delayed work'),
                    ('misconduct', 'Rude or improper behaviour'), ('corruption', 'Bribery or corruption'),
                    ('quality', 'Poor quality of work'), ('other', 'Something else')], max_length=20)),
                ('details', models.TextField()),
                ('status', models.CharField(choices=[('open', 'Open'), ('reviewed', 'Reviewed'), ('closed', 'Closed')],
                                            db_index=True, default='open', max_length=10)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reports',
                                               to='grievances.officer')),
                ('reported_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                  related_name='employee_reports', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='WorkAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True)),
                ('priority', models.CharField(choices=[('high', 'High'), ('medium', 'Medium'), ('low', 'Low')],
                                              default='medium', max_length=10)),
                ('due_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[('assigned', 'Assigned'), ('in_progress', 'In progress'),
                                                     ('done', 'Done')], db_index=True, default='assigned',
                                            max_length=20)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('assigned_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                  related_name='work_assigned', to=settings.AUTH_USER_MODEL)),
                ('complaint', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                related_name='assignments', to='grievances.complaint')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assignments',
                                               to='grievances.officer')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
