# 🥗 Diet Tracker Telegram Bot

A production-ready, single-user Telegram bot that tracks meals, calories,
macros, and water intake.  Groq AI handles nutrition analysis; Google Sheets
stores every entry.  Designed for one-click deploy on **Render**.

Send `2 eggs and 1 banana` — the bot logs estimated calories, protein, carbs,
fat, and fiber to your spreadsheet automatically.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Deployment on Render (Recommended)](#deployment-on-render-recommended)
4. [Local Development (Polling Mode)](#local-development-polling-mode)
5. [Google Setup — Service Account in .env](#google-setup--service-account-in-env)
6. [Environment Variable Reference](#environment-variable-reference)
7. [Commands & Usage](#commands--usage)
8. [Example Conversations](#example-conversations)
9. [Google Sheets Layout](#google-sheets-layout)
10. [Reliability](#reliability)
11. [Troubleshooting](#troubleshooting)
12. [Project Structure](#project-structure)

---

## Features

| Feature | Detail |
|---|---|
| 🔐 Auth | Password gate — only one authorised user; session held in memory |
| 🍽 Food logging | Free-text input → Groq AI → structured nutrition JSON |
| 💧 Water tracking | `/water` with quick-add buttons (250 / 500 / 750 / 1000 ml) or custom amount |
| 📊 `/summary` | Today's totals live from Google Sheets |
| 🧠 `/analyze` | Groq AI daily coaching — score, strengths, improvements, recommendation |
| 📋 `/today` | Everything logged today, grouped by meal |
| ⏰ Reminders | Water reminders at 9 AM, 12 PM, 3 PM, 6 PM, 9 PM (timezone-aware) |
| 🔄 Dual mode | **Webhook** (Render / production) or **polling** (local dev) — auto-detected |
| ♻️ Retry logic | Exponential backoff on both Groq and Sheets API calls |
| 📁 No JSON file | Google credentials stored directly as env vars — no file to upload |

---

## Architecture

```
Telegram update
      │
      ▼
handlers.py ──auth──► auth.py (in-memory session per user ID)
      │
      ├─ food text ──► groq_client.py ──► Groq API (JSON mode)
      │                      │
      │                      ▼
      │              models.py (validate + type)
      │                      │
      └──────────────► sheets_client.py ──► Google Sheets API
                             │
                        scheduler.py (JobQueue: 5×/day reminders)

Render webhook          PTB's built-in HTTP server (0.0.0.0:PORT)
  (production)    ──►   Telegram POSTs updates here over HTTPS
                         Render provides the TLS certificate automatically

Local polling           Bot long-polls Telegram (no public URL needed)
  (development)
```

---

## Deployment on Render (Recommended)

### 1 — Push the code to GitHub / GitLab

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/you/diet-tracker-bot.git
git push -u origin main
```

### 2 — Create a Render Web Service

**Option A — Blueprint (easiest)**

1. In Render, click **New +** → **Blueprint**
2. Connect your repo — Render reads `render.yaml` and pre-fills everything

**Option B — Manual**

1. **New +** → **Web Service** → connect your repo
2. Settings:
   - **Runtime**: Python
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `python main.py`
   - **Health check path**: `/`
   - **Plan**: Free (or Starter for always-on)

### 3 — Set environment variables

In the service's **Environment** tab, add every variable from
[Environment Variable Reference](#environment-variable-reference).

The secret ones (`TELEGRAM_BOT_TOKEN`, `AUTH_PASSWORD`, `GROQ_API_KEY`, all
`GOOGLE_*` fields) must be set manually — they are marked `sync: false` in
`render.yaml` so they are never accidentally committed to source control.

### 4 — Get your service URL and set WEBHOOK_URL

After the first deploy succeeds, Render shows you the service URL at the top
of the dashboard, e.g. `https://diet-tracker-bot.onrender.com`.

Add it as an environment variable:

```
WEBHOOK_URL=https://diet-tracker-bot.onrender.com
```

**Do NOT add a trailing slash.**

### 5 — Trigger a redeploy

Render will pick up the new `WEBHOOK_URL`, register the webhook with Telegram,
and your bot will be live.

> **Free-plan cold starts**: Render's free tier spins down services after
> 15 minutes of inactivity, causing a ~30-second cold start on the next
> message.  Upgrade to the **Starter** plan ($7/month) for always-on.

---

## Local Development (Polling Mode)

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — set TELEGRAM_BOT_TOKEN, AUTH_PASSWORD, GROQ_API_KEY,
# and all GOOGLE_* fields.  Leave WEBHOOK_URL empty.

python main.py
```

When `WEBHOOK_URL` is empty the bot uses long-polling — no public URL
or port-forwarding needed.

---

## Google Setup — Service Account in .env

No JSON file is uploaded or committed anywhere.  You copy individual fields
from the service-account JSON directly into env vars (or Render's dashboard).

### Step 1 — Google Cloud project

1. https://console.cloud.google.com → **New Project** (e.g. `diet-tracker-bot`)
2. **APIs & Services → Library** → enable **Google Sheets API**
3. **APIs & Services → Library** → enable **Google Drive API**

### Step 2 — Create a service account + download key

1. **IAM & Admin → Service Accounts → Create Service Account**
   Name: `diet-tracker-sa`  (click through, no project roles needed)
2. Open the new service account → **Keys** tab → **Add Key → Create new key → JSON**
3. A `.json` file downloads automatically — open it in a text editor

### Step 3 — Copy fields into your .env / Render dashboard

From the JSON file, map each field to an env var:

| JSON field | Env var |
|---|---|
| `project_id` | `GOOGLE_PROJECT_ID` |
| `private_key_id` | `GOOGLE_PRIVATE_KEY_ID` |
| `private_key` | `GOOGLE_PRIVATE_KEY` |
| `client_email` | `GOOGLE_CLIENT_EMAIL` |
| `client_id` | `GOOGLE_CLIENT_ID` |
| `client_x509_cert_url` | `GOOGLE_CLIENT_X509_CERT_URL` |

**Private key note**: The `private_key` value in the JSON already contains
`\n` sequences (escaped newlines).  Copy it exactly as-is — the bot converts
`\n` → real newlines at runtime.  In your `.env` file it looks like:

```
GOOGLE_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----
```

On Render, paste the value into the env var input box exactly as it appears
in the JSON file — Render preserves `\n` sequences correctly.

### Step 4 — Share the Google Sheet

1. Create a Google Sheet: https://sheets.google.com (name: `Diet Tracker`)
2. Copy the `client_email` value from the JSON  
   (looks like `diet-tracker-sa@your-project.iam.gserviceaccount.com`)
3. In the sheet: **Share** → paste that email → **Editor** access → Send
4. Copy the spreadsheet ID from the URL and set `GOOGLE_SHEET_ID`

The bot creates `Food_Log`, `Water_Log`, and `Daily_Summary` worksheets
automatically on first run — you don't need to create them.

---

## Environment Variable Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | From @BotFather |
| `AUTH_PASSWORD` | ✅ | — | Password user types after /start (min 4 chars) |
| `WEBHOOK_URL` | — | `` | Your Render service HTTPS URL. Leave blank for local polling |
| `PORT` | — | `8443` | Injected by Render automatically — don't set manually |
| `WEBHOOK_PATH` | — | `webhook` | URL path Telegram POSTs to |
| `GROQ_API_KEY` | ✅ | — | From console.groq.com/keys |
| `GROQ_MODEL` | — | `llama-3.3-70b-versatile` | Any Groq-hosted model |
| `GOOGLE_PROJECT_ID` | ✅ | — | From service-account JSON |
| `GOOGLE_PRIVATE_KEY_ID` | ✅ | — | From service-account JSON |
| `GOOGLE_PRIVATE_KEY` | ✅ | — | Full PEM private key from service-account JSON |
| `GOOGLE_CLIENT_EMAIL` | ✅ | — | Service account email from JSON |
| `GOOGLE_CLIENT_ID` | ✅ | — | From service-account JSON |
| `GOOGLE_CLIENT_X509_CERT_URL` | ✅ | — | From service-account JSON |
| `GOOGLE_SHEET_ID` | ✅* | — | Spreadsheet ID from URL |
| `GOOGLE_SHEET_NAME` | ✅* | — | Spreadsheet title (fallback if ID blank) |
| `APP_TIMEZONE` | — | `Asia/Kolkata` | IANA timezone |
| `DAILY_WATER_GOAL_ML` | — | `3000` | Daily water target (ml) |
| `DAILY_CALORIE_GOAL` | — | `2000` | Daily calorie target (kcal) |
| `MAX_RETRIES` | — | `3` | API retry attempts |
| `RETRY_BACKOFF_SECONDS` | — | `2` | Exponential backoff base (seconds) |
| `LOG_LEVEL` | — | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FILE` | — | `logs/bot.log` | Rotating log file (local only; Render uses stdout) |

\* Either `GOOGLE_SHEET_ID` or `GOOGLE_SHEET_NAME` must be set.

---

## Commands & Usage

| Command | Description |
|---|---|
| `/start` | Authenticate (enter password when prompted) |
| `/help` | List all commands |
| `/water` | Log water — shows quick-add buttons, or `/water 500` |
| `/summary` | Today's nutrition totals |
| `/analyze` | Groq AI analysis of today's full diet log |
| `/today` | All meals logged today, grouped by type |

Once authenticated, **any plain-text message** is treated as a food entry.
No command prefix needed.

---

## Example Conversations

**Authentication**
```
You:  /start
Bot:  🔒 Welcome to Diet Tracker Bot. Please enter the password to continue.
You:  mypassword
Bot:  👋 Welcome! Send me anything you ate, e.g. '2 eggs and toast'. Type /help for commands.
```

**Logging food**
```
You:  Chicken biryani 300g
Bot:  🍛 Logged: Chicken biryani 300g
      Serving: 300g  •  Lunch

      Calories: 510 kcal
      Protein: 28.0 g
      Carbs: 62.0 g
      Fat: 14.0 g
      Fiber: 2.5 g
```

**Water logging**
```
You:  /water
Bot:  💧 How much water would you like to log?
      [250 ml] [500 ml]
      [750 ml] [1000 ml]
      [✏️ Custom amount]

      *tap 500 ml*

Bot:  💧 +500 ml logged

      🟦🟦🟦🟦🟦🟦🟦🟦🟦⬜
      Today: 2.80 L / 3.00 L goal
      Remaining: 200 ml
```

**`/summary`**
```
📊 Daily Summary

Calories: 1850 kcal (150 under goal)
Protein: 130.0 g
Carbs: 180.0 g
Fat: 55.0 g
Fiber: 25.0 g

Water: 2.80 L / 3.00 L

Meals Logged: 5
```

**`/analyze`**
```
🧠 AI Daily Analysis

Nutrition Score: 8.5/10

Protein: Adequate
Hydration: Slightly low
Calorie balance: On target

Strengths:
✅ High protein intake
✅ Good dietary fiber

Improvements:
⚠️ Drink 700ml more water
⚠️ Add more vegetables

Recommendation:
Include leafy greens at dinner.
```

**Scheduled water reminder (auto, 5×/day)**
```
Bot:  💧 Water Reminder

      Have you had enough water today?
```

---

## Google Sheets Layout

Worksheets are created automatically on first run.

**Food_Log**

| Date | Time | Food | Calories | Protein | Carbs | Fat | Fiber | Meal Type |
|---|---|---|---|---|---|---|---|---|
| 2026-06-22 | 08:30:00 | 2 eggs and 1 banana | 245.0 | 14.0 | 25.0 | 10.0 | 3.0 | breakfast |

**Water_Log**

| Date | Time | Amount (ml) |
|---|---|---|
| 2026-06-22 | 08:32:10 | 500 |

**Daily_Summary** (one row per day, upserted on every log)

| Date | Total Calories | Total Protein | Total Carbs | Total Fat | Total Fiber | Water Intake |
|---|---|---|---|---|---|---|
| 2026-06-22 | 1850.0 | 130.0 | 180.0 | 55.0 | 25.0 | 2800 |

---

## Reliability

- **Groq calls**: `tenacity.AsyncRetrying` with exponential backoff — retries on connection errors, 429 rate-limits, and malformed JSON
- **Sheets calls**: `tenacity.Retrying` (inside `asyncio.to_thread`) — retries on `gspread.APIError`, connection errors, and timeouts
- **Partial failures**: if Groq succeeds but Sheets fails, the user still sees the nutrition breakdown and an explicit warning that it wasn't saved
- **Global error handler**: catches anything unhandled, logs the full traceback, and sends a friendly reply instead of crashing the process
- **Logs**: console (stdout) + rotating file (`logs/bot.log`, 5 MB × 5 backups)

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Bot doesn't respond | Check `TELEGRAM_BOT_TOKEN`; check Render logs for startup errors |
| `Missing required environment variable` | A `GOOGLE_*` or other required var is blank in Render's Environment tab |
| `Google auth failed` | `GOOGLE_PRIVATE_KEY` is malformed — ensure `\n` sequences are present and the full PEM block is pasted |
| `Could not open spreadsheet` | Wrong `GOOGLE_SHEET_ID` or the sheet isn't shared with `GOOGLE_CLIENT_EMAIL` as Editor |
| Webhook not receiving updates | `WEBHOOK_URL` must be the exact Render URL with `https://` and no trailing slash; trigger a redeploy after setting it |
| Reminders not arriving | Reminders are armed on first successful login; send `/start` + password after each bot restart |
| Cold-start delay (~30s) on free plan | Upgrade to Render Starter ($7/mo) for always-on service |
| "AI service unavailable" replies | Check `GROQ_API_KEY` validity and Groq rate-limit quota |

---

## Project Structure

```
diet_tracker_bot/
├── main.py                  # Entry point: webhook or polling, service wiring
├── requirements.txt
├── .env.example             # Copy to .env for local dev
├── .gitignore
├── render.yaml              # Render Blueprint (one-click deploy)
├── README.md
├── logs/
│   └── .gitkeep
└── bot/
    ├── __init__.py
    ├── config.py            # Env var loading, Google creds dict, webhook config
    ├── logger.py            # Console + rotating file logging
    ├── auth.py              # In-memory password auth + require_auth decorator
    ├── models.py            # NutritionInfo / WaterEntry / DailySummaryData
    ├── groq_client.py       # Groq AI: food analysis + daily coaching
    ├── sheets_client.py     # Google Sheets: read/write all three worksheets
    ├── handlers.py          # All Telegram command / message / callback handlers
    ├── scheduler.py         # Daily water-reminder JobQueue setup
    ├── keyboards.py         # Inline keyboard builders (water quick-add)
    └── utils.py             # Telegram message formatting helpers
```
