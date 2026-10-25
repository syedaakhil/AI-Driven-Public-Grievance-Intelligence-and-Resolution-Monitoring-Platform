"""
Step 1 of 3 for the employee dashboard: add Department and the new employee columns, all nullable.
0007 fills them in from the existing data, and 0008 makes them required and drops the old columns.
"""
import django.db.models.deletion
import django.core.validators
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('grievances', '0005_complaintupdate_is_public'),
    ]

    operations = [
        # The old columns become optional first, so that undoing 0008 can add them back empty and
        # 0007's backwards step can fill them before this migration makes them required again.
        migrations.AlterField(
            model_name='category',
            name='department',
            field=models.CharField(default='', max_length=100),
        ),
        migrations.AlterField(
            model_name='officer',
            name='category',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='officers', to='grievances.category'),
        ),
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddField(
            model_name='category',
            name='department_ref',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='categories', to='grievances.department'),
        ),
        migrations.AddField(
            model_name='officer',
            name='employee_id',
            field=models.CharField(editable=False, max_length=13, null=True),
        ),
        migrations.AddField(
            model_name='officer',
            name='department',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='employees', to='grievances.department'),
        ),
        migrations.AddField(
            model_name='officer',
            name='photo',
            field=models.FileField(blank=True, upload_to='employees/%Y/', validators=[
                django.core.validators.FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])]),
        ),
        migrations.AddField(
            model_name='officer',
            name='date_joined',
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
        migrations.AddField(
            model_name='officer',
            name='status',
            field=models.CharField(choices=[('active', 'Active'), ('on_leave', 'On leave'), ('inactive', 'Inactive')],
                                   db_index=True, default='active', max_length=10),
        ),
    ]
