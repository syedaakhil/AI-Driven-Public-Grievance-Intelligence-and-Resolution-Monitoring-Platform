from django.contrib import admin

from .models import (Category, Complaint, ComplaintUpdate, Department, EmployeeReport, Notice, Officer,
                     SupportEnquiry, Ward, WorkAssignment)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'department', 'target_time', 'order']
    prepopulated_fields = {'slug': ['name']}


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'area']


@admin.register(Officer)
class OfficerAdmin(admin.ModelAdmin):
    list_display = ['employee_id', 'name', 'designation', 'department', 'ward', 'status', 'date_joined']
    list_filter = ['status', 'department', 'ward']
    search_fields = ['employee_id', 'name', 'designation', 'email']
    readonly_fields = ['employee_id']

    def has_delete_permission(self, request, obj=None):
        return False   # employees are deactivated (status "inactive"), never deleted, to keep history


@admin.register(EmployeeReport)
class EmployeeReportAdmin(admin.ModelAdmin):
    list_display = ['employee', 'reason', 'status', 'reported_by', 'created_at']
    list_filter = ['status', 'reason']
    autocomplete_fields = ['employee']


@admin.register(WorkAssignment)
class WorkAssignmentAdmin(admin.ModelAdmin):
    list_display = ['title', 'employee', 'complaint', 'priority', 'due_date', 'status', 'created_at']
    list_filter = ['status', 'priority']
    search_fields = ['title', 'employee__name', 'employee__employee_id', 'complaint__reference']
    autocomplete_fields = ['employee', 'complaint']
    readonly_fields = ['completed_at']


class ComplaintUpdateInline(admin.TabularInline):
    model = ComplaintUpdate
    extra = 0
    fields = ['created_at', 'status', 'note', 'is_public', 'author']
    readonly_fields = ['created_at', 'author']


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = ['reference', 'title', 'category', 'ward', 'channel', 'priority', 'status', 'created_at']
    list_filter = ['status', 'priority', 'channel', 'category', 'ward']
    search_fields = ['reference', 'title', 'citizen_name', 'citizen_contact']
    readonly_fields = ['reference', 'created_at', 'updated_at', 'resolved_at']
    autocomplete_fields = ['officer']
    inlines = [ComplaintUpdateInline]


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ['title', 'topic', 'published_on', 'is_published']
    list_filter = ['is_published']
    search_fields = ['title', 'summary']


@admin.register(SupportEnquiry)
class SupportEnquiryAdmin(admin.ModelAdmin):
    list_display = ['reference', 'name', 'email', 'topic', 'created_at']
    list_filter = ['topic']
    readonly_fields = ['reference', 'created_at']
