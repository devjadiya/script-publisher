"""
notifier.py — Talk-page notification system for WikiScriptSync.

Change detection strategy:
  For GitHub repos we use get_file_sha_github() which fetches the git
  tree (1 API call) and compares the blob SHA — NO file content download.
  This is ~10x cheaper than downloading file content and much less likely
  to hit rate limits.

  For GitLab or if the SHA approach fails, we fall back to fetching the
  single file content and computing a SHA-256 hash.

Every error is caught individually so one failing script never
prevents notifications for the other scripts.
"""

import hashlib
import logging
import os

from django.utils import timezone as django_timezone
from .wiki_api import BotSession, WikiAPIError

# Ensure .env is loaded (Django settings.py loads it, but double-check for safety)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

BOT_USERNAME  = os.environ.get("NOTIFIER_BOT_USERNAME", "")
BOT_PASSWORD  = os.environ.get("NOTIFIER_BOT_PASSWORD", "")
BOT_WIKI      = os.environ.get("NOTIFIER_BOT_WIKI", "meta.wikimedia.org")
TOOLFORGE_URL = "https://script-publisher.toolforge.org"

# Debug: Log the bot credentials (mask password)
logger.info(f"Bot credentials loaded: USERNAME={BOT_USERNAME}, PASSWORD={'*' * len(BOT_PASSWORD) if BOT_PASSWORD else 'None'}, WIKI={BOT_WIKI}")


def _normalize_repo_url(url: str) -> str:
    """Strip trailing slash so URLs stored with/without slash compare equal."""
    return url.rstrip("/") if url else url


def _fmt_timestamp() -> str:
    """
    Return a human-readable UTC timestamp that works on Windows and Linux.
    e.g. '24 March 2026, 16:42 UTC'
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    # %d gives zero-padded day; strip leading zero manually (Windows safe)
    day   = str(now.day)          # "1" or "24" — no padding
    month = now.strftime("%B")    # "March"
    year  = now.strftime("%Y")    # "2026"
    time  = now.strftime("%H:%M") # "16:42"
    return f"{day} {month} {year}, {time} UTC"


def check_and_notify_all() -> dict:
    """
    Check all active TrackedScripts for upstream changes.
    Notify opted-in users via talk page for any that changed.
    Returns a summary dict.
    """
    from .models import TrackedScript, UserPreference

    summary = {
        "checked": 0, "changed": 0, "notified": 0,
        "errors": 0, "skipped_no_pref": 0, "error_details": []
    }

    scripts = TrackedScript.objects.filter(is_active=True)
    logger.info("check_and_notify_all: found %d active tracked script(s)", scripts.count())

    if not BOT_USERNAME or not BOT_PASSWORD:
        msg = (
            "NOTIFIER_BOT_USERNAME or NOTIFIER_BOT_PASSWORD not set in .env. "
            "Notifications will not be sent."
        )
        logger.error(msg)
        summary["error_details"].append(msg)

    changed_scripts = []  # Collect changed scripts for summary notification

    for script in scripts:
        summary["checked"] += 1
        repo_url = _normalize_repo_url(script.source_repo)

        logger.info(
            "Checking script %d: %s from %s",
            script.id, script.source_file, repo_url or "(no repo URL)"
        )

        # ── Compute current identifier (blob SHA or content hash) ────────────
        try:
            current_id = _get_current_identifier(repo_url, script.source_file)
        except Exception as e:
            msg = f"Could not check {script.source_file}: {e}"
            logger.warning(msg)
            summary["errors"] += 1
            summary["error_details"].append(msg)
            # Still update last_checked_at so we know we tried
            try:
                script.last_checked_at = django_timezone.now()
                script.save(update_fields=["last_checked_at"])
            except Exception:
                pass
            continue

        script.last_checked_at = django_timezone.now()

        stored  = script.last_hash or ""
        current = current_id or ""

        logger.info(
            "  stored=%s… current=%s…",
            stored[:12] or "(empty)",
            current[:12] or "(empty)",
        )

        if current and current != stored:
            # ── File changed ─────────────────────────────────────────────────
            script.has_update = True
            try:
                script.save(update_fields=["last_checked_at", "has_update"])
            except Exception as e:
                logger.error("Could not save script %d: %s", script.id, e)
                continue

            summary["changed"] += 1
            logger.info("  CHANGED — checking if %s wants notifications", script.username)

            pref = UserPreference.objects.filter(
                username=script.username, opted_out=False
            ).first()

            if not pref:
                summary["skipped_no_pref"] += 1
                logger.info(
                    "  Skipping: %s has not opted in. "
                    "They need to visit /dashboard/notifications/ and enable.",
                    script.username
                )
            else:
                changed_scripts.append(script)  # Collect for summary
        else:
            # ── No change ────────────────────────────────────────────────────
            script.has_update = False
            try:
                script.save(update_fields=["last_checked_at", "has_update"])
            except Exception as e:
                logger.error("Could not save script %d: %s", script.id, e)
            logger.info("  No change.")

    # Send single summary notification if any scripts changed
    if changed_scripts:
        ok, detail = _send_summary_talk_page_notification(changed_scripts)
        if ok:
            summary["notified"] += 1
        else:
            summary["errors"] += 1
            summary["error_details"].append(detail)

    return summary


def _get_current_identifier(repo_url: str, source_file: str) -> str | None:
    """
    Get a stable identifier for the current state of a file.

    For GitHub: uses the git blob SHA (1 API call, no content download).
    For GitLab / fallback: downloads the file and computes SHA-256.

    Returns a string that changes whenever the file content changes.
    Returns None if the file cannot be found or the repo cannot be reached.
    """
    from .repo_fetcher import get_file_sha_github, fetch_single_file, RepoFetchError

    if not repo_url:
        logger.warning("_get_current_identifier: empty repo_url")
        return None

    # Try GitHub blob SHA first (cheapest — 1 API call, no content download)
    if "github.com" in repo_url:
        try:
            sha = get_file_sha_github(repo_url, source_file)
            if sha:
                logger.debug(
                    "_get_current_identifier: blob SHA for %s = %s",
                    source_file, sha[:12]
                )
                return sha
            # sha is None means file not found in tree
            logger.warning(
                "_get_current_identifier: '%s' not found in GitHub tree for %s",
                source_file, repo_url
            )
            return None
        except Exception as e:
            logger.debug(
                "_get_current_identifier: blob SHA failed for %s, "
                "falling back to content hash: %s",
                source_file, e
            )
            # Fall through to content-based approach

    # Fallback: fetch file content and compute SHA-256
    try:
        content = fetch_single_file(repo_url, source_file)
        if content is None:
            logger.warning(
                "_get_current_identifier: '%s' not found in %s",
                source_file, repo_url
            )
            return None
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    except RepoFetchError as e:
        raise Exception(str(e))


def register_tracked_script(
    username, source_repo, source_file,
    target_wiki, target_page, initial_content,
):
    """Register or update a TrackedScript after a publish."""
    from .models import TrackedScript

    source_repo = _normalize_repo_url(source_repo)

    if not source_repo:
        logger.warning(
            "register_tracked_script: empty source_repo for %s/%s — "
            "change detection will not work.",
            target_wiki, target_page
        )

    initial_hash = hashlib.sha256(initial_content.encode("utf-8")).hexdigest()

    obj, created = TrackedScript.objects.update_or_create(
        username=username,
        source_repo=source_repo,
        source_file=source_file,
        target_wiki=target_wiki,
        target_page=target_page,
        defaults={
            "last_hash":       initial_hash,
            "has_update":      False,
            "is_active":       True,
            "last_checked_at": django_timezone.now(),
        },
    )
    action = "Created" if created else "Updated"
    logger.info(
        "%s TrackedScript: %s → %s/%s (hash: %s…)",
        action, source_file, target_wiki, target_page, initial_hash[:8]
    )


def _send_summary_talk_page_notification(changed_scripts) -> tuple[bool, str]:
    """Post a single summary notification section to User_talk:{username} on BOT_WIKI."""
    from .models import NotificationLog

    # Reload credentials from environment on each call, in case .env was updated.
    bot_username = os.environ.get("NOTIFIER_BOT_USERNAME", BOT_USERNAME)
    bot_password = os.environ.get("NOTIFIER_BOT_PASSWORD", BOT_PASSWORD)
    bot_wiki     = os.environ.get("NOTIFIER_BOT_WIKI", BOT_WIKI)

    if not bot_username or not bot_password:
        msg = "Bot credentials not configured — cannot send notification."
        logger.error(msg)
        return False, msg

    logger.info(
        "Using bot credentials: username=%s password=%s wiki=%s",
        bot_username,
        "*" * len(bot_password) if bot_password else "(none)",
        bot_wiki,
    )

    # Assume all scripts are for the same user (as per current logic)
    username = changed_scripts[0].username
    talk_page = f"User talk:{username}"
    section_title = "Script updates available"
    edit_summary = f"Script update notifications: {len(changed_scripts)} file(s) (WikiScriptSync)"

    try:
        message = _build_summary_notification_message(changed_scripts)
    except Exception as e:
        msg = f"Could not build summary notification message: {e}"
        logger.error(msg)
        return False, msg

    logger.info(
        "Posting summary notification to '%s' on %s as %s",
        talk_page, bot_wiki, bot_username
    )

    log = NotificationLog(
        tracked_script=changed_scripts[0],  # Log against first script
        notified_username=username,
        notified_wiki=bot_wiki,
        talk_page=talk_page,
    )

    try:
        bot = BotSession(bot_wiki)
        bot.login(bot_username, bot_password)
        result = bot.append_section(
            page_title=talk_page,
            section_title=section_title,
            content=message,
            summary=edit_summary,
        )
        newrevid = result.get("newrevid")
        log.success = True
        log.revision_id = newrevid
        log.save()

        talk_url = f"https://{BOT_WIKI}/wiki/{talk_page.replace(' ', '_')}"
        logger.info("✓ Summary notification posted: revid=%s — %s", newrevid, talk_url)
        return True, ""

    except WikiAPIError as e:
        msg = str(e)
        logger.error("WikiAPIError posting summary to %s: %s", talk_page, msg)

        if "captcha" in msg.lower():
            logger.warning("CAPTCHA hit on full message, retrying with minimal plain text")
            try:
                minimal_msg = _build_minimal_notification_message(changed_scripts)
                bot2 = BotSession(bot_wiki)
                bot2.login(bot_username, bot_password)
                result2 = bot2.append_section(
                    page_title=talk_page,
                    section_title="Script updates",
                    content=minimal_msg,
                    summary=edit_summary,
                )
                newrevid2 = result2.get("newrevid")
                log.success = True
                log.revision_id = newrevid2
                log.error_message = "Sent with minimal text after CAPTCHA on full message"
                log.save()
                logger.info("✓ Minimal notification posted (CAPTCHA fallback): revid=%s", newrevid2)
                return True, ""
            except WikiAPIError as e2:
                logger.warning("Retry with minimal text also failed: %s", str(e2))
                log.success = False
                log.error_message = f"CAPTCHA + retry failed: {str(e2)}"
                log.save()
                bot_password_name = bot_username.split("@")[-1] if "@" in bot_username else "notifier-meta"
                logger.error(
                    "CAPTCHA FIX: Log in as %s on %s, go to Special:BotPasswords, "
                    "edit '%s' password, tick 'High-volume (bot) access' AND "
                    "'Create, edit, and move pages'. Save and update NOTIFIER_BOT_PASSWORD in .env.",
                    bot_username, bot_wiki, bot_password_name
                )
                return False, str(e2)
        else:
            log.success = False
            log.error_message = msg
            log.save()
        return False, msg

    except Exception as e:
        msg = f"Unexpected error posting summary to {talk_page}: {e}"
        log.success = False
        log.error_message = msg
        log.save()
        logger.exception("Unexpected error posting summary notification")
        return False, msg


def _build_summary_notification_message(changed_scripts) -> str:
    """Build the wikitext summary notification. Minimal to avoid CAPTCHA triggers."""
    username = changed_scripts[0].username
    bot_display = BOT_USERNAME.split("@")[0] if BOT_USERNAME else "WikiScriptSyncBot"

    message = (
        f"Hello [[User:{username}|{username}]],\n\n"
        f"The following script file(s) have been updated in their source repositories:\n\n"
    )

    for script in changed_scripts:
        message += f"* {script.source_file} → [[{script.target_wiki}:{script.target_page}]]\n"

    message += (
        f"\nPlease review the changes and update if needed.\n\n"
        f"— {bot_display} (WikiScriptSync notifier)"
    )

    return message


def _build_minimal_notification_message(changed_scripts) -> str:
    """Build minimal plain-text notification for CAPTCHA fallback (no URLs/links)."""
    username = changed_scripts[0].username
    file_list = ", ".join([s.source_file for s in changed_scripts])
    return (
        f"Script update notification for [[User:{username}|{username}]].\n\n"
        f"Updated files: {file_list}\n\n"
        f"Please visit the dashboard to review and apply changes."
    )
