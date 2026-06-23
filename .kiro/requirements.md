# Water Entry Logging - Requirements

## Problem Statement
Users can mention water intake (e.g., "drank 2 glasses of water"), but the bot doesn't log it to the Water_Log sheet. The parsing logic exists but is never called.

## Functional Requirements

### R1: Detect Water Mentions
- When a user sends a message, detect if it's about water intake
- Use the existing `try_parse_water_ml()` regex parser first (fast, no API call)
- Extract the amount in millilitres from natural language (e.g., "500ml", "2 glasses", "1.5L")
- Support various units: ml, litre/liter, glass, cup, bottle

**Acceptance Criteria:**
- "drank 250ml water" → detected as 250ml water entry
- "had 2 glasses of water" → detected as 500ml (2 × 250ml per glass)
- "1.5 liters of water" → detected as 1500ml
- "how much water should i drink" → NOT detected (no amount)
- "2 eggs and 1 banana" → NOT detected (no water keyword)

### R2: Show Water Entry Confirmation
- Display a preview showing the amount, progress toward daily goal, and hydration bar
- Provide two buttons: ✅ Log it and ❌ Discard
- Include context: current total for today, daily goal, remaining to reach goal
- Show progress bar (e.g., 3/4 filled circles toward 2L goal)

**Acceptance Criteria:**
- Preview shows: "+[amount] ml logged"
- Shows progress bar with emojis (blue circles filled, white circles empty)
- Shows: "Today: X.XX L / Y.YY L goal"
- Shows remaining ml if not yet at goal
- Shows "🎉 Goal reached!" if goal is met

### R3: Log Water to Sheet
- When user confirms, save to Water_Log sheet
- Append row: [Date (DD-MM-YYYY), Time (HH:MM:SS), Amount (ml)]
- Update Daily_Summary sheet with new water total for today
- Show success message

**Acceptance Criteria:**
- Water entry appears in Water_Log sheet with correct date, time, amount
- Daily_Summary's water total updates immediately
- Water appears in `/today` and `/goal` commands
- Success message confirms: "✅ +X ml logged to your sheet!"

### R4: Error Handling
- If user tries to log 0ml or negative: show validation error
- If user tries to log >5000ml: show validation error
- If Google Sheets connection fails: show error, keep water in preview
- If water entry is too old when confirming: show "entry expired, please resend"

**Acceptance Criteria:**
- Invalid amounts show: "⚠️ Water amount must be between 1 and 5000 ml"
- Sheet errors show: "⚠️ Couldn't write to Google Sheets right now..."
- User can retry or discard without issues

### R5: Maintain Food Logging Priority
- Water detection should not interfere with food logging
- If a message isn't detected as water, proceed to AI food analysis
- Food entries should still work exactly as before

**Acceptance Criteria:**
- "drank 250ml water" → logged as water (not sent to AI)
- "ate a chicken sandwich" → sent to AI food analysis as usual
- Both flows work independently

## Non-Functional Requirements

### NR1: Consistency with Food Flow
- Water confirmation UX should match food confirmation (same button style, similar messaging)
- Use existing patterns: inline keyboard, callback queries, pending entry storage

### NR2: Performance
- Water parsing via regex only (no API calls until confirmation)
- Should not slow down text message handling

### NR3: Reliability
- All water entries logged are reflected in Daily_Summary
- No orphaned/lost water entries
- Retry-safe: users can resend if confirmation times out

## Out of Scope
- Voice message water detection
- Manual water log correction / deletion from sheet (separate feature)
- Water reminders (already implemented in scheduler.py)
