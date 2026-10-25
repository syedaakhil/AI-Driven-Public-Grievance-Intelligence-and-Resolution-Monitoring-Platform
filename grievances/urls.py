from django.urls import path

from . import views

app_name = 'grievances'

urlpatterns = [
    path('', views.home, name='home'),
    path('complaints/', views.complaint_list, name='complaint_list'),
    path('complaints/new/', views.complaint_create, name='complaint_create'),
    path('complaints/export.csv', views.complaint_export, name='complaint_export'),
    path('complaints/<str:reference>/', views.complaint_detail, name='complaint_detail'),
    path('complaints/<str:reference>/delete/', views.complaint_delete, name='complaint_delete'),
    path('complaints/<str:reference>/files/<str:kind>/', views.complaint_file, name='complaint_file'),
    path('officers/', views.officers, name='officers'),
    path('officers/tasks/<int:pk>/status/', views.assignment_status, name='assignment_status'),
    path('officers/<str:employee_id>/', views.officer_detail, name='officer_detail'),
    path('officers/<str:employee_id>/edit/', views.officer_edit, name='officer_edit'),
    path('officers/<str:employee_id>/deactivate/', views.officer_deactivate, name='officer_deactivate'),
    path('officers/<str:employee_id>/id-card/', views.officer_id_card, name='officer_id_card'),
    path('officers/<str:employee_id>/photo/', views.officer_photo, name='officer_photo'),
    path('officers/<str:employee_id>/report/', views.officer_report, name='officer_report'),
    path('officers/<str:employee_id>/assign/', views.officer_assign, name='officer_assign'),
    path('help-desk/', views.help_desk, name='help_desk'),
    path('help-support/', views.help_support, name='help_support'),
    path('complaint-registration/', views.complaint_registration, name='complaint_registration'),
    path('citizen/complaint/', views.citizen_complaint_page, name='citizen_complaint_page'),
    path('track/', views.track_list, name='track_list'),
    path('track/<str:reference>/', views.track_detail, name='track_detail'),
    path('api/complaints/', views.api_complaints, name='api_complaints'),
]
