from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import AuditEvent, AuditRun, Client, Membership, Project, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Studio access",
            {
                "fields": (
                    "role",
                    "must_change_password",
                    "temporary_password_expires_at",
                    "password_changed_at",
                )
            },
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Studio access", {"fields": ("role", "must_change_password")}),
    )
    list_display = ("username", "email", "role", "is_staff", "is_active", "must_change_password")
    list_filter = ("role", "is_staff", "is_active", "must_change_password")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "retention_days", "archived_at")
    search_fields = ("name", "slug")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "primary_domain", "business_type", "status")
    list_filter = ("business_type", "status", "locale")
    search_fields = ("name", "primary_domain", "client__name")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "client", "project", "access_role", "is_active")
    list_filter = ("access_role", "is_active")


@admin.register(AuditRun)
class AuditRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "project",
        "profile",
        "state",
        "evidence_coverage",
        "health_score",
        "created_at",
    )
    list_filter = ("profile", "state")
    readonly_fields = ("id", "created_at", "updated_at", "version")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "actor", "project", "request_id")
    list_filter = ("event_type",)
    search_fields = ("event_type", "request_id", "object_id")
    readonly_fields = tuple(field.name for field in AuditEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
