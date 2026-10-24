from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

admin.site.site_header = 'City Corporation administration'
admin.site.site_title = 'City Corporation admin'
admin.site.index_title = 'Manage complaints, officers and notices'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('accounts.urls')),
    path('', include('grievances.urls')),
    path('', include('assistant.urls')),
    # Language switch (POST language=en|hi) and the JavaScript translation catalogue
    path('i18n/', include('django.conf.urls.i18n')),
    path('jsi18n/', JavaScriptCatalog.as_view(), name='javascript-catalog'),
]
# Uploaded files are not served from /media/; see grievances.views.complaint_file.
