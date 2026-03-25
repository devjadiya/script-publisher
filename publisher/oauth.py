"""
oauth.py
--------
Handles the Wikimedia OAuth 2.0 flow for Script Publisher.

Scope: identity only (basic).
  - We use OAuth to verify who the user is (username, groups, rights).
  - We do NOT use the OAuth token to edit wiki pages.
  - Editing is done either via the user's own BotPassword,
    or via the notification flow where the user edits themselves.

Security: this keeps the tool within its approved OAuth scope and
avoids the JS-editing concerns raised in community review.
"""

import os
import secrets
import requests
from urllib.parse import urlencode
from functools import wraps
from django.shortcuts import redirect

CLIENT_ID      = os.environ.get("OAUTH_CLIENT_ID", "")
CLIENT_SECRET  = os.environ.get("OAUTH_CLIENT_SECRET", "")
REDIRECT_URI   = os.environ.get("OAUTH_REDIRECT_URI", "")
AUTHORIZE_URL  = os.environ.get("OAUTH_AUTHORIZE_URL", "https://meta.wikimedia.org/w/rest.php/oauth2/authorize")
TOKEN_URL      = os.environ.get("OAUTH_TOKEN_URL",     "https://meta.wikimedia.org/w/rest.php/oauth2/access_token")
PROFILE_URL    = os.environ.get("OAUTH_PROFILE_URL",   "https://meta.wikimedia.org/w/rest.php/oauth2/resource/profile")
REQUEST_TIMEOUT = 10


class OAuthError(Exception):
    """Raised when any step of the OAuth flow fails."""


def build_authorize_url(request) -> str:
    """Generate the Wikimedia OAuth 2.0 authorization URL with CSRF state."""
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "basic",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def handle_callback(request) -> dict:
    """
    Called from the OAuth callback view.
    Validates state, exchanges code for token, fetches profile.
    Returns dict: {username, groups, rights, token}
    """
    returned_state = request.GET.get("state", "")
    stored_state   = request.session.pop("oauth_state", None)

    if not stored_state or returned_state != stored_state:
        raise OAuthError("State mismatch — possible CSRF attempt. Please try logging in again.")

    error = request.GET.get("error")
    if error:
        raise OAuthError(f"Wikimedia denied authorization: {request.GET.get('error_description', error)}")

    code = request.GET.get("code")
    if not code:
        raise OAuthError("No authorization code received from Wikimedia.")

    token_data   = _exchange_code(code)
    access_token = token_data.get("access_token")
    if not access_token:
        raise OAuthError("Token exchange succeeded but no access_token was returned.")

    profile = _fetch_profile(access_token)
    return {
        "username": profile.get("username", ""),
        "groups":   profile.get("groups", []),
        "rights":   profile.get("rights", []),
        "token":    access_token,
    }


def _exchange_code(code: str) -> dict:
    payload = {
        "grant_type":   "authorization_code",
        "code":         code,
        "redirect_uri": REDIRECT_URI,
        "client_id":    CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    try:
        r = requests.post(TOKEN_URL, data=payload, headers={"User-Agent": _ua()}, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise OAuthError(f"Could not reach Wikimedia token endpoint: {e}")
    if not r.ok:
        raise OAuthError(f"Token exchange failed (HTTP {r.status_code}): {r.text}")
    return r.json()


def _fetch_profile(access_token: str) -> dict:
    try:
        r = requests.get(
            PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}", "User-Agent": _ua()},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        raise OAuthError(f"Could not reach Wikimedia profile endpoint: {e}")
    if not r.ok:
        raise OAuthError(f"Profile fetch failed (HTTP {r.status_code}): {r.text}")
    return r.json()


# ── Session helpers ──────────────────────────────────────────────────────────

def login_user(request, profile: dict):
    request.session["user_username"] = profile["username"]
    request.session["user_groups"]   = profile["groups"]
    request.session["user_rights"]   = profile["rights"]
    request.session["user_token"]    = profile["token"]
    request.session.set_expiry(86400 * 7)


def logout_user(request):
    request.session.flush()


def get_current_user(request) -> dict | None:
    username = request.session.get("user_username")
    if not username:
        return None
    groups = request.session.get("user_groups", [])
    rights = request.session.get("user_rights", [])
    return {
        "username":           username,
        "groups":             groups,
        "rights":             rights,
        "is_interface_admin": "interface-admin" in groups,
    }


def require_login(view_func):
    """Decorator: redirect to /get-started/ if not logged in."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not get_current_user(request):
            return redirect("get_started")
        return view_func(request, *args, **kwargs)
    return wrapper


def _ua() -> str:
    return "WikiScriptPublisher/1.0 (https://script-publisher.toolforge.org; Toolforge)"
