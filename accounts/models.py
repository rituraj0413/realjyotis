from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Profile(models.Model):
    ROLE_USER = "user"
    ROLE_PANDIT = "pandit"
    ROLE_ADMIN = "admin"

    ROLE_CHOICES = [
        (ROLE_USER, "User"),
        (ROLE_PANDIT, "Pandit"),
        (ROLE_ADMIN, "Admin"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_USER)
    full_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    birth_interest = models.CharField(max_length=200, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    birth_time = models.TimeField(null=True, blank=True)
    birth_place = models.CharField(max_length=200, blank=True)
    wallet_balance = models.PositiveIntegerField(default=0)
    email_verified = models.BooleanField(default=False)
    email_otp = models.CharField(max_length=6, blank=True)
    otp_created_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


class Pandit(models.Model):
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=100)
    expertise = models.CharField(max_length=200)
    email = models.EmailField()   # 👈 IMPORTANT
    phone = models.CharField(max_length=20, blank=True)
    bio = models.TextField(blank=True)
    experience_years = models.PositiveIntegerField(default=0)
    consultation_fee = models.PositiveIntegerField(default=500)
    is_verified = models.BooleanField(default=False)

    def __str__(self):
        return self.name
    

class Booking(models.Model):
    STATUS_PENDING_PAYMENT = "pending_payment"
    STATUS_PAYMENT_SUBMITTED = "payment_submitted"
    STATUS_VERIFIED = "verified"
    STATUS_REJECTED = "rejected"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = [
        (STATUS_PENDING_PAYMENT, "Pending Payment"),
        (STATUS_PAYMENT_SUBMITTED, "Payment Submitted"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_COMPLETED, "Completed"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    pandit = models.ForeignKey(Pandit, on_delete=models.CASCADE)
    date = models.DateField()
    time = models.TimeField()
    amount = models.IntegerField()
    question = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING_PAYMENT)
    razorpay_order_id = models.CharField(max_length=120, blank=True)
    razorpay_payment_id = models.CharField(max_length=120, blank=True)
    admin_note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    payment_completed_at = models.DateTimeField(null=True, blank=True)
    receipt_sent_at = models.DateTimeField(null=True, blank=True)
    consultation_started_at = models.DateTimeField(null=True, blank=True)
    consultation_expires_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} with {self.pandit.name} on {self.date}"


class WalletTransaction(models.Model):
    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"

    PACKAGE_STARTER = "starter_10"
    PACKAGE_HOURLY = "hourly_60"
    PACKAGE_UNLIMITED = "unlimited_day"

    PACKAGE_CHOICES = [
        (PACKAGE_STARTER, "First 10 Minutes"),
        (PACKAGE_HOURLY, "1 Hour"),
        (PACKAGE_UNLIMITED, "1 Day Unlimited"),
    ]

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    package_code = models.CharField(max_length=30, choices=PACKAGE_CHOICES)
    package_name = models.CharField(max_length=100)
    amount = models.PositiveIntegerField()
    minutes_included = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    razorpay_order_id = models.CharField(max_length=120, blank=True)
    razorpay_payment_id = models.CharField(max_length=120, blank=True)
    unlimited_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    payment_completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.package_name} ({self.amount})"


class Review(models.Model):
    name = models.CharField(max_length=100)
    message = models.TextField()
    video = models.FileField(upload_to='videos/')

    def __str__(self):
        return self.name


class Comment(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=100)
    message = models.TextField()
    is_visible = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.name}: {self.message[:40]}"
