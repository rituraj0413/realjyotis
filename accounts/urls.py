from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),              # homepage
    path('signup/', views.signup, name='signup'),   # signup
    path('pandit-signup/', views.pandit_signup, name='pandit_signup'),
    path('login/', views.user_login, name='login'), # login
    path('verify-otp/<int:user_id>/', views.verify_otp, name='verify_otp'),
    path('resend-otp/<int:user_id>/', views.resend_otp, name='resend_otp'),
    path('logout/', views.user_logout, name='logout'), # logout
    path('dashboard/', views.dashboard, name='dashboard'), # dashboard
    path('kundli/', views.kundli, name='kundli'),  # kundli + AI
    path('chatbot/', views.chatbot, name='chatbot'),
    path('chatbot-api/', views.chatbot_api, name='chatbot_api'),
    path('booking/', views.book_pandit, name='book_pandit'),
    path('payment/', views.payment_page, name='payment'),
    path('payment-success/', views.payment_success, name='payment_success'),
    path('wallet/recharge/', views.recharge_wallet, name='recharge_wallet'),
    path('consultation-room/<int:booking_id>/', views.consultation_room, name='consultation_room'),
    path('bookings/<int:booking_id>/verify/', views.verify_booking, name='verify_booking'),
    path('bookings/<int:booking_id>/reject/', views.reject_booking, name='reject_booking'),
    path('pandits/<int:pandit_id>/verify/', views.verify_pandit, name='verify_pandit'),
    path('pandits/<int:pandit_id>/unverify/', views.unverify_pandit, name='unverify_pandit'),
]
