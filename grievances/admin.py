from django.contrib import admin

from .models import Category, Complaint, ComplaintUpdate, Notice, Officer, SupportEnquiry, Ward


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'department', 'target_time', 'order']
    prepopulated_fields = {'slug': ['name']}


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'area']


@admin.register(Officer)
class OfficerAdmin(admin.ModelAdmin):
    list_display = ['name', 'designation', 'category', 'ward', 'email', 'is_active']
    list_filter = ['category', 'ward', 'is_active']
    search_fields = ['name', 'designation', 'email']


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
