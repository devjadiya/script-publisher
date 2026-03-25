# WikiScriptSync — Deployment Guide

---

## Local development

### Step 1 — Set up virtual environment

```powershell
# Windows PowerShell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 2 — Configure .env

Your `.env` is already configured with real credentials. Key points:

| Variable | Value | Notes |
|---|---|---|
| `OAUTH_CLIENT_ID` | `c4b5128cc24c0b2c46e4f63cca29de43` | Approved consumer |
| `OAUTH_CLIENT_SECRET` | `0534219c56e661de1ab794267912f6527b6a47a5` | Never commit this |
| `OAUTH_REDIRECT_URI` | Toolforge URL | OAuth only works on Toolforge |
| `NOTIFIER_BOT_USERNAME` | `WikiScriptSyncBot@notifier` | mediawiki.org account |
| `NOTIFIER_BOT_PASSWORD` | `l5ktfaffon324qah6vdmkoboar9rigq9` | Edit existing pages only |
| `NOTIFIER_BOT_WIKI` | `meta.wikimedia.org` | Where bot posts notifications |

### Step 3 — Migrate and run

```powershell
python manage.py migrate
python manage.py runserver
```

Open: http://localhost:8000

---

## Authentication: local vs Toolforge

| Method | Local dev | Toolforge |
|---|---|---|
| **BotPassword tab** | ✅ Works | ✅ Works |
| **Wikimedia OAuth** | ❌ Blocked | ✅ Works |

**Why OAuth doesn't work locally:** Your registered OAuth consumer only allows the
callback URL `https://script-publisher.toolforge.org/api/auth/callback`. Wikimedia
rejects any other redirect URI. This is correct security behaviour.

**For local testing:** Use the BotPassword tab on `/get-started/`. Enter your own
Wikimedia BotPassword credentials. The audit log will use whichever `wiki_username`
you enter.

---

## Notification bot

The bot account `WikiScriptSyncBot` on mediawiki.org posts talk-page notifications.

- **Account:** `WikiScriptSyncBot` on `mediawiki.org`
- **Bot password name:** `notifier`
- **Grants given:** Edit existing pages ONLY — no JS editing, no sitewide rights
- **What it does:** Appends a new section to `User_talk:{username}` when an
  upstream repository file hash changes

**This completely sidesteps the OAuth JS-editing security concern** raised by Tgr (WMF)
because the bot never touches JS or CSS pages — only talk pages.

To test notifications locally:

1. Log in via BotPassword tab
2. Publish a file (draft path is fine — it still registers the TrackedScript)
3. Visit `http://localhost:8000/api/check-updates/`
4. If the upstream file has changed since publish, the bot will post to your talk page

---

## Toolforge deployment

### Set environment variables

```bash
ssh login.toolforge.org

toolforge envvars create SECRET_KEY "generate-a-long-random-string"
toolforge envvars create DEBUG "False"
toolforge envvars create ALLOWED_HOSTS "script-publisher.toolforge.org"
toolforge envvars create OAUTH_CLIENT_ID "c4b5128cc24c0b2c46e4f63cca29de43"
toolforge envvars create OAUTH_CLIENT_SECRET "0534219c56e661de1ab794267912f6527b6a47a5"
toolforge envvars create OAUTH_REDIRECT_URI "https://script-publisher.toolforge.org/api/auth/callback"
toolforge envvars create NOTIFIER_BOT_USERNAME "WikiScriptSyncBot@notifier"
toolforge envvars create NOTIFIER_BOT_PASSWORD "l5ktfaffon324qah6vdmkoboar9rigq9"
toolforge envvars create NOTIFIER_BOT_WIKI "meta.wikimedia.org"
```

For MariaDB (recommended for production):

```bash
# Get your credentials
ssh login.toolforge.org
cat ~/replica.my.cnf

toolforge envvars create DATABASE_URL \
  "mysql://s12345:YOUR_PASSWORD@tools.db.svc.wikimedia.cloud/s12345__script_publisher"
```

### Deploy

```bash
bash build.sh
```

---

## GitLab commit message sequence

Use these exact messages for the code review — Nux specifically noted that
vague messages like "updated" make the code hard to review:

```
Fix architecture: move Python modules out of templates/ into publisher app
Replace gitingest.com with direct GitHub and GitLab API fetching
Fix requirements.txt: Django 5.2.1 replaces non-existent 6.0
Add notification system: bot posts to User_talk pages on upstream changes
Add TrackedScript, UserPreference, NotificationLog models
Add notifications dashboard with opt-in/opt-out and notification log
Consolidate all migrations into single 0001_initial
Add WikiScriptSyncBot: dedicated bot account for talk-page notifications
```

---

## Security checklist

- [ ] `DEBUG=False` on Toolforge
- [ ] `SECRET_KEY` is unique, not in git
- [ ] `OAUTH_CLIENT_SECRET` only in Toolforge env vars, never committed
- [ ] `NOTIFIER_BOT_PASSWORD` only in Toolforge env vars, never committed
- [ ] Bot has "Edit existing pages" only — no JS/CSS editing rights
- [ ] `db.sqlite3` in `.gitignore` ✅
- [ ] `.env` in `.gitignore` ✅
- [ ] OAuth scope is `basic` (identity only) — no JS editing via OAuth ✅
