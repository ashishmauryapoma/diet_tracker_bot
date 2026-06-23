# Deployment Guide - Diet Tracker Bot with Water Logging

## ✅ Pre-Deployment Checklist

Before deploying the updated bot, ensure you have:

- [ ] Python 3.10+ installed
- [ ] All dependencies in requirements.txt
- [ ] Google Sheets API credentials
- [ ] Telegram Bot Token from BotFather
- [ ] Groq API key
- [ ] A Google Sheet created and ready
- [ ] Read access to this guide completely

---

## Step 1: Verify Code Changes

### Files Modified (3):
1. ✅ `bot/keyboards.py` - Added water keyboard functions
2. ✅ `bot/handlers.py` - Added water detection and confirmation handler
3. ✅ `main.py` - Registered water callback handler

### Files Created (5):
1. ✅ `.kiro/design.md` - Design specification
2. ✅ `.kiro/requirements.md` - Requirements specification
3. ✅ `.kiro/tasks.md` - Implementation tasks
4. ✅ `WATER_LOGGING_IMPLEMENTATION.md` - Implementation guide
5. ✅ `COMPLETE_PROJECT_SUMMARY.md` - Full project documentation

### Quick Verification:
```bash
# Check keyboards.py has water constants
grep "WATER_CONFIRM_CALLBACK" bot/keyboards.py

# Check handlers.py has water detection
grep "try_parse_water_ml" bot/handlers.py

# Check main.py has water handler
grep "water_confirm_callback" main.py
```

All should return matches. If not, verify files were updated correctly.

---

## Step 2: Local Testing

### 2.1 Setup Local Environment

```bash
# Navigate to project directory
cd diet_tracker_bot

# Create virtual environment (optional but recommended)
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Mac/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2.2 Configure .env File

```bash
# Copy example to .env
cp .env.example .env

# Edit .env with your credentials
# Required fields:
# - TELEGRAM_BOT_TOKEN
# - GROQ_API_KEY
# - GOOGLE_SHEET_ID
# - GOOGLE_SERVICE_ACCOUNT_* (all fields)
# - BOT_PASSWORD
```

**Example .env:**
```env
TELEGRAM_BOT_TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh
GROQ_API_KEY=gsk_abc123xyz789...
GROQ_MODEL=mixtral-8x7b-32768
GOOGLE_SHEET_ID=1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p
GOOGLE_SHEET_NAME=DietTracker
GOOGLE_SERVICE_ACCOUNT_TYPE=service_account
GOOGLE_SERVICE_ACCOUNT_PROJECT_ID=my-project
# ... all other Google service account fields ...
BOT_PASSWORD=MySecurePassword123
DAILY_CALORIE_GOAL=2000
DAILY_PROTEIN_GOAL_G=150
DAILY_CARBS_GOAL_G=225
DAILY_FAT_GOAL_G=65
DAILY_FIBER_GOAL_G=25
DAILY_WATER_GOAL_ML=2000
APP_TIMEZONE=Asia/Kolkata
```

### 2.3 Run Bot Locally

```bash
# Start the bot in polling mode
python main.py

# Expected output:
# [INFO] Starting Diet Tracker Bot | timezone=Asia/Kolkata | model=mixtral-8x7b-32768 | mode=polling
# [INFO] Connecting to Google Sheets…
# [INFO] Connected to Google Spreadsheet: DietTracker
# [INFO] Bot initialised and ready.
```

Bot is running! Keep terminal open.

### 2.4 Test in Telegram

Open Telegram and chat with your bot:

```
1. Send: /start
   Bot: 🔒 Welcome to Diet Tracker Bot. Please enter the password to continue.

2. Send: MySecurePassword123
   Bot: ✨ Welcome to Diet Tracker
        Your premium nutrition tracking experience.
        Type /help for commands.

3. Send: drank 250ml water
   Bot: 💧 +250 ml logged
        🔵🔵🔵⚪⚪⚪⚪⚪
        Today: 1.25 L / 2.00 L goal
        Remaining: 750 ml
        
        Tap ✅ Log it to save, or ❌ Discard to cancel.

4. Click: ✅ Log it
   Bot: 💧 +250 ml logged to your sheet!

5. Send: /goal
   Bot: 📊 Today's Progress
        
        Calories: 0.0/2000 kcal ░░░░░░░░░░ 0%
        Protein: 0.0/150 g ░░░░░░░░░░ 0%
        Carbs: 0.0/225 g ░░░░░░░░░░ 0%
        Fat: 0.0/65 g ░░░░░░░░░░ 0%
        Fiber: 0.0/25 g ░░░░░░░░░░ 0%
        
        💧 Water: 0.25L/2.00L 🔵⚪⚪⚪⚪⚪⚪⚪

6. Send: /today
   Bot: 📋 Today's Log
        
        💧 Water: 0.25 L
        Total Calories: 0
```

### 2.5 Test Additional Scenarios

```
Test 1 - Multiple water formats:
  Send: "had 2 glasses of water"
  Expected: 500ml logged

Test 2 - Liters:
  Send: "1.5 liters of water"
  Expected: 1500ml logged

Test 3 - Invalid water:
  Send: "drank 6000ml"
  Expected: "⚠️ Water amount cannot exceed 5000ml..."

Test 4 - Discard water:
  Send: "drank 100ml"
  Expected: [Shows preview]
  Click: ❌ Discard
  Expected: "💧 Entry discarded."

Test 5 - Food still works:
  Send: "ate a grilled chicken sandwich"
  Expected: [Food preview with nutrition]
```

### 2.6 Verify Google Sheets

1. Open your Google Sheet
2. Check "Water_Log" tab - should have new row
3. Check "Daily_Summary" tab - water total should be 250ml
4. Check "Food_Log" tab - should be empty (no food logged)

### 2.7 Stop Local Bot

```bash
# Press Ctrl+C in terminal where bot is running
```

---

## Step 3: Commit and Push Code

```bash
# Stage all changes
git add -A

# Commit
git commit -m "feat: Add water entry logging functionality

- Add water detection via regex parsing
- Add water confirmation keyboard UI
- Add water logging to Google Sheets
- Update Daily_Summary with water totals
- Support multiple water formats (ml, glasses, liters, cups, bottles)
- Add comprehensive error handling
- Include water in /goal and /today commands"

# Push to repository
git push origin main
```

---

## Step 4: Deploy to Render.com

### 4.1 If Not Using Render, Deploy to Your Server

**For Heroku:**
```bash
git push heroku main
heroku logs --tail
```

**For traditional server:**
```bash
# SSH into server
ssh user@your-server.com

# Clone/pull repository
cd /opt/diet-tracker-bot
git pull origin main

# Install dependencies
pip install -r requirements.txt

# Restart bot (depends on your setup)
systemctl restart diet-tracker-bot
```

### 4.2 For Render.com (If Using):

1. Go to [Render.com Dashboard](https://dashboard.render.com)
2. Find your "Diet Tracker Bot" service
3. It should auto-deploy from main branch
4. Check deployment logs:
   - Click "Logs" tab
   - Should see: "Bot initialised and ready"
5. Wait 2-3 minutes for deployment to complete

### 4.3 Verify Deployment

```bash
# Check if bot is running (Render dashboard shows "Live")
# Logs should show:
[INFO] Starting Diet Tracker Bot | ... | mode=webhook
[INFO] Connected to Google Spreadsheet: DietTracker
[INFO] Bot initialised and ready.
```

---

## Step 5: Post-Deployment Testing

### 5.1 Test in Telegram (Same as Local)

1. Send `/start` and authenticate
2. Test water: "drank 500ml water"
3. Test food: "ate a sandwich"
4. Test `/goal` and `/today`
5. Verify Google Sheets has entries

### 5.2 Monitor Logs

**On Render:**
```bash
# View real-time logs
# Dashboard → Select Service → Logs tab
```

**On Traditional Server:**
```bash
# Check logs
tail -f /path/to/diet-tracker-bot/logs/bot.log
```

Watch for errors like:
```
ERROR: append_water_log failed
ERROR: Could not connect to Google Sheets
ERROR: Groq analysis failed
```

### 5.3 Common Post-Deployment Issues

| Issue | Symptom | Fix |
|-------|---------|-----|
| Bot offline | No response to `/start` | Check Render dashboard, restart service |
| Water not saving | Water preview shown but not logged | Check Google Sheets permissions, verify GOOGLE_SHEET_ID |
| No water in /goal | Water total shows 0 after logging | Restart bot to refresh cache |
| Timeout errors | "Bot didn't respond" | Check internet connection, Groq API status |

---

## Step 6: Monitoring & Maintenance

### Daily Monitoring:

```bash
# Check bot is alive
# Send: /start in Telegram
# Should respond within 5 seconds

# Check logs for errors
tail -100 logs/bot.log | grep ERROR

# Verify recent entries in Google Sheets
# Water_Log should have today's entries
# Daily_Summary should show today's total
```

### Weekly Maintenance:

```bash
# Archive old logs
gzip logs/bot.log
mv logs/bot.log.gz logs/bot.log.$(date +%Y%m%d).gz

# Check Google Sheets quota
# API quota shown in Google Cloud Console

# Monitor error rates
grep "SheetsError\|GroqAnalysisError" logs/bot.log | wc -l
```

### Monthly Tasks:

- [ ] Update python-telegram-bot library: `pip install --upgrade python-telegram-bot`
- [ ] Review and update goals in .env if needed
- [ ] Backup Google Sheets data
- [ ] Check Groq API usage and billing
- [ ] Review error logs for patterns
- [ ] Test complete flow one more time

---

## Rollback Plan (If Something Goes Wrong)

### Quick Rollback:

```bash
# Revert last commit
git revert HEAD

# Push
git push origin main

# Bot auto-redeploys (Render) or manually restart
```

### Detailed Rollback:

```bash
# Show commit history
git log --oneline -5

# Reset to previous version
git reset --hard <commit-hash>

# Force push (be careful!)
git push -f origin main
```

### Data Safety:

Water and food data in Google Sheets is NEVER deleted by code rollbacks. All logged entries remain safe.

---

## Performance Monitoring

### Response Times (Expected):

- `/start` command: <500ms
- Water detection: <50ms (regex only)
- Food analysis: 1-3 seconds (API call)
- Sheet save: 0.5-2 seconds (with retries)
- `/goal` command: <1 second
- `/today` command: <1 second
- `/analyze` command: 2-5 seconds

### Resource Usage (Expected):

- Memory: 50-150MB (depending on activity)
- CPU: <5% idle, 20-50% during API calls
- Disk: ~1MB logs per day
- Network: ~100KB per user per day

---

## Update Procedure (Future Updates)

When updating the bot:

```bash
# 1. Pull latest code
git pull origin main

# 2. Install new dependencies (if any)
pip install -r requirements.txt

# 3. Test locally
python main.py

# 4. Send `/start` to test
# 5. Stop bot (Ctrl+C)

# 6. Deploy (auto on Render, manual restart elsewhere)
# 7. Verify in Telegram
# 8. Check logs for errors
# 9. Monitor for 24 hours
```

---

## Troubleshooting Guide

### Bot won't start:

```bash
# Check Python version
python --version  # Should be 3.10+

# Check dependencies
pip list | grep telegram

# Check .env exists and is readable
cat .env | head -5

# Check error message
python main.py 2>&1 | head -20
```

### Bot starts but doesn't respond:

```bash
# Check Telegram bot token is correct
# Send to BotFather: /token <your_bot>

# Check internet connection
ping google.com

# Check Telegram API status
# Visit: https://telegram.org/

# Check logs
tail -50 logs/bot.log | grep -i "error"
```

### Water entries not saving:

```bash
# Test Google Sheets connection
python -c "from bot.sheets_client import SheetsClient; print('OK')"

# Check sheet exists
# Visit Google Sheets and verify:
# - Sheet name matches GOOGLE_SHEET_NAME
# - Has "Water_Log" tab
# - "Daily_Summary" tab

# Check credentials
# Verify service account has edit permissions
```

### Food analysis not working:

```bash
# Check Groq API key
# Visit: https://console.groq.com/

# Test Groq connection
python -c "from bot.groq_client import GroqNutritionClient; print('OK')"

# Check API usage in Groq dashboard
# Ensure you haven't exceeded quota
```

---

## Success Indicators

After deployment, you should see:

✅ Bot responds to `/start` within 5 seconds
✅ Water entries logged show confirmation immediately
✅ Google Sheets updated within 2 seconds of confirmation
✅ `/goal` command shows water in progress bar
✅ `/today` command lists water entries
✅ No ERROR entries in logs (occasional WARN is ok)
✅ Multiple users can use bot simultaneously
✅ Entries persist across bot restarts

---

## Getting Help

### If something breaks:

1. Check logs for error messages
2. Review this troubleshooting guide
3. Test locally to isolate the issue
4. Check API status pages (Telegram, Groq, Google)
5. Review recent changes
6. Rollback to last known good version if needed

### Common Error Messages:

| Error | Cause | Solution |
|-------|-------|----------|
| `ConfigError: Missing TELEGRAM_BOT_TOKEN` | Env var not set | Check .env file |
| `SheetsError: Could not connect to Google Sheets` | Auth or network issue | Check GOOGLE_* env vars |
| `GroqAnalysisError: API request failed` | Groq service down or quota exceeded | Check Groq dashboard |
| `ValidationError: Water amount cannot exceed 5000ml` | User entered invalid amount | Normal - bot rejects it |

---

## Final Checklist Before Going Live

- [ ] All 3 code files modified correctly
- [ ] Tested locally with multiple water entries
- [ ] Tested food entries still work
- [ ] Tested `/goal` and `/today` commands
- [ ] Google Sheets updated correctly
- [ ] Error cases handled (invalid input, sheet errors)
- [ ] .env file properly configured
- [ ] No credentials in git history
- [ ] render.yaml or deployment config updated
- [ ] Deployed to production server
- [ ] Tested in production Telegram bot
- [ ] Verified Google Sheets has entries from production
- [ ] Monitoring logs for errors
- [ ] Documentation updated (this guide)
- [ ] Rollback plan ready if needed

---

## Deployment Complete! ✅

Your Diet Tracker Bot with water logging is now live. Users can:

1. **Log water** - Natural language: "drank 250ml", "2 glasses", "1.5 liters"
2. **See progress** - `/goal` shows water bar toward daily goal
3. **View logs** - `/today` shows all water entries
4. **Continue food logging** - Existing functionality unchanged

Monitor the logs for the first 24 hours to ensure everything runs smoothly.

---

## Next Steps (Optional Enhancements)

After initial deployment, consider:

1. **Add /water command** - Quick preset water amounts
2. **Add history endpoint** - View past entries
3. **Add statistics** - Weekly/monthly reports
4. **Add notifications** - Scheduled water reminders
5. **Add social features** - Share progress with friends

See `COMPLETE_PROJECT_SUMMARY.md` for more enhancement ideas.

---

**Happy tracking! 🎉**
