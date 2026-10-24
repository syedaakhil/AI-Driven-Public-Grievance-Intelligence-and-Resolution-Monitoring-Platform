from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('login/admin/', views.admin_login, name='admin_login'),
    path('login/otp/send/', views.send_otp, name='send_otp'),
    path('login/otp/verify/', views.verify_otp, name='verify_otp'),
    path('login/otp/reset/', views.reset_otp, name='reset_otp'),
    path('logout/', views.logout_view, name='logout'),
]
