from django.contrib import admin

from .models import CollectionJob, Dataset, DatasetRecord, TrainingRun


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ['slug', 'name', 'record_count', 'label_count', 'last_collected_at']
    readonly_fields = ['record_count', 'label_count', 'last_collected_at', 'created_at']


@admin.register(CollectionJob)
class CollectionJobAdmin(admin.ModelAdmin):
    list_display = ['pk', 'dataset', 'status', 'records_processed', 'records_new', 'attempts', 'created_at', 'finished_at']
    list_filter = ['status', 'dataset']
    readonly_fields = [field.name for field in CollectionJob._meta.fields]

    def has_add_permission(self, request):
        return False   # jobs start from the Agent Work page or "manage.py run_collection_jobs"


@admin.register(DatasetRecord)
class DatasetRecordAdmin(admin.ModelAdmin):
    list_display = ['dataset', 'source_id', 'label', 'collected_at']
    list_filter = ['dataset', 'label']
    search_fields = ['source_id', 'text']
    readonly_fields = ['job', 'collected_at']


@admin.register(TrainingRun)
class TrainingRunAdmin(admin.ModelAdmin):
    list_display = ['pk', 'dataset', 'status', 'record_count', 'started_at', 'finished_at']
    list_filter = ['status', 'dataset']
    readonly_fields = [field.name for field in TrainingRun._meta.fields]

    def has_add_permission(self, request):
        return False   # training runs only through "manage.py train_model"
