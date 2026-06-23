# Water Entry Logging Feature - Design Document

## Overview
Water entries are currently parsed from user input but never logged to the Google Sheet. The feature needs to intercept water-related messages and save them to the Water_Log sheet with proper confirmation feedback.

## Current State
- `utils.try_parse_water_ml()` can detect water mentions and extract amounts (e.g., "drank 250ml water" → 250)
- `WaterEntry` model exists and validates water amounts (1-5000ml)
- `sheets_client.append_water_log()` exists to save water to Water_Log sheet
- `handlers.handle_text()` receives all text messages but has no water detection logic
- Water display/summaries work correctly - only logging is broken

## Design

### Flow
1. User sends text like "drank 250ml water" or "2 glasses of water"
2. `handle_text()` checks intent:
   - First: Try fast regex parse with `try_parse_water_ml()`
   - If found: Create WaterEntry, show confirmation with bar chart
   - If not found: Fall back to existing food/unknown logic
3. User confirms via inline button (✅ Log / ❌ Discard)
4. Callback handler saves to Water_Log sheet and refreshes Daily_Summary
5. Show success message with updated total and progress toward goal

### Changes Required

#### 1. handlers.py
- Add `_PENDING_WATER_KEY` constants for storing pending water entries in user_data
- Update `handle_text()` to detect water before food classification
- Add `water_confirm_callback()` handler for water confirmation buttons
- Register new callback handler in main.py

#### 2. keyboards.py
- Add `water_confirm_keyboard()` function returning inline keyboard with ✅ Log / ❌ Discard buttons
- Add constants `WATER_CONFIRM_CALLBACK` and `WATER_UNDO_CALLBACK`

#### 3. main.py
- Register `water_confirm_callback` with appropriate pattern matching

### Technical Details

**Water Detection Logic:**
```
if intent classification needed:
  - Use try_parse_water_ml(text) → returns ml amount or None
  - If amount found and valid: treat as water entry
  - Otherwise: proceed with existing food logic
```

**Keyboard Pattern:**
- Pattern: `water_confirm|water_undo` (callback_data)
- Matches water confirmation/discard buttons

**Data Flow:**
1. Parse water amount from text
2. Create WaterEntry(amount_ml, logged_at)
3. Store in context.user_data[_PENDING_WATER_KEY][message_id] = entry
4. Show preview with confirmation buttons
5. On confirm: append to Water_Log, refresh Daily_Summary
6. On discard: just remove from pending and edit message

**Error Handling:**
- ValidationError from WaterEntry (invalid ml) → show error, don't create pending entry
- SheetsError on save → show error, keep water in preview for retry
- Missing entry on callback → user sent too old water entry, ask to resend

## Precedence
Water detection happens before food classification to avoid false positives (e.g., "500ml water" shouldn't be sent to AI for food analysis).
