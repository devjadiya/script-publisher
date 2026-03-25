# Script Publisher — Local Setup & Testing Guide

## Prerequisites
- Python 3.11 or 3.12
- Git
- A Wikimedia account (for OAuth testing)

---

## Step 1 — Create and activate a virtual environment

### Windows (PowerShell)
```powershell
cd script-publisher
python -m venv venv
venv\Scripts\Activate.ps1
```

### macOS / Linux
```bash
cd script-publisher
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` at the start of your prompt.

---

## Step 2 — Install dependencies
```bash
pip install -r requirements.txt
```

---

## Step 3 — Set up your .env file
```bash
# Copy the example
copy .env.example .env        # Windows
cp .env.example .env          # Mac/Linux

# Edit .env — change these two lines for local testing:
#   DEBUG=True
#   OAUTH_REDIRECT_URI=http://localhost:8000/api/auth/callback
```

Your `.env` for local development should look like this:
```
DEBUG=True
SECRET_KEY=any-long-random-string-for-local-dev
ALLOWED_HOSTS=127.0.0.1,localhost

OAUTH_CLIENT_ID=c4b5128cc24c0b2c46e4f63cca29de43
OAUTH_CLIENT_SECRET=0534219c56e661de1ab794267912f6527b6a47a5
OAUTH_REDIRECT_URI=http://localhost:8000/api/auth/callback

# Notification bot (create Special:BotPasswords for a dedicated account)
NOTIFIER_BOT_USERNAME=ScriptPublisherBot@notifier
NOTIFIER_BOT_PASSWORD=your-bot-password-here
NOTIFIER_BOT_WIKI=meta.wikimedia.org

# Leave DATABASE_URL empty to use SQLite locally
```

---

## Step 4 — Run database migrations
```bash
python manage.py migrate
```

This creates `db.sqlite3` locally with all tables:
- `publisher_publishlog`
- `publisher_trackedscript`
- `publisher_userpreference`
- `publisher_notificationlog`
- Django session and admin tables

---

## Step 5 — Create a local admin account (optional, for /admin panel)
```bash
python manage.py createsuperuser
```

---

## Step 6 — Run the development server
```bash
python manage.py runserver
```

Open: http://localhost:8000

---

## Step 7 — Test the full flow locally

### OAuth login test
1. Go to http://localhost:8000/get-started/
2. Click "Continue with Wikimedia"
3. You will be redirected to meta.wikimedia.org
4. Authorize the app
5. You should land on http://localhost:8000/dashboard/

> **Note**: For OAuth to work locally, the redirect URI
> `http://localhost:8000/api/auth/callback` must be registered.
> Your current consumer has `https://script-publisher.toolforge.org/api/auth/callback`.
> You will need to register a **second** OAuth consumer for local testing,
> or temporarily update the consumer's redirect URI.
> Alternatively, test login via the BotPassword tab — that works without OAuth.

### Repository fetch test
1. Go to http://localhost:8000/dashboard/upload/
2. Enter: `https://github.com/nicowillis/wikimedia-scripts` (any public repo with .js/.css)
3. Click "Fetch files" — files should appear without any gitingest dependency

### Publish test (draft path — safest, no permissions needed)
1. Select files, click Continue
2. Set destinations in Mapping Config
3. On Publish page, leave method as "Draft URL"
4. Click "Publish files" → confirm
5. You get a pre-filled edit URL + talk-page notification wikitext

### Notification system test
1. Go to http://localhost:8000/dashboard/notifications/
2. Opt in with your wiki username
3. Go to http://localhost:8000/api/check-updates/ (GET)
4. This checks all tracked scripts for changes and queues notifications

---

## MariaDB setup (Toolforge production only)

### On the Toolforge login node:
```bash
ssh login.toolforge.org

# Create your database (one-time)
sql create

# This gives you a database named:  s<your-uid>__script_publisher
# And credentials in: ~/replica.my.cnf

# Find your credentials:
cat ~/replica.my.cnf
```

### Set DATABASE_URL on Toolforge:
```bash
toolforge envvars create DATABASE_URL \
  "mysql://s12345:YOUR_PASSWORD@tools.db.svc.wikimedia.cloud/s12345__script_publisher"
```

Replace `s12345` with your actual Toolforge username and set your actual password.

### Run migrations on Toolforge:
```bash
# SSH into your tool's shell
toolforge shell script-publisher

# Inside the tool shell:
python manage.py migrate
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'django'`**
→ Your virtual environment is not activated. Run `venv\Scripts\Activate.ps1` again.

**`django.db.OperationalError: no such table`**
→ Run `python manage.py migrate`

**OAuth callback gives 404**
→ Your OAUTH_REDIRECT_URI in .env doesn't match the server URL. For local dev, use `http://localhost:8000/api/auth/callback`.

**`DisallowedHost` error**
→ Add `127.0.0.1` and `localhost` to `ALLOWED_HOSTS` in your `.env`.