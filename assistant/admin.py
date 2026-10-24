from django.contrib import admin

from .models import EmailLog, KnowledgeRecord, ReportRun


@admin.register(KnowledgeRecord)
class KnowledgeRecordAdmin(admin.ModelAdmin):
    list_display = ['dataset', 'title', 'external_id', 'updated_at']
    list_filter = ['dataset']
    search_fields = ['title', 'content', 'external_id']
    readonly_fields = ['fingerprint', 'search_text', 'created_at', 'updated_at']


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'kind', 'status', 'subject', 'record_count', 'recipient']
    list_filter = ['kind', 'status']
    readonly_fields = [field.name for field in EmailLog._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ReportRun)
class ReportRunAdmin(admin.ModelAdmin):
    list_display = ['report_date', 'status', 'complaint_count', 'attempts', 'finished_at']
    list_filter = ['status']
    readonly_fields = ['started_at', 'finished_at', 'attempts', 'complaint_count', 'error']
