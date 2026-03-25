from django.urls import path
from . import views

urlpatterns = [
    # ── Public ───────────────────────────────────────────────────────────────
    path("", views.home, name="home"),
    path("get-started/", views.get_started, name="get_started"),

    # ── OAuth 2.0 ─────────────────────────────────────────────────────────────
    path("oauth/start/", views.oauth_redirect, name="oauth_redirect"),
    path("api/auth/callback/", views.oauth_callback, name="oauth_callback"),
    path("logout/", views.logout_view, name="logout"),

    # ── Dashboard ──────────────────────────────────────────────────────────────
    path("dashboard/", views.dashboard_home, name="dashboard"),
    path("dashboard/upload/", views.upload_files, name="upload_files"),
    path("dashboard/mapping/", views.mapping_config, name="mapping_config"),
    path("dashboard/publish/", views.publish, name="publish"),
    path("dashboard/log/", views.publish_log, name="publish_log"),
    path("dashboard/settings/", views.settings_view, name="settings"),
    path("dashboard/notifications/", views.notifications, name="notifications"),

    # ── API ────────────────────────────────────────────────────────────────────
    path("api/fetch-repo/", views.api_fetch_repo, name="api_fetch_repo"),
    path("api/page-content/", views.api_page_content, name="api_page_content"),
    path("api/publish/", views.api_publish, name="api_publish"),
    path("api/check-updates/", views.api_check_updates, name="api_check_updates"),

    # ── Opt-out (no login required, linked from talk-page notification) ────────
    path("notifications/opt-out/", views.notification_opt_out, name="notification_opt_out"),
]
