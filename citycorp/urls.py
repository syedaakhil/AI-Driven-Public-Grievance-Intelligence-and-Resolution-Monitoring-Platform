from django.contrib import admin
from django.urls import include, path

admin.site.site_header = 'City Corporation administration'
admin.site.site_title = 'City Corporation admin'
admin.site.index_title = 'Manage complaints, officers and notices'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('accounts.urls')),
    path('', include('grievances.urls')),
]
# Uploaded files are not served from /media/; see grievances.views.complaint_file.
