from django.urls import path

from . import views

app_name = 'assistant'

urlpatterns = [
    path('api/assistant/chat/', views.chat_message, name='chat'),
    path('api/ingest/', views.ingest_data, name='ingest'),
]
