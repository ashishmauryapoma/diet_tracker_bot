# Water Entry Logging - Implementation Tasks

## Task 1: Add Water Keyboard UI
**Status:** not_started

Add water confirmation keyboard to keyboards.py.

### Sub-tasks:
- [x] Define `WATER_CONFIRM_CALLBACK` and `WATER_UNDO_CALLBACK` constants
- [x] Create `water_confirm_keyboard()` function with ✅ Log / ❌ Discard buttons

### Files to modify:
- `bot/keyboards.py`

---

## Task 2: Add Water Entry Handlers
**Status:** not_started
**Depends on:** Task 1

Implement water confirmation and discard handlers in handlers.py.

### Sub-tasks:
- [x] Add `_PENDING_WATER_KEY` and `_PENDING_WATER_GLOBAL_KEY` constants
- [x] Implement `water_confirm_callback()` function:
  - Handle confirm (save to sheet, refresh summary, show success)
  - Handle discard (show discarded message)
  - Handle missing entry (show "entry expired" message)
- [x] Update `handle_text()` to detect water before food:
  - Call `try_parse_water_ml(text)` first
  - If amount found and valid: create WaterEntry, show confirmation
  - If ValidationError: show error message
  - If None: proceed with existing food/intent classification

### Files to modify:
- `bot/handlers.py`

### Testing:
- "drank 250ml water" → shows preview, saves correctly on confirm
- "2 glasses of water" → shows preview with 500ml
- "invalid water" → proceeds to food classification
- Confirm/discard buttons work
- Water appears in `/today` and `/goal` commands

---

## Task 3: Register Water Handler in Bot
**Status:** not_started
**Depends on:** Task 2

Register the water callback handler in main.py.

### Sub-tasks:
- [x] Import `water_confirm_callback` from handlers
- [x] Add `CallbackQueryHandler` for water confirmation pattern

### Files to modify:
- `bot/main.py`

---

## Task 4: Manual Testing
**Status:** not_started
**Depends on:** Task 3

Test the water logging feature end-to-end.

### Test scenarios:
- [~] User sends "drank 250ml water" → preview shows
- [x] User confirms → saved to Water_Log sheet
- [x] Check Daily_Summary updates correctly
- [x] `/goal` command shows updated water total
- [x] `/today` command shows water entry
- [~] User sends "2 glasses" → converted to 500ml
- [~] User sends "1.5 liters" → converted to 1500ml
- [~] User tries "0ml" → validation error
- [~] User tries "6000ml" → validation error
- [~] User discards entry → shows "discarded"
- [x] Food entries still work normally

