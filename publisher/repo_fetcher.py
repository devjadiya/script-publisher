"""
repo_fetcher.py
---------------
Fetches JS/CSS files from public GitHub or GitLab repositories.
No third-party services — all requests go directly to the platform API.

Rate limit strategy:
  - fetch_files()        — fetches ALL JS/CSS files (used on Upload page)
  - fetch_single_file()  — fetches ONE file by path (used by notifier for hashing)
  - get_file_sha_github()— returns only the blob SHA of a file, uses 1 API call
                           and does NOT count toward rate limits the same way
                           (used for efficient change detection)

GitHub rate limits (unauthenticated): 60 requests/hour per IP.
For the notifier, we use get_file_sha_github() which is much cheaper.
"""

import re
import base64
import logging
import requests

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".js", ".css"}
DEFAULT_TIMEOUT    = 15


class RepoFetchError(Exception):
    """Raised when a repository cannot be fetched or parsed."""


# ── Public entry points ───────────────────────────────────────────────────────

def fetch_files(repo_url: str) -> list[dict]:
    """
    Fetch all JS/CSS files from a public repository.
    Returns list of {name, content, size, platform}.
    Used on the Upload page.
    """
    platform, owner, repo, branch = _parse_url(repo_url)
    if platform == "github":
        return _fetch_github(owner, repo, branch)
    elif platform == "gitlab":
        base = _gitlab_base(repo_url)
        return _fetch_gitlab(base, owner, repo, branch)
    raise RepoFetchError(f"Unsupported repository URL: {repo_url}")


def fetch_single_file(repo_url: str, file_path: str) -> str | None:
    """
    Fetch the content of a single file from a repository.
    Much cheaper than fetch_files() — only 1-2 API calls.
    Returns the file content as a string, or None if not found.
    Used by the notifier for SHA-256 hashing.
    """
    try:
        platform, owner, repo, branch = _parse_url(repo_url)
    except RepoFetchError:
        return None

    if platform == "github":
        if not branch:
            try:
                branch = _github_default_branch(owner, repo)
            except RepoFetchError:
                return None
        return _github_file_content(owner, repo, branch, file_path)

    elif platform == "gitlab":
        base = _gitlab_base(repo_url)
        from urllib.parse import quote
        project_path = quote(f"{owner}/{repo}", safe="")
        if not branch:
            try:
                branch = _gitlab_default_branch(base, project_path)
            except RepoFetchError:
                return None
        return _gitlab_file_content(base, project_path, branch, file_path)

    return None


def get_file_sha_github(repo_url: str, file_path: str) -> str | None:
    """
    Get the git blob SHA of a single file from GitHub using the Trees API.
    This is the most rate-efficient way to detect changes:
      - 1 API call total (or 2 if branch is unknown)
      - Returns the git blob SHA (not a content hash)
      - GitHub blob SHAs change whenever file content changes
      - Does NOT require fetching file content at all

    Returns the blob SHA string, or None on any failure.
    Used by the notifier instead of downloading the full file content.
    """
    try:
        platform, owner, repo, branch = _parse_url(repo_url)
        if platform != "github":
            return None

        if not branch:
            branch = _github_default_branch(owner, repo)

        # Trees API with recursive=1 — returns the whole tree including blob SHAs
        tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        data = _get(tree_url, "GitHub").json()

        for item in data.get("tree", []):
            if item.get("type") == "blob" and item.get("path") == file_path:
                return item.get("sha")

        logger.debug("get_file_sha_github: '%s' not found in tree", file_path)
        return None

    except RepoFetchError as e:
        logger.debug("get_file_sha_github failed: %s", e)
        return None
    except Exception as e:
        logger.debug("get_file_sha_github unexpected error: %s", e)
        return None


# ── URL parsing ───────────────────────────────────────────────────────────────

def _parse_url(url: str):
    """Returns (platform, owner, repo, branch). branch may be None."""
    url = url.rstrip("/")

    gh = re.match(
        r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/tree/([^/]+))?$", url
    )
    if gh:
        return "github", gh.group(1), gh.group(2), gh.group(3)

    gl = re.match(
        r"https://(gitlab\.wikimedia\.org|gitlab\.com)/([^/]+(?:/[^/]+)*)/([^/]+?)(?:\.git)?(?:/-/tree/([^/]+))?$",
        url,
    )
    if gl:
        return "gitlab", gl.group(2), gl.group(3), gl.group(4)

    raise RepoFetchError(
        "Could not parse repository URL. "
        "Expected a public GitHub or GitLab URL. "
        f"Got: {url}"
    )


def _gitlab_base(url: str) -> str:
    m = re.match(r"(https://[^/]+)", url)
    return m.group(1) if m else "https://gitlab.com"


# ── GitHub fetchers ───────────────────────────────────────────────────────────

def _fetch_github(owner: str, repo: str, branch: str | None) -> list[dict]:
    if not branch:
        branch = _github_default_branch(owner, repo)

    tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    data     = _get(tree_url, "GitHub").json()

    files = []
    for item in data.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item["path"]
        if not _is_allowed(path):
            continue
        content = _github_file_content(owner, repo, branch, path)
        if content is not None:
            files.append({
                "name":     path,
                "content":  content,
                "size":     item.get("size", 0),
                "platform": "github",
            })
    return files


def _github_default_branch(owner: str, repo: str) -> str:
    r = _get(f"https://api.github.com/repos/{owner}/{repo}", "GitHub")
    return r.json().get("default_branch", "main")


def _github_file_content(owner: str, repo: str, branch: str, path: str) -> str | None:
    """Fetch a single file's content from GitHub Contents API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    try:
        data    = _get(url, "GitHub").json()
        encoded = data.get("content", "")
        return base64.b64decode(encoded.replace("\n", "")).decode("utf-8", errors="replace")
    except RepoFetchError:
        raise
    except Exception as e:
        logger.debug("_github_file_content failed for %s: %s", path, e)
        return None


# ── GitLab fetchers ───────────────────────────────────────────────────────────

def _fetch_gitlab(base: str, namespace: str, repo: str, branch: str | None) -> list[dict]:
    from urllib.parse import quote
    project_path = quote(f"{namespace}/{repo}", safe="")

    if not branch:
        branch = _gitlab_default_branch(base, project_path)

    tree_url  = (
        f"{base}/api/v4/projects/{project_path}/repository/tree"
        f"?recursive=true&per_page=100&ref={branch}"
    )
    all_items = []
    page = 1
    while True:
        items = _get(f"{tree_url}&page={page}", "GitLab").json()
        if not items:
            break
        all_items.extend(items)
        if len(items) < 100:
            break
        page += 1

    files = []
    for item in all_items:
        if item.get("type") != "blob":
            continue
        path = item["path"]
        if not _is_allowed(path):
            continue
        content = _gitlab_file_content(base, project_path, branch, path)
        if content is not None:
            files.append({
                "name":     path,
                "content":  content,
                "size":     len(content.encode()),
                "platform": "gitlab",
            })
    return files


def _gitlab_default_branch(base: str, project_path: str) -> str:
    r = _get(f"{base}/api/v4/projects/{project_path}", "GitLab")
    return r.json().get("default_branch", "main")


def _gitlab_file_content(base: str, project_path: str, branch: str, path: str) -> str | None:
    from urllib.parse import quote
    encoded_path = quote(path, safe="")
    url = f"{base}/api/v4/projects/{project_path}/repository/files/{encoded_path}/raw?ref={branch}"
    try:
        return _get(url, "GitLab").text
    except RepoFetchError:
        raise
    except Exception as e:
        logger.debug("_gitlab_file_content failed for %s: %s", path, e)
        return None


# ── Shared HTTP helper ────────────────────────────────────────────────────────

def _is_allowed(path: str) -> bool:
    return any(path.endswith(ext) for ext in ALLOWED_EXTENSIONS)


def _get(url: str, platform: str = "remote") -> requests.Response:
    headers = {
        "User-Agent": "WikiScriptPublisher/1.0 (https://script-publisher.toolforge.org; Toolforge)",
        "Accept":     "application/json",
    }
    try:
        r = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
    except requests.exceptions.ConnectionError:
        raise RepoFetchError(
            f"Could not connect to {platform}. Check your internet connection."
        )
    except requests.exceptions.Timeout:
        raise RepoFetchError(
            f"{platform} request timed out after {DEFAULT_TIMEOUT}s. Try again."
        )

    if r.status_code == 404:
        raise RepoFetchError(
            f"Not found on {platform} (404). "
            "Check the repository is public and the URL is correct."
        )
    if r.status_code == 403:
        # Check for rate limit header
        remaining = r.headers.get("X-RateLimit-Remaining", "unknown")
        reset_ts  = r.headers.get("X-RateLimit-Reset", "")
        hint = ""
        if reset_ts:
            try:
                from datetime import datetime, timezone
                reset_dt = datetime.fromtimestamp(int(reset_ts), tz=timezone.utc)
                hint = f" Resets at {reset_dt.strftime('%H:%M UTC')}."
            except Exception:
                pass
        raise RepoFetchError(
            f"GitHub rate limit reached (60 requests/hour for unauthenticated access). "
            f"Remaining: {remaining}.{hint} "
            f"Please wait a few minutes before trying again. "
            f"Avoid fetching the same repository repeatedly in quick succession."
        )
    if r.status_code == 429:
        raise RepoFetchError(
            f"{platform} returned 429 Too Many Requests. Please wait and try again."
        )
    if not r.ok:
        raise RepoFetchError(
            f"{platform} returned HTTP {r.status_code}. "
            f"URL: {url}"
        )
    return r
