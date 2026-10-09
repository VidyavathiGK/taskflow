from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Admin configuration for the custom User model, displaying avatar_color
    and ensuring custom fields are editable in the admin dashboard.
    """
    list_display = ("username", "email", "first_name", "last_name", "avatar_color", "is_staff")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "first_name", "last_name")
    
    # Extend standard fieldsets with avatar_color
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Presence & UI Settings", {"fields": ("avatar_color",)}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Presence & UI Settings", {"fields": ("avatar_color",)}),
    )
