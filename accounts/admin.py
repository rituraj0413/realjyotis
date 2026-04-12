from django.contrib import admin

# Register your models here.
from .models import Booking, Comment, Pandit, Profile, Review, WalletTransaction

admin.site.register(Profile)
admin.site.register(Pandit)
admin.site.register(Booking)
admin.site.register(Review)
admin.site.register(Comment)
admin.site.register(WalletTransaction)
