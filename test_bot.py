"""
test_bot.py — Test WikiScriptSyncBot posting to a talk page.

Run from project root (venv activated):
  python test_bot.py

This tests every step: API reachability, login, CSRF token, and the edit.
If you get a CAPTCHA error at step 5, see the fix instructions printed.
"""

import os, sys, json, requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BOT_USERNAME = os.environ.get("NOTIFIER_BOT_USERNAME", "WikiScriptSyncBot@notifier-meta")
BOT_PASSWORD = os.environ.get("NOTIFIER_BOT_PASSWORD", "0mne6ndov4ptjdg4vg2p33g7tus57go5")
BOT_WIKI     = os.environ.get("NOTIFIER_BOT_WIKI",     "meta.wikimedia.org")
API_URL      = f"https://{BOT_WIKI}/w/api.php"
TARGET_TALK  = "User talk:Dev Jadiya"
USER_AGENT   = "WikiScriptPublisher/1.0 (https://script-publisher.toolforge.org; test)"


def step(n, label):
    print(f"\n{'='*60}\n  {n}. {label}\n{'='*60}")

def pp(d):
    print(json.dumps(d, indent=2, ensure_ascii=False))

if not BOT_PASSWORD:
    print("ERROR: NOTIFIER_BOT_PASSWORD is not set in .env")
    sys.exit(1)

print(f"Bot:  {BOT_USERNAME}")
print(f"Wiki: {BOT_WIKI}")
print(f"Target: {TARGET_TALK}")

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

# 1. Reachability
step(1, "API reachability")
r = session.get(API_URL, params={"action":"query","meta":"siteinfo","format":"json"}, timeout=15)
print(f"  HTTP {r.status_code}")
if not r.ok:
    print("FAIL"); sys.exit(1)
sitename = r.json().get("query",{}).get("general",{}).get("sitename","?")
print(f"  Site: {sitename} — OK")

# 2. Login token
step(2, "Login token")
r = session.get(API_URL, params={"action":"query","meta":"tokens","type":"login","format":"json"}, timeout=15)
data = r.json()
login_token = data.get("query",{}).get("tokens",{}).get("logintoken")
if not login_token:
    print("FAIL: no logintoken"); pp(data); sys.exit(1)
print(f"  Got: {login_token[:20]}… — OK")

# 3. Login
step(3, f"Login as {BOT_USERNAME}")
r = session.post(API_URL, data={"action":"login","lgname":BOT_USERNAME,"lgpassword":BOT_PASSWORD,"lgtoken":login_token,"format":"json"}, timeout=15)
data = r.json(); pp(data)
if data.get("login",{}).get("result") != "Success":
    reason = data.get("login",{}).get("reason","unknown")
    print(f"\nFAIL: Login failed — {reason}")
    print("\nPossible fixes:")
    print("  - Make sure the BotPassword was created on meta.wikimedia.org (not mediawiki.org)")
    print("  - Check NOTIFIER_BOT_USERNAME matches exactly: WikiScriptSyncBot@notifier-meta")
    print("  - Check NOTIFIER_BOT_PASSWORD is correct in .env")
    sys.exit(1)
print("\n  LOGIN OK")

# 4. CSRF token
step(4, "CSRF token")
r = session.get(API_URL, params={"action":"query","meta":"tokens","type":"csrf","format":"json"}, timeout=15)
data = r.json()
csrf = data.get("query",{}).get("tokens",{}).get("csrftoken")
if not csrf or csrf == "+\\":
    print("FAIL: no csrf token"); pp(data); sys.exit(1)
print(f"  Got: {csrf[:20]}… — OK")

# 5. Append test section
step(5, f"Appending test section to '{TARGET_TALK}'")
from datetime import datetime, timezone
ts = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC").lstrip("0")
content = (
    f"This is a connectivity test from WikiScriptSync.\n\n"
    f"Bot: [[User:WikiScriptSyncBot|WikiScriptSyncBot]]\n"
    f"Time: {ts}\n\n"
    f"If you see this, the bot is working correctly."
)
r = session.post(API_URL, data={
    "action":"edit", "title":TARGET_TALK,
    "section":"new", "sectiontitle":"WikiScriptSync bot connectivity test",
    "text":content, "summary":"WikiScriptSync bot test",
    "bot":"1", "token":csrf, "format":"json",
}, timeout=20)
data = r.json()
print("\n  FULL RESPONSE:")
pp(data)

edit = data.get("edit", {})

if "captcha" in edit:
    print("\nFAIL: CAPTCHA required — the bot does not have skipcaptcha right")
    print("\nFix:")
    print(f"  1. Log in as WikiScriptSyncBot on https://{BOT_WIKI}")
    print(f"  2. Go to https://{BOT_WIKI}/wiki/Special:BotPasswords")
    print(f"  3. Edit 'notifier-meta'")
    print(f"  4. Tick ALL of these:")
    print(f"       ✓ High-volume (bot) access     ← includes skipcaptcha")
    print(f"       ✓ Edit existing pages")
    print(f"       ✓ Create, edit, and move pages  ← needed if talk page is new")
    print(f"  5. Save — copy the NEW generated password")
    print(f"  6. Update NOTIFIER_BOT_PASSWORD in .env")
    print(f"  7. Restart Django server")
    sys.exit(1)

if "error" in data:
    print(f"\nFAIL: API error [{data['error'].get('code')}]: {data['error'].get('info')}")
    sys.exit(1)

if edit.get("result") == "Success":
    revid = edit.get("newrevid")
    print(f"\n  SUCCESS! revid={revid}")
    print(f"  https://{BOT_WIKI}/wiki/{TARGET_TALK.replace(' ','_')}")
elif "nochange" in edit:
    print("\n  nochange (content already identical — OK)")
else:
    print(f"\n  UNEXPECTED: {edit}"); sys.exit(1)

print(f"\n{'='*60}\n  ALL STEPS PASSED\n{'='*60}\n")
