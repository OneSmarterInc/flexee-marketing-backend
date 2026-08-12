from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

admin.site.register(
    User,
    type("_UA", (UserAdmin,), {
        "fieldsets": UserAdmin.fieldsets + (("Flexee", {"fields": ("role",)}),),
        "list_display": ("username", "email", "role", "is_staff"),
    }),
)
