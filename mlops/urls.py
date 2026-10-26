from django.urls import path

from . import views

app_name = 'mlops'

urlpatterns = [
    path('agent-work/', views.agent_work, name='agent_work'),
    path('agent-work/news/<int:pk>/review/', views.news_review, name='news_review'),
    path('api/agent-work/collect/', views.api_collect, name='api_collect'),
    path('api/agent-work/jobs/<int:pk>/', views.api_job, name='api_job'),
]
