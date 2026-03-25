"""
wiki_api.py — MediaWiki API interactions for WikiScriptSync.
"""

import logging
import requests
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20

KNOWN_WIKIS = {
    "commons.wikimedia.org":  "https://commons.wikimedia.org/w/api.php",
    "en.wikipedia.org":       "https://en.wikipedia.org/w/api.php",
    "de.wikipedia.org":       "https://de.wikipedia.org/w/api.php",
    "fr.wikipedia.org":       "https://fr.wikipedia.org/w/api.php",
    "es.wikipedia.org":       "https://es.wikipedia.org/w/api.php",
    "www.wikidata.org":       "https://www.wikidata.org/w/api.php",
    "meta.wikimedia.org":     "https://meta.wikimedia.org/w/api.php",
    "en.wiktionary.org":      "https://en.wiktionary.org/w/api.php",
    "mediawiki.org":          "https://www.mediawiki.org/w/api.php",
}


class WikiAPIError(Exception):
    """Raised when a MediaWiki API call fails."""


class BotSession:
    """
    A requests session authenticated with a BotPassword.

    CAPTCHA note:
      New accounts on meta.wikimedia.org require CAPTCHA for edits.
      To bypass this, the BotPassword MUST have the 'skipcaptcha' grant.
      Go to Special:BotPasswords on meta.wikimedia.org, edit the 'notifier-meta'
      password, and tick 'High-volume (bot) access' — this includes skipcaptcha.
    """

    def __init__(self, wiki_domain: str):
        self.wiki_domain = wiki_domain.lower().strip()
        self.api_url     = _api_url(self.wiki_domain)
        self.session     = requests.Session()
        self.session.headers.update({"User-Agent": _user_agent()})
        self._logged_in  = False
        logger.debug("BotSession: %s → %s", self.wiki_domain, self.api_url)

    def login(self, bot_username: str, bot_password: str):
        """Two-step BotPassword login as required by MediaWiki API."""
        logger.info("Logging in as %s on %s", bot_username, self.wiki_domain)
        token = self._get_token("login")
        result = self._post({
            "action":     "login",
            "lgname":     bot_username,
            "lgpassword": bot_password,
            "lgtoken":    token,
            "format":     "json",
        }).json()

        login_result = result.get("login", {}).get("result")
        if login_result == "Success":
            self._logged_in = True
            logger.info("Logged in as %s on %s", bot_username, self.wiki_domain)
        elif login_result == "Failed":
            reason = result.get("login", {}).get("reason", "unknown reason")
            raise WikiAPIError(
                f"BotPassword login failed for {bot_username} on {self.wiki_domain}: {reason}"
            )
        else:
            raise WikiAPIError(
                f"Unexpected login result '{login_result}' for {bot_username}. "
                f"Full response: {result}"
            )

    def get_page_content(self, page_title: str) -> str | None:
        """Fetch current wikitext. Returns None if page does not exist."""
        data  = self._get({
            "action":  "query",
            "prop":    "revisions",
            "titles":  page_title,
            "rvprop":  "content",
            "rvslots": "main",
            "format":  "json",
        }).json()
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            if page_id == "-1":
                return None
            revisions = page_data.get("revisions", [])
            if revisions:
                return revisions[0].get("slots", {}).get("main", {}).get("*", "")
        return None

    def edit_page(self, page_title: str, new_content: str, edit_summary: str,
                  minor: bool = False) -> dict:
        """Replace the full content of a wiki page. Requires prior login()."""
        if not self._logged_in:
            raise WikiAPIError("Not logged in. Call login() first.")
        csrf_token = self._get_token("csrf")
        data = self._post({
            "action":      "edit",
            "title":       page_title,
            "text":        new_content,
            "summary":     edit_summary,
            "minor":       "1" if minor else "0",
            "bot":         "1",
            "token":       csrf_token,
            "format":      "json",
        }).json()

        logger.debug("edit_page response for %s: %s", page_title, data)

        if "error" in data:
            code = data["error"].get("code", "unknown")
            info = data["error"].get("info", "")
            raise WikiAPIError(f"Edit failed [{code}]: {info}")
        if data.get("edit", {}).get("result") != "Success":
            raise WikiAPIError(f"Edit did not succeed: {data}")
        return data.get("edit", {})

    def append_section(self, page_title: str, section_title: str,
                       content: str, summary: str) -> dict:
        """
        Append a new section to a talk page without replacing existing content.

        Uses section=new which always creates a new section at the bottom.

        IMPORTANT: Requires 'skipcaptcha' right on the BotPassword.
        On meta.wikimedia.org:
          Special:BotPasswords → edit 'notifier-meta' →
          tick 'High-volume (bot) access' (includes skipcaptcha)
        """
        if not self._logged_in:
            raise WikiAPIError("Not logged in. Call login() first.")

        csrf_token = self._get_token("csrf")
        payload = {
            "action":       "edit",
            "title":        page_title,
            "section":      "new",
            "sectiontitle": section_title,
            "text":         content,
            "summary":      summary,
            "bot":          "1",
            "token":        csrf_token,
            "format":       "json",
        }

        response_data = self._post(payload).json()

        logger.info(
            "append_section raw API response for '%s' on %s: %s",
            page_title, self.wiki_domain, response_data
        )

        # CAPTCHA block — most common failure for new bot accounts
        edit = response_data.get("edit", {})
        if "captcha" in edit:
            raise WikiAPIError(
                f"CAPTCHA required for '{page_title}' on {self.wiki_domain}. "
                f"The bot account needs the 'skipcaptcha' right. "
                f"Fix: Go to Special:BotPasswords on {self.wiki_domain}, "
                f"edit the BotPassword for this bot, and tick "
                f"'High-volume (bot) access' (which includes skipcaptcha). "
                f"The bot account also needs to be autoconfirmed "
                f"(make a few manual edits while logged in as WikiScriptSyncBot)."
            )

        if "error" in response_data:
            err_code = response_data["error"].get("code", "unknown")
            err_info = response_data["error"].get("info", str(response_data["error"]))
            raise WikiAPIError(f"Edit API error [{err_code}]: {err_info}")

        if "nochange" in edit:
            logger.warning(
                "append_section returned 'nochange' for '%s' on %s",
                page_title, self.wiki_domain
            )
            return edit

        result = edit.get("result")
        if result != "Success":
            raise WikiAPIError(
                f"append_section did not succeed for '{page_title}'. "
                f"Result: '{result}'. Full response: {response_data}"
            )

        newrevid = edit.get("newrevid")
        logger.info(
            "append_section succeeded: '%s' on %s, newrevid=%s",
            page_title, self.wiki_domain, newrevid
        )
        return edit

    def _get_token(self, token_type: str) -> str:
        data  = self._get({
            "action": "query",
            "meta":   "tokens",
            "type":   token_type,
            "format": "json",
        }).json()
        token = data.get("query", {}).get("tokens", {}).get(f"{token_type}token")
        if not token:
            raise WikiAPIError(
                f"Could not retrieve '{token_type}' token from {self.wiki_domain}. "
                f"Response: {data}"
            )
        return token

    def _get(self, params: dict) -> requests.Response:
        try:
            r = self.session.get(self.api_url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise WikiAPIError(f"Network error connecting to {self.wiki_domain}: {e}")
        if not r.ok:
            raise WikiAPIError(f"HTTP {r.status_code} from {self.wiki_domain} API.")
        return r

    def _post(self, data: dict) -> requests.Response:
        try:
            r = self.session.post(self.api_url, data=data, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise WikiAPIError(f"Network error connecting to {self.wiki_domain}: {e}")
        if not r.ok:
            raise WikiAPIError(f"HTTP {r.status_code} from {self.wiki_domain} API.")
        return r


# ── Draft URL helper ──────────────────────────────────────────────────────────

def build_draft_edit_url(wiki_domain: str, page_title: str, content: str) -> str:
    """Build the edit URL for the user to open. Content shown in tool UI."""
    params = {"title": page_title, "action": "edit"}
    return f"https://{wiki_domain}/w/index.php?{urlencode(params)}"


def build_talk_page_notification(wiki_domain, page_title, repo_url, username, edit_summary):
    """Build wikitext notification for manual draft publish."""
    return (
        f"== Script update available ==\n\n"
        f"A new version of [[{page_title}]] is available from "
        f"[{repo_url} the source repository].\n\n"
        f"; Edit summary: {edit_summary}\n"
        f"; Prepared by: [[User:{username}]] using "
        f"[[Toolforge:script-publisher|WikiScriptSync]]\n\n"
        f"Please review the diff before applying. ~~~~"
    )


# ── Diff helper ───────────────────────────────────────────────────────────────

def compute_diff(old_content, new_content):
    import difflib
    old_lines = (old_content or "").splitlines()
    new_lines = new_content.splitlines()
    diff = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag == "equal":
            for line in old_lines[i1:i2]:
                diff.append({"type": "unchanged", "line": line})
        elif tag in ("replace", "delete"):
            for line in old_lines[i1:i2]:
                diff.append({"type": "removed", "line": line})
            if tag == "replace":
                for line in new_lines[j1:j2]:
                    diff.append({"type": "added", "line": line})
        elif tag == "insert":
            for line in new_lines[j1:j2]:
                diff.append({"type": "added", "line": line})
    return diff


def _api_url(wiki_domain: str) -> str:
    clean = wiki_domain.lower().strip()
    return KNOWN_WIKIS.get(clean, f"https://{clean}/w/api.php")


def _user_agent() -> str:
    return "WikiScriptPublisher/1.0 (https://script-publisher.toolforge.org; Toolforge)"
