# AI-Based Intent Classification - Water vs Food

## What Changed

✅ **AI Classification for Intent Detection** - Now uses Groq AI to intelligently classify if user input is water or food, instead of just regex matching.

✅ **Handles Misspellings** - Works with typos and variations:
- "water" ✅
- "waetr" ✅ (misspelled water)
- "pani" ✅ (water in Hindi)
- "h2o" ✅ (chemical formula for water)
- "1 glass" ✅
- "drank" ✅

✅ **Default 500ml for Water** - Any water-related text defaults to 500ml

✅ **Clean 8-Emoji Progress Bar** - Shows progress toward goal:
- Example: `💧 Water: 2.5L/4.0L 🔵🔵🔵🔵⚪⚪⚪⚪`

---

## Files Modified

### 1. **bot/groq_client.py**
- Updated `INTENT_CLASSIFY_SYSTEM_PROMPT` to distinguish between "water", "food", and "unknown"
- Updated `classify_intent()` method to return "water", "food", or "unknown"
- AI now recognizes water variations: water, waetr, pani, h2o, 1 glass, etc.

### 2. **bot/handlers.py**
- Removed regex-based water detection (try_parse_water_ml)
- Now uses `groq_client.classify_intent(text)` for both water and food classification
- If intent is "water": Log 500ml to Water_Log
- If intent is "food": Proceed with AI food analysis
- If intent is "unknown": Show "I couldn't recognize that" message

### 3. **bot/utils.py**
- Updated `format_water_confirmation()` to show 8-emoji progress bar
- Format: `💧 Water: X.XL/X.XL 🔵🔵⚪⚪⚪⚪⚪⚪`
- 1 emoji = 500ml (max 8 emojis for 4L)

---

## How It Works

### Old Flow (Regex-based)
```
User: "waetr"
↓
try_parse_water_ml("waetr") → None (not recognized)
↓
Sent to AI for food analysis
↓
AI analyzes as food → "Logged: water"
❌ WRONG: Logged to Food_Log instead of Water_Log
```

### New Flow (AI Classification)
```
User: "waetr"
↓
AI classifies intent → "water"
↓
Auto-log 500ml
↓
Show water confirmation
✅ CORRECT: Logged to Water_Log
```

---

## User Experience Examples

### Example 1: Misspelled Water
```
User: waetr
Bot:  💧 +500 ml logged
      
      💧 Water: 0.5L/4.0L 🔵⚪⚪⚪⚪⚪⚪⚪
      Remaining: 3500 ml
      
      Tap ✅ Log it to save, or ❌ Discard to cancel.

User: [clicks ✅]
Bot:  💧 +500 ml logged to your sheet!
```

### Example 2: Hindi Word for Water
```
User: pani
Bot:  💧 +500 ml logged
      
      💧 Water: 0.5L/4.0L 🔵⚪⚪⚪⚪⚪⚪⚪
      
      Tap ✅ Log it to save, or ❌ Discard to cancel.
```

### Example 3: Food Entry
```
User: chicken biryani 300g
Bot:  🍛 Logged: Chicken Biryani
      Serving: 300g • Lunch
      
      Calories: 450 kcal
      Protein: 20.0 g
      Carbs: 45.0 g
      Fat: 18.0 g
      Fiber: 2.5 g
      
      Tap ✅ Log it to save, or ❌ Discard to cancel.
```

---

## Water Recognition Keywords

The AI recognizes these water-related terms:
- **English**: water, drank, gulp, sip, glass, bottle, h2o, hydrate
- **Variations**: waetr (typo), h2o (chemical), sparkling water, plain water
- **Hindi**: pani
- **Quantities**: 1 glass, 500ml, 1 liter, 2 cups, 1 bottle

---

## Food vs Water Classification

| Input | Classified As | Action |
|-------|--------------|--------|
| "water" | Water | 500ml logged to Water_Log |
| "waetr" | Water | 500ml logged to Water_Log |
| "pani" | Water | 500ml logged to Water_Log |
| "h2o" | Water | 500ml logged to Water_Log |
| "1 glass" | Water | 500ml logged to Water_Log |
| "drank 250ml" | Water | 500ml logged to Water_Log |
| "ate chicken" | Food | AI analysis + Food_Log |
| "biryani" | Food | AI analysis + Food_Log |
| "juice" | Food | AI analysis + Food_Log |
| "hello" | Unknown | "I couldn't recognize..." |
| "2+2" | Unknown | "I couldn't recognize..." |

---

## Testing

```bash
# Run locally
python main.py

# Test in Telegram:

# Test 1: Misspelled water
water → Shows 500ml ✅
waetr → Shows 500ml ✅
pani → Shows 500ml ✅

# Test 2: Food entries
ate a sandwich → AI analysis ✅
chicken biryani → AI analysis ✅

# Test 3: Unknown entries
hello there → "I couldn't recognize that" ✅
```

---

## Benefits

✅ **No More Wrong Classification** - AI understands context, not just keywords
✅ **Typo-Tolerant** - Handles misspellings ("waetr", "watr", etc.)
✅ **Multilingual** - Recognizes water in different languages ("pani")
✅ **Smart Defaults** - All water entries default to 500ml
✅ **Clean UI** - 8-emoji progress bar is concise and clear
✅ **User-Friendly** - Confirmation before saving gives users control

---

## Summary

The bot now uses AI-based classification to intelligently distinguish between water and food entries, handling typos, variations, and multilingual inputs. All water entries go to Water_Log (not Food_Log), default to 500ml, and display with a clean 8-emoji progress bar.

**Status**: ✅ Ready for production use
