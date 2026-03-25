"""
views.py — complete view layer for WikiScriptSync.
"""

import json
import logging
import os

from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone

from .oauth import (
    build_authorize_url, handle_callback,
    login_user, logout_user, get_current_user,
    require_login, OAuthError,
)
from .repo_fetcher import fetch_files, RepoFetchError
from .wiki_api import (
    BotSession, WikiAPIError,
    build_draft_edit_url, build_talk_page_notification, KNOWN_WIKIS,
)
from .models import PublishLog, TrackedScript, UserPreference, NotificationLog
from .notifier import check_and_notify_all, register_tracked_script

logger = logging.getLogger(__name__)


# ── Public pages ──────────────────────────────────────────────────────────────

def home(request):
    return render(request, "publisher/home.html", {
        "current_user": get_current_user(request),
    })


def get_started(request):
    if get_current_user(request):
        return redirect("dashboard")

    if request.method == "POST" and request.POST.get("auth_type") == "botpassword":
        bot_username  = request.POST.get("bot_username", "").strip()
        bot_password  = request.POST.get("bot_password", "")
        wiki_username = request.POST.get("wiki_username", "").strip()

        if not bot_username or not bot_password or not wiki_username:
            return render(request, "publisher/get_started.html",
                          {"error": "All three fields are required."})

        request.session["user_username"] = wiki_username
        request.session["user_groups"]   = []
        request.session["user_rights"]   = []
        request.session["user_token"]    = None
        request.session["bot_username"]  = bot_username
        request.session["bot_password"]  = bot_password
        request.session.set_expiry(86400)
        return redirect("dashboard")

    error = request.session.pop("auth_error", None)
    return render(request, "publisher/get_started.html", {"error": error})


# ── OAuth ─────────────────────────────────────────────────────────────────────

def oauth_redirect(request):
    return redirect(build_authorize_url(request))


def oauth_callback(request):
    try:
        profile = handle_callback(request)
        login_user(request, profile)
        return redirect("dashboard")
    except OAuthError as e:
        request.session["auth_error"] = str(e)
        return redirect("get_started")


def logout_view(request):
    logout_user(request)
    return redirect("home")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@require_login
def dashboard_home(request):
    user = get_current_user(request)
    recent_logs     = PublishLog.objects.filter(username=user["username"])[:5]
    tracked_count   = TrackedScript.objects.filter(username=user["username"], is_active=True).count()
    pending_updates = TrackedScript.objects.filter(
        username=user["username"], is_active=True, has_update=True
    ).count()
    pref = UserPreference.objects.filter(username=user["username"]).first()

    return render(request, "publisher/dashboard/home.html", {
        "current_user":          user,
        "recent_logs":           recent_logs,
        "tracked_count":         tracked_count,
        "pending_updates":       pending_updates,
        "notifications_enabled": pref and not pref.opted_out,
    })


@require_login
def upload_files(request):
    return render(request, "publisher/dashboard/upload_files.html", {
        "current_user": get_current_user(request),
    })


@require_login
def mapping_config(request):
    return render(request, "publisher/dashboard/mapping_config.html", {
        "current_user": get_current_user(request),
        "wiki_choices": json.dumps(list(KNOWN_WIKIS.keys())),
    })


@require_login
def publish(request):
    return render(request, "publisher/dashboard/publish.html", {
        "current_user": get_current_user(request),
        "wiki_choices": list(KNOWN_WIKIS.keys()),
    })


@require_login
def publish_log(request):
    user = get_current_user(request)
    logs = PublishLog.objects.filter(username=user["username"])
    return render(request, "publisher/dashboard/publish_log.html", {
        "current_user": user,
        "logs":         logs,
    })


@require_login
def settings_view(request):
    return render(request, "publisher/dashboard/settings.html", {
        "current_user": get_current_user(request),
    })


# ── Notifications ─────────────────────────────────────────────────────────────

@require_login
def notifications(request):
    user = get_current_user(request)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "opt_in":
            pref, _ = UserPreference.objects.get_or_create(
                username=user["username"], defaults={"opted_out": False}
            )
            pref.opted_out    = False
            pref.opted_out_at = None
            pref.save()
        elif action == "opt_out":
            pref, _ = UserPreference.objects.get_or_create(username=user["username"])
            pref.opted_out    = True
            pref.opted_out_at = timezone.now()
            pref.save()
        return redirect("notifications")

    pref              = UserPreference.objects.filter(username=user["username"]).first()
    tracked           = TrackedScript.objects.filter(username=user["username"], is_active=True)
    notification_logs = NotificationLog.objects.filter(
        notified_username=user["username"]
    ).select_related("tracked_script")[:20]

    return render(request, "publisher/dashboard/notifications.html", {
        "current_user":         user,
        "pref":                 pref,
        "tracked":              tracked,
        "notification_logs":    notification_logs,
        "notifier_bot_wiki":    os.environ.get("NOTIFIER_BOT_WIKI", "meta.wikimedia.org"),
        "notifier_bot_display": (
            os.environ.get("NOTIFIER_BOT_USERNAME", "").split("@")[0] or "WikiScriptSyncBot"
        ),
    })


def notification_opt_out(request):
    from django.core import signing
    from django.core.signing import BadSignature, SignatureExpired

    token = request.GET.get("token", "")
    try:
        username = signing.loads(token, salt="notification-opt-out", max_age=86400 * 30)
    except (BadSignature, SignatureExpired):
        return render(request, "publisher/opt_out_result.html", {
            "error": "This opt-out link is invalid or has expired.",
        })

    pref, _ = UserPreference.objects.get_or_create(username=username)
    pref.opted_out    = True
    pref.opted_out_at = timezone.now()
    pref.save()

    return render(request, "publisher/opt_out_result.html", {
        "username": username,
        "success":  True,
    })


# ── API: fetch repository ─────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def api_fetch_repo(request):
    if not get_current_user(request):
        return JsonResponse({"error": "Not authenticated."}, status=401)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    repo_url = body.get("repo_url", "").strip()
    if not repo_url:
        return JsonResponse({"error": "repo_url is required."}, status=400)

    try:
        files = fetch_files(repo_url)
    except RepoFetchError as e:
        return JsonResponse({"error": str(e)}, status=422)
    except Exception:
        logger.exception("Unexpected error in api_fetch_repo")
        return JsonResponse({"error": "Unexpected error fetching repository."}, status=500)

    return JsonResponse({"files": files, "count": len(files)})


# ── API: page content for diff ────────────────────────────────────────────────

@require_GET
def api_page_content(request):
    if not get_current_user(request):
        return JsonResponse({"error": "Not authenticated."}, status=401)

    wiki = request.GET.get("wiki", "").strip()
    page = request.GET.get("page", "").strip()

    if not wiki or not page:
        return JsonResponse({"error": "wiki and page parameters are required."}, status=400)

    try:
        content = BotSession(wiki).get_page_content(page)
        return JsonResponse({"content": content, "exists": content is not None})
    except WikiAPIError as e:
        return JsonResponse({"error": str(e)}, status=422)


# ── API: publish ──────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def api_publish(request):
    current_user = get_current_user(request)
    if not current_user:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    method       = body.get("method", "draft")
    wiki         = body.get("wiki", "").strip()
    page         = body.get("page", "").strip()
    content      = body.get("content", "").strip()
    edit_summary = (body.get("edit_summary") or "Published via WikiScriptSync").strip()
    source_repo  = body.get("source_repo", "").strip()
    source_file  = body.get("source_file", "").strip()
    version      = body.get("version", "").strip()

    if not wiki:
        return JsonResponse({"error": "wiki is required."}, status=400)
    if not page:
        return JsonResponse({"error": "page is required."}, status=400)
    if not content:
        return JsonResponse({"error": "content is required. Re-fetch the repository."}, status=400)

    if method == "botpassword":
        return _publish_botpassword(
            request, current_user, body, wiki, page, content,
            edit_summary, source_repo, source_file, version,
        )
    elif method == "draft":
        return _publish_draft(
            current_user, wiki, page, content, edit_summary,
            source_repo, source_file, version,
        )
    else:
        return JsonResponse({"error": "method must be 'botpassword' or 'draft'."}, status=400)


def _publish_botpassword(request, current_user, body, wiki, page, content,
                          edit_summary, source_repo, source_file, version):
    bot_username = (body.get("bot_username") or request.session.get("bot_username", "")).strip()
    bot_password = body.get("bot_password") or request.session.get("bot_password", "")

    if not bot_username or not bot_password:
        return JsonResponse(
            {"error": "bot_username and bot_password are required for BotPassword publish."},
            status=400,
        )

    log = PublishLog(
        username=current_user["username"],
        source_repo=source_repo, source_file=source_file,
        target_wiki=wiki, target_page=page,
        version=version, edit_summary=edit_summary,
        publish_method="botpassword", status="pending_review",
    )

    try:
        session = BotSession(wiki)
        session.login(bot_username, bot_password)
        result = session.edit_page(page, content, edit_summary)

        log.status = "success"
        log.save()

        if source_repo and source_file:
            register_tracked_script(
                username=current_user["username"],
                source_repo=source_repo, source_file=source_file,
                target_wiki=wiki, target_page=page,
                initial_content=content,
            )

        return JsonResponse({
            "success":     True,
            "method":      "botpassword",
            "wiki":        wiki,
            "page":        page,
            "revision_id": result.get("newrevid"),
            "log_id":      log.id,
        })

    except WikiAPIError as e:
        log.status        = "failed"
        log.error_message = str(e)
        log.save()
        return JsonResponse({"error": str(e)}, status=422)

    except Exception:
        log.status        = "failed"
        log.error_message = "Unexpected internal error."
        log.save()
        logger.exception("Unexpected error in BotPassword publish")
        return JsonResponse({"error": "Unexpected error during publishing."}, status=500)


def _publish_draft(current_user, wiki, page, content, edit_summary,
                   source_repo, source_file, version):
    edit_url     = build_draft_edit_url(wiki, page, content)
    notification = build_talk_page_notification(
        wiki, page,
        source_repo or "(source repo not recorded)",
        current_user["username"], edit_summary,
    )

    log = PublishLog.objects.create(
        username=current_user["username"],
        source_repo=source_repo, source_file=source_file,
        target_wiki=wiki, target_page=page,
        version=version, edit_summary=edit_summary,
        publish_method="draft", status="pending_review",
    )

    if source_repo and source_file:
        register_tracked_script(
            username=current_user["username"],
            source_repo=source_repo, source_file=source_file,
            target_wiki=wiki, target_page=page,
            initial_content=content,
        )

    return JsonResponse({
        "success":               True,
        "method":                "draft",
        "edit_url":              edit_url,
        "notification_wikitext": notification,
        "log_id":                log.id,
    })


# ── API: trigger update check ─────────────────────────────────────────────────

@require_GET
def api_check_updates(request):
    """
    GET /api/check-updates/
    Checks all tracked scripts. Sends talk-page notifications for changed ones.
    Returns full summary including any error details.
    """
    if not get_current_user(request):
        return JsonResponse({"error": "Not authenticated."}, status=401)

    summary = check_and_notify_all()

    # Build a human-readable message
    parts = [
        f"Checked {summary['checked']} script(s).",
        f"{summary['changed']} changed upstream.",
        f"{summary['notified']} notification(s) sent to talk page.",
    ]
    if summary.get("skipped_no_pref"):
        parts.append(
            f"{summary['skipped_no_pref']} skipped (user not opted in to notifications)."
        )
    if summary["errors"]:
        parts.append(f"{summary['errors']} error(s) — see error_details below.")

    return JsonResponse({
        "status":        "ok",
        "summary":       summary,
        "message":       " ".join(parts),
        "error_details": summary.get("error_details", []),
        "talk_page_url": f"https://meta.wikimedia.org/wiki/User_talk:{get_current_user(request)['username'].replace(' ', '_')}",
    })
