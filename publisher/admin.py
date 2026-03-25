from django.contrib import admin
from .models import PublishLog, TrackedScript, UserPreference, NotificationLog


@admin.register(PublishLog)
class PublishLogAdmin(admin.ModelAdmin):
    list_display    = ("username", "source_file", "target_wiki", "target_page", "publish_method", "status", "created_at")
    list_filter     = ("status", "publish_method", "target_wiki")
    search_fields   = ("username", "source_file", "target_page")
    readonly_fields = ("created_at",)
    ordering        = ("-created_at",)


@admin.register(TrackedScript)
class TrackedScriptAdmin(admin.ModelAdmin):
    list_display    = ("username", "source_file", "target_wiki", "target_page", "has_update", "last_checked_at", "is_active")
    list_filter     = ("has_update", "is_active", "target_wiki")
    search_fields   = ("username", "source_file", "target_page")
    readonly_fields = ("created_at", "last_checked_at")


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display  = ("username", "opted_out", "opted_in_at", "opted_out_at")
    list_filter   = ("opted_out",)
    search_fields = ("username",)


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display    = ("notified_username", "notified_wiki", "talk_page", "success", "sent_at")
    list_filter     = ("success", "notified_wiki")
    search_fields   = ("notified_username", "talk_page")
    readonly_fields = ("sent_at",)
