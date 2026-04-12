from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.urls import reverse
from groq import Groq
import razorpay
import random

from .models import Booking, Comment, Pandit, Profile, WalletTransaction


WALLET_PACKAGES = {
    WalletTransaction.PACKAGE_STARTER: {
        "name": "First 10 Minutes",
        "amount": 100,
        "minutes": 10,
        "description": "Minimum recharge to begin a quick pandit consultation.",
    },
    WalletTransaction.PACKAGE_HOURLY: {
        "name": "1 Hour Session",
        "amount": 499,
        "minutes": 60,
        "description": "For one focused session with enough time for detailed guidance.",
    },
    WalletTransaction.PACKAGE_UNLIMITED: {
        "name": "1 Day Unlimited",
        "amount": 1999,
        "minutes": 0,
        "description": "Unlimited access for one day for deeper follow-up and repeat discussion.",
    },
}


def _sync_wallet_balance(profile):
    total = WalletTransaction.objects.filter(
        user=profile.user,
        status=WalletTransaction.STATUS_COMPLETED,
    ).aggregate(total=Sum("amount"))["total"] or 0
    if profile.wallet_balance != total:
        profile.wallet_balance = total
        profile.save(update_fields=["wallet_balance"])
    return total


def _booking_room_name(booking):
    return f"realjyotish-booking-{booking.id}-{booking.user_id}-{booking.pandit_id}"


def _booking_session_minutes(booking):
    if booking.amount <= 100:
        return 10
    if booking.amount <= 499:
        return 60
    if booking.amount >= 1999:
        return 1440
    return 60


def _can_join_booking_room(user, booking):
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    if booking.user_id == user.id:
        return True
    return bool(booking.pandit.user_id and booking.pandit.user_id == user.id)


def _booking_is_paid(booking):
    return booking.status in [
        Booking.STATUS_PAYMENT_SUBMITTED,
        Booking.STATUS_VERIFIED,
        Booking.STATUS_COMPLETED,
    ]


def _start_or_refresh_booking_session(booking):
    duration_minutes = _booking_session_minutes(booking)
    now = timezone.now()

    if not booking.consultation_started_at:
        booking.consultation_started_at = now
        booking.consultation_expires_at = now + timedelta(minutes=duration_minutes)
        booking.save(update_fields=["consultation_started_at", "consultation_expires_at"])

    remaining_seconds = max(0, int((booking.consultation_expires_at - now).total_seconds()))
    return duration_minutes, remaining_seconds


def _format_remaining_time(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _decorate_booking_state(bookings):
    now = timezone.now()
    for booking in bookings:
        booking.can_join_room = _booking_is_paid(booking)
        booking.session_state = ""
        booking.session_time_label = ""

        if not booking.can_join_room:
            booking.session_state = "payment_pending"
            booking.session_time_label = "Complete payment to unlock the room."
            continue

        if not booking.consultation_started_at:
            booking.session_state = "ready"
            booking.session_time_label = f"{_booking_session_minutes(booking)} minutes available when you join."
            continue

        remaining_seconds = max(0, int((booking.consultation_expires_at - now).total_seconds()))
        booking.remaining_seconds = remaining_seconds
        if remaining_seconds <= 0:
            booking.session_state = "expired"
            booking.session_time_label = "Session expired. Recharge or book again to continue."
        else:
            booking.session_state = "active"
            booking.session_time_label = f"Time left: {_format_remaining_time(remaining_seconds)}"

    return bookings


def _generate_email_otp():
    return f"{random.randint(100000, 999999)}"


def _send_signup_otp(user, profile):
    otp = _generate_email_otp()
    profile.email_otp = otp
    profile.otp_created_at = timezone.now()
    profile.email_verified = False
    profile.save(update_fields=["email_otp", "otp_created_at", "email_verified"])

    full_name = profile.full_name or user.username
    email_body = (
        f"Hello {full_name},\n\n"
        f"Your RealJyotish verification OTP is: {otp}\n\n"
        f"This OTP is valid for 10 minutes.\n"
        f"If you did not request this, please ignore this email.\n"
    )

    send_mail(
        "Your RealJyotish OTP",
        email_body,
        getattr(settings, "DEFAULT_FROM_EMAIL", "realjyotish0001@gmail.com"),
        [user.email],
        fail_silently=False,
    )


def _send_payment_receipt(booking):
    user_display = booking.user.username
    email_body = (
        f"Hello {user_display},\n\n"
        f"Your RealJyotish payment has been received successfully.\n\n"
        f"Receipt details:\n"
        f"Pandit: {booking.pandit.name}\n"
        f"Date: {booking.date}\n"
        f"Time: {booking.time}\n"
        f"Amount: Rs. {booking.amount}\n"
        f"Order ID: {booking.razorpay_order_id}\n"
        f"Payment ID: {booking.razorpay_payment_id}\n\n"
        f"Our team will keep the record in admin dashboard and continue with the next verification step.\n"
        f"Thank you for choosing RealJyotish.\n"
    )

    send_mail(
        "RealJyotish Payment Receipt",
        email_body,
        getattr(settings, "DEFAULT_FROM_EMAIL", "realjyotish0001@gmail.com"),
        [booking.user.email],
        fail_silently=False,
    )


def home(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        message = request.POST.get("message", "").strip()

        if not name and request.user.is_authenticated:
            name = request.user.username

        if name and message:
            Comment.objects.create(
                user=request.user if request.user.is_authenticated else None,
                name=name,
                message=message,
            )
            messages.success(request, "Your comment has been added.")
            return redirect("home")

        messages.error(request, "Please enter your name and comment.")

    comments = Comment.objects.filter(is_visible=True).order_by("-created_at")
    return render(request, "home.html", {"comments": comments})


def signup(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password")
        full_name = request.POST.get("full_name", "").strip()
        birth_date = request.POST.get("birth_date") or None
        birth_time = request.POST.get("birth_time") or None
        birth_place = request.POST.get("birth_place", "").strip()

        if User.objects.filter(username=username).exists():
            messages.error(request, "User already exists.")
            return redirect("signup")

        if not email:
            messages.error(request, "Email is required for verification.")
            return redirect("signup")

        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already exists.")
            return redirect("signup")

        user = User.objects.create_user(username=username, password=password, email=email, is_active=False)
        profile, _ = Profile.objects.update_or_create(
            user=user,
            defaults={
                "full_name": full_name,
                "birth_date": birth_date,
                "birth_time": birth_time,
                "birth_place": birth_place,
                "email_verified": False,
            },
        )

        try:
            _send_signup_otp(user, profile)
            if "console.EmailBackend" in settings.EMAIL_BACKEND:
                messages.success(request, "Account created. Development mode is active, so the OTP is printed in the terminal and you can enter it on the next page.")
                return render(request, "verification_sent.html", {
                    "email": email,
                    "user_id": user.id,
                    "console_mode": True,
                })

            messages.success(request, "Account created. Check your email for the OTP before signing in.")
        except Exception as error:
            messages.error(request, "Account created, but the OTP email could not be sent.")
            return render(request, "verification_sent.html", {
                "email": email,
                "user_id": user.id,
                "console_mode": False,
                "error_detail": str(error) if settings.DEBUG else "",
            })

        return redirect("verify_otp", user_id=user.id)

    return render(request, "signup.html")


def pandit_signup(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        expertise = request.POST.get("expertise", "").strip()
        bio = request.POST.get("bio", "").strip()
        experience_years = request.POST.get("experience_years") or 0
        consultation_fee = request.POST.get("consultation_fee") or 500

        if not username or not password or not name or not email or not expertise:
            messages.error(request, "Please fill username, password, name, email, and expertise.")
            return redirect("pandit_signup")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("pandit_signup")

        user = User.objects.create_user(username=username, password=password, email=email)
        Profile.objects.update_or_create(
            user=user,
            defaults={"role": Profile.ROLE_PANDIT, "phone": phone},
        )
        Pandit.objects.create(
            user=user,
            name=name,
            email=email,
            phone=phone,
            expertise=expertise,
            bio=bio,
            experience_years=experience_years,
            consultation_fee=consultation_fee,
            is_verified=False,
        )

        messages.success(request, "Pandit profile submitted. Login now; admin verification is required before users can book you.")
        return redirect("login")

    return render(request, "pandit_signup.html")


def user_login(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password")
        lookup_username = username

        matched_user = User.objects.filter(email__iexact=username).first()
        if matched_user:
            lookup_username = matched_user.username

        user = authenticate(request, username=lookup_username, password=password)

        if user is not None:
            profile, _ = Profile.objects.get_or_create(user=user)
            if not profile.email_verified:
                messages.error(request, "Please verify your email before signing in.")
                return redirect("login")
            login(request, user)
            return redirect("dashboard")

        user_by_name = User.objects.filter(username=username).first() or User.objects.filter(email__iexact=username).first()
        if user_by_name and user_by_name.check_password(password):
            profile, _ = Profile.objects.get_or_create(user=user_by_name)
            if not profile.email_verified or not user_by_name.is_active:
                messages.error(request, "Your email is not verified yet. Please check your inbox.")
                return redirect("login")

        messages.error(request, "Invalid username or password.")

    return render(request, "login.html")


def verify_otp(request, user_id):
    user = User.objects.filter(id=user_id).first()
    if not user:
        messages.error(request, "Account not found.")
        return redirect("signup")

    profile, _ = Profile.objects.get_or_create(user=user)

    if request.method == "POST":
        otp = request.POST.get("otp", "").strip()
        if not otp:
            messages.error(request, "Please enter the OTP.")
            return redirect("verify_otp", user_id=user.id)

        if not profile.email_otp or otp != profile.email_otp:
            messages.error(request, "Invalid OTP.")
            return redirect("verify_otp", user_id=user.id)

        if not profile.otp_created_at or timezone.now() > profile.otp_created_at + timedelta(minutes=10):
            messages.error(request, "OTP expired. Please request a new OTP.")
            return redirect("verify_otp", user_id=user.id)

        user.is_active = True
        user.save(update_fields=["is_active"])
        profile.email_verified = True
        profile.email_otp = ""
        profile.otp_created_at = None
        profile.save(update_fields=["email_verified", "email_otp", "otp_created_at"])
        messages.success(request, "Email verified successfully. You can now sign in.")
        return redirect("login")

    return render(request, "verification_sent.html", {
        "email": user.email,
        "user_id": user.id,
        "console_mode": "console.EmailBackend" in settings.EMAIL_BACKEND,
    })


def resend_otp(request, user_id):
    user = User.objects.filter(id=user_id).first()
    if not user:
        messages.error(request, "Account not found.")
        return redirect("signup")

    profile, _ = Profile.objects.get_or_create(user=user)
    try:
        _send_signup_otp(user, profile)
        messages.success(request, "A new OTP has been sent to your email.")
    except Exception as error:
        messages.error(request, f"OTP could not be sent. {error if settings.DEBUG else ''}")

    return redirect("verify_otp", user_id=user.id)


@login_required
def dashboard(request):
    profile, _ = Profile.objects.get_or_create(
        user=request.user,
        defaults={"role": Profile.ROLE_ADMIN if request.user.is_staff else Profile.ROLE_USER},
    )
    _sync_wallet_balance(profile)

    if request.user.is_staff or profile.role == Profile.ROLE_ADMIN:
        bookings = Booking.objects.select_related("user", "pandit").order_by("-created_at")
        wallet_transactions = WalletTransaction.objects.select_related("user").order_by("-created_at")
        verified_amount = bookings.filter(status__in=[
            Booking.STATUS_VERIFIED,
            Booking.STATUS_COMPLETED,
        ]).aggregate(total=Sum("amount"))["total"] or 0

        return render(request, "admin_dashboard.html", {
            "total_users": User.objects.filter(is_staff=False).count(),
            "total_pandits": Pandit.objects.count(),
            "verified_pandits": Pandit.objects.filter(is_verified=True).count(),
            "total_bookings": bookings.count(),
            "pending_bookings": bookings.filter(status=Booking.STATUS_PAYMENT_SUBMITTED).count(),
            "total_amount": verified_amount,
            "commission": verified_amount * 0.2,
            "bookings": bookings[:12],
            "wallet_transactions": wallet_transactions[:12],
            "wallet_revenue": wallet_transactions.filter(status=WalletTransaction.STATUS_COMPLETED).aggregate(total=Sum("amount"))["total"] or 0,
            "pandits": Pandit.objects.select_related("user").order_by("name"),
        })

    pandit = Pandit.objects.filter(user=request.user).first()
    if profile.role == Profile.ROLE_PANDIT or pandit:
        if not pandit:
            messages.error(request, "Your pandit profile is not linked yet. Ask admin to link your user account.")
            return render(request, "pandit_dashboard.html", {"pandit": None, "bookings": []})

        bookings = Booking.objects.filter(pandit=pandit).select_related("user").order_by("-created_at")
        verified_amount = bookings.filter(status__in=[
            Booking.STATUS_VERIFIED,
            Booking.STATUS_COMPLETED,
        ]).aggregate(total=Sum("amount"))["total"] or 0
        verified_bookings = bookings.filter(status=Booking.STATUS_VERIFIED).count()
        bookings = list(bookings)
        _decorate_booking_state(bookings)

        return render(request, "pandit_dashboard.html", {
            "pandit": pandit,
            "bookings": bookings,
            "verified_bookings": verified_bookings,
            "total_earnings": verified_amount * 0.8,
            "joinable_statuses": [
                Booking.STATUS_PAYMENT_SUBMITTED,
                Booking.STATUS_VERIFIED,
                Booking.STATUS_COMPLETED,
            ],
        })

    bookings = Booking.objects.filter(user=request.user).select_related("pandit").order_by("-created_at")
    bookings = list(bookings)
    _decorate_booking_state(bookings)
    wallet_transactions = WalletTransaction.objects.filter(
        user=request.user,
        status=WalletTransaction.STATUS_COMPLETED,
    ).order_by("-created_at")
    active_package = WalletTransaction.objects.filter(
        user=request.user,
        status=WalletTransaction.STATUS_COMPLETED,
        unlimited_until__gte=timezone.now(),
    ).order_by("-created_at").first() or wallet_transactions.first()
    return render(request, "user_dashboard.html", {
        "profile": profile,
        "bookings": bookings,
        "wallet_packages": WALLET_PACKAGES,
        "wallet_transactions": wallet_transactions[:6],
        "active_package": active_package,
        "joinable_statuses": [
            Booking.STATUS_PAYMENT_SUBMITTED,
            Booking.STATUS_VERIFIED,
            Booking.STATUS_COMPLETED,
        ],
    })


def user_logout(request):
    logout(request)
    return redirect("login")


@login_required
def recharge_wallet(request):
    if request.method != "POST":
        return redirect("dashboard")

    package_code = request.POST.get("package_code", "").strip()
    package = WALLET_PACKAGES.get(package_code)

    if not package:
        messages.error(request, "Please choose a valid recharge package.")
        return redirect("dashboard")

    wallet_transaction = WalletTransaction.objects.create(
        user=request.user,
        package_code=package_code,
        package_name=package["name"],
        amount=package["amount"],
        minutes_included=package["minutes"],
    )
    return redirect(f"{reverse('payment')}?wallet_id={wallet_transaction.id}")


def _groq_client():
    api_key = getattr(settings, "GROQ_API_KEY", "")

    if not api_key:
        raise ValueError("GROQ_API_KEY is missing in Django settings.")

    return Groq(api_key=api_key)


def _groq_chat(messages):
    response = _groq_client().chat.completions.create(
        model=getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages=messages,
        temperature=0.7,
        max_completion_tokens=700,
    )
    return response.choices[0].message.content


def _friendly_groq_error(error):
    detail = str(error)
    if "model" in detail.lower():
        return "Groq model error. Check GROQ_MODEL in settings.py and use a currently supported Groq model."
    if "api" in detail.lower() or "auth" in detail.lower() or "401" in detail:
        return "Groq API key error. Check that GROQ_API_KEY is correct and active."
    return "Groq request failed. Check your internet connection, API key, and Groq account limits."


def kundli(request):
    if request.method == "POST":
        name = request.POST.get("name")
        dob = request.POST.get("dob")
        birth_time = request.POST.get("time")
        place = request.POST.get("place")

        prompt = f"""
Generate a short astrology prediction for:
Name: {name}
DOB: {dob}
Time: {birth_time}
Place: {place}

Include:
- Personality
- Career
- Love life
"""

        try:
            prediction = _groq_chat([
                {
                    "role": "system",
                    "content": "You are an expert astrology assistant. Keep the answer practical and easy to understand.",
                },
                {"role": "user", "content": prompt},
            ])
            return render(request, "result.html", {"name": name, "prediction": prediction})
        except Exception as error:
            return render(
                request,
                "result.html",
                {
                    "name": name,
                    "error": _friendly_groq_error(error),
                    "error_detail": str(error) if settings.DEBUG else "",
                },
            )

    return render(request, "kundli.html")


def chatbot(request):
    return render(request, "chatbot.html")


def chatbot_api(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST requests are allowed."}, status=405)

    user_message = request.POST.get("message", "").strip()
    if not user_message:
        return JsonResponse({"reply": "Please type a question first."})

    try:
        reply = _groq_chat([
            {"role": "system", "content": "You are an expert astrology assistant."},
            {"role": "user", "content": user_message},
        ])
        return JsonResponse({"reply": reply})
    except Exception as error:
        message = _friendly_groq_error(error)
        if settings.DEBUG:
            message = f"{message} Detail: {error}"
        return JsonResponse({"reply": message, "error": True}, status=500)


def _default_pandits():
    defaults = [
        {
            "name": "Pandit Arun Sharma",
            "expertise": "Kundli, marriage matching, and career guidance",
            "email": "pandit.arun@example.com",
        },
        {
            "name": "Pandit Meera Joshi",
            "expertise": "Relationship guidance, muhurat, and spiritual remedies",
            "email": "pandit.meera@example.com",
        },
        {
            "name": "Pandit Dev Mishra",
            "expertise": "Vedic astrology, dosha analysis, and family guidance",
            "email": "pandit.dev@example.com",
        },
    ]

    for pandit in defaults:
        Pandit.objects.get_or_create(
            email=pandit["email"],
            defaults={
                "name": pandit["name"],
                "expertise": pandit["expertise"],
                "bio": "Available for focused astrology consultations and practical remedies.",
                "experience_years": 8,
                "consultation_fee": 500,
                "is_verified": True,
            },
        )


@login_required
def book_pandit(request):
    if not Pandit.objects.exists():
        _default_pandits()

    pandits = Pandit.objects.filter(is_verified=True).order_by("name")
    if not pandits.exists():
        messages.error(request, "No verified pandits are available right now. Please check again later.")
    if request.method == "POST":
        pandit_id = request.POST.get("pandit")
        date = request.POST.get("date")
        time = request.POST.get("time")
        question = request.POST.get("question", "")

        try:
            pandit = Pandit.objects.get(id=pandit_id)
        except Pandit.DoesNotExist:
            messages.error(request, "Please select a valid pandit.")
            return redirect("book_pandit")

        booking = Booking.objects.create(
            user=request.user,
            pandit=pandit,
            date=date,
            time=time,
            amount=pandit.consultation_fee,
            question=question,
        )

        try:
            send_mail(
                "New consultation booking",
                f"Client: {request.user.username}\nDate: {date}\nTime: {time}",
                getattr(settings, "EMAIL_HOST_USER", ""),
                [pandit.email],
                fail_silently=True,
            )
        except Exception:
            pass

        messages.success(request, "Consultation slot selected. Complete payment to confirm.")
        return redirect(f"{reverse('payment')}?booking_id={booking.id}")

    return render(request, "consultation.html", {
        "pandits": pandits,
    })


@login_required
def payment_page(request):
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    booking = None
    wallet_transaction = None
    booking_id = request.GET.get("booking_id")
    wallet_id = request.GET.get("wallet_id")
    amount = 50000
    payment_title = "Confirm your consultation booking."
    payment_copy = "Complete the payment to reserve your session and continue with the next steps."
    payment_items = [
        "Consultation fee is prepared through Razorpay.",
        "Your booking can be confirmed after successful payment.",
        "Keep your questions ready before the session.",
    ]

    if booking_id:
        try:
            booking = Booking.objects.get(id=booking_id, user=request.user)
            amount = booking.amount * 100
        except Booking.DoesNotExist:
            messages.error(request, "Booking was not found.")
            return redirect("book_pandit")
    elif wallet_id:
        try:
            wallet_transaction = WalletTransaction.objects.get(id=wallet_id, user=request.user)
            amount = wallet_transaction.amount * 100
            payment_title = f"Recharge {wallet_transaction.package_name}."
            payment_copy = "Complete the payment to activate your package and credit your wallet automatically."
            payment_items = [
                f"Package: {wallet_transaction.package_name}",
                "Wallet balance updates only after successful Razorpay payment.",
                "Recharge history will appear in your dashboard automatically.",
            ]
        except WalletTransaction.DoesNotExist:
            messages.error(request, "Wallet recharge request was not found.")
            return redirect("dashboard")

    try:
        payment = client.order.create({
            "amount": amount,
            "currency": "INR",
            "payment_capture": 1,
        })
        if booking:
            booking.razorpay_order_id = payment.get("id", "")
            booking.status = Booking.STATUS_PAYMENT_SUBMITTED
            booking.save(update_fields=["razorpay_order_id", "status"])
        if wallet_transaction:
            wallet_transaction.razorpay_order_id = payment.get("id", "")
            wallet_transaction.save(update_fields=["razorpay_order_id"])
        payment_error = ""
    except Exception as error:
        payment = {"amount": amount, "id": ""}
        payment_error = f"Payment setup failed. Check Razorpay keys in settings.py. Detail: {error}"

    return render(request, "payment.html", {
        "booking": booking,
        "wallet_transaction": wallet_transaction,
        "payment": payment,
        "payment_error": payment_error,
        "payment_title": payment_title,
        "payment_copy": payment_copy,
        "payment_items": payment_items,
        "razorpay_key": settings.RAZORPAY_KEY_ID,
    })


@login_required
def payment_success(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST requests are allowed."}, status=405)

    booking_id = request.POST.get("booking_id")
    wallet_id = request.POST.get("wallet_id")
    payment_id = request.POST.get("razorpay_payment_id", "").strip()
    order_id = request.POST.get("razorpay_order_id", "").strip()

    if wallet_id:
        try:
            wallet_transaction = WalletTransaction.objects.get(id=wallet_id, user=request.user)
        except WalletTransaction.DoesNotExist:
            return JsonResponse({"error": "Wallet recharge not found."}, status=404)

        if wallet_transaction.status == WalletTransaction.STATUS_COMPLETED:
            return JsonResponse({
                "ok": True,
                "receipt_status": "already_completed",
                "redirect_url": reverse("dashboard"),
            })

        profile, _ = Profile.objects.get_or_create(user=request.user)
        wallet_transaction.razorpay_payment_id = payment_id
        if order_id:
            wallet_transaction.razorpay_order_id = order_id
        wallet_transaction.status = WalletTransaction.STATUS_COMPLETED
        wallet_transaction.payment_completed_at = timezone.now()
        if wallet_transaction.package_code == WalletTransaction.PACKAGE_UNLIMITED:
            wallet_transaction.unlimited_until = timezone.now() + timedelta(days=1)
        wallet_transaction.save(update_fields=[
            "razorpay_payment_id",
            "razorpay_order_id",
            "status",
            "payment_completed_at",
            "unlimited_until",
        ])
        profile.wallet_balance += wallet_transaction.amount
        profile.save(update_fields=["wallet_balance"])

        return JsonResponse({
            "ok": True,
            "receipt_status": "wallet_credited",
            "redirect_url": reverse("dashboard"),
        })

    try:
        booking = Booking.objects.select_related("user", "pandit").get(id=booking_id, user=request.user)
    except Booking.DoesNotExist:
        return JsonResponse({"error": "Booking not found."}, status=404)

    booking.razorpay_payment_id = payment_id
    if order_id:
        booking.razorpay_order_id = order_id
    booking.status = Booking.STATUS_PAYMENT_SUBMITTED
    booking.payment_completed_at = timezone.now()
    booking.admin_note = "Payment completed by user and receipt sent."
    booking.save(update_fields=[
        "razorpay_payment_id",
        "razorpay_order_id",
        "status",
        "payment_completed_at",
        "admin_note",
    ])

    receipt_status = "sent"
    try:
        if booking.user.email:
            _send_payment_receipt(booking)
            booking.receipt_sent_at = timezone.now()
            booking.save(update_fields=["receipt_sent_at"])
        else:
            receipt_status = "user_has_no_email"
    except Exception:
        receipt_status = "failed"

    return JsonResponse({
        "ok": True,
        "receipt_status": receipt_status,
        "redirect_url": reverse("dashboard"),
    })


@login_required
def consultation_room(request, booking_id):
    try:
        booking = Booking.objects.select_related("user", "pandit", "pandit__user").get(id=booking_id)
    except Booking.DoesNotExist:
        messages.error(request, "Consultation booking not found.")
        return redirect("dashboard")

    if not _can_join_booking_room(request.user, booking):
        messages.error(request, "You are not allowed to join this consultation room.")
        return redirect("dashboard")

    if not _booking_is_paid(booking):
        messages.error(request, "Complete payment first to unlock the consultation room.")
        return redirect("dashboard")

    room_name = _booking_room_name(booking)
    duration_minutes, remaining_seconds = _start_or_refresh_booking_session(booking)
    if remaining_seconds <= 0:
        messages.error(request, "This consultation session has ended. Recharge or book again to continue.")
        return redirect("dashboard")
    display_name = booking.user.username if request.user == booking.user else request.user.username

    return render(request, "consultation_room.html", {
        "booking": booking,
        "room_name": room_name,
        "jitsi_room_url": f"https://meet.jit.si/{room_name}",
        "duration_minutes": duration_minutes,
        "display_name": display_name,
        "remaining_seconds": remaining_seconds,
    })


@login_required
def verify_booking(request, booking_id):
    if not request.user.is_staff:
        messages.error(request, "Only admin can verify payments.")
        return redirect("dashboard")

    booking = Booking.objects.get(id=booking_id)
    booking.status = Booking.STATUS_VERIFIED
    booking.verified_at = timezone.now()
    booking.admin_note = "Payment verified by admin."
    booking.save(update_fields=["status", "verified_at", "admin_note"])
    messages.success(request, "Booking payment verified.")
    return redirect("dashboard")


@login_required
def reject_booking(request, booking_id):
    if not request.user.is_staff:
        messages.error(request, "Only admin can reject payments.")
        return redirect("dashboard")

    booking = Booking.objects.get(id=booking_id)
    booking.status = Booking.STATUS_REJECTED
    booking.admin_note = "Payment rejected by admin."
    booking.save(update_fields=["status", "admin_note"])
    messages.success(request, "Booking payment rejected.")
    return redirect("dashboard")


@login_required
def verify_pandit(request, pandit_id):
    if not request.user.is_staff:
        messages.error(request, "Only admin can verify pandits.")
        return redirect("dashboard")

    pandit = Pandit.objects.get(id=pandit_id)
    pandit.is_verified = True
    pandit.save(update_fields=["is_verified"])
    if pandit.user:
        Profile.objects.update_or_create(
            user=pandit.user,
            defaults={"role": Profile.ROLE_PANDIT, "phone": pandit.phone},
        )
    messages.success(request, f"{pandit.name} is now verified.")
    return redirect("dashboard")


@login_required
def unverify_pandit(request, pandit_id):
    if not request.user.is_staff:
        messages.error(request, "Only admin can unverify pandits.")
        return redirect("dashboard")

    pandit = Pandit.objects.get(id=pandit_id)
    pandit.is_verified = False
    pandit.save(update_fields=["is_verified"])
    messages.success(request, f"{pandit.name} is now hidden from booking.")
    return redirect("dashboard")
