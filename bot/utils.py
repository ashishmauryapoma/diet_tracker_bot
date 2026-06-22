"""
Telegram message formatting helpers.

Centralizing formatting here keeps handlers.py focused on control flow, and
makes it easy to tweak the look of bot replies in one place. All messages
use Telegram's HTML parse mode.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any

from bot.models import DailySummaryData, NutritionInfo

# ---------------------------------------------------------------------------
# Water intent detection — fast regex (no API call needed for obvious cases)
# ---------------------------------------------------------------------------

# Keywords that strongly suggest the user is talking about water / hydration
_WATER_KEYWORDS = re.compile(
    r"\b(water|drink|drank|gulp|sip|bottle|glass|hydrat|h2o|fluid|aqua)\b",
    re.IGNORECASE,
)

# Extract a numeric amount + optional unit (ml / l / litre / liter / glass / cup)
_AMOUNT_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*"          # number (integer or decimal)
    r"(ml|milliliter|millilitre|"   # ml variants
    r"l|liter|litre|"               # litre variants
    r"glass(?:es)?|cup(?:s)?|"      # glass / cup
    r"bottle(?:s)?)",               # bottle
    re.IGNORECASE,
)

# Approximate conversions to ml
_UNIT_TO_ML: dict[str, int] = {
    "ml": 1,
    "milliliter": 1,
    "millilitre": 1,
    "l": 1000,
    "liter": 1000,
    "litre": 1000,
    "glass": 250,
    "glasses": 250,
    "cup": 240,
    "cups": 240,
    "bottle": 500,
    "bottles": 500,
}


def try_parse_water_ml(text: str) -> int | None:
    """
    Try to extract a water amount (in ml) from plain text using regex only.

    Returns the amount in ml if confident the user is describing water intake,
    otherwise returns None so the caller can fall back to the AI.

    Examples that match:
        "drank 250ml water"        → 250
        "drink 2 glasses of water" → 500
        "had 1.5 liters of water"  → 1500
        "500ml water"              → 500
        "water 300"                → 300  (bare number with water keyword)

    Examples that return None:
        "2 eggs and 1 banana"      → None  (food, not water)
        "how much water should I drink?" → None  (no amount)
    """
    if not _WATER_KEYWORDS.search(text):
        return None  # doesn't mention water at all

    match = _AMOUNT_PATTERN.search(text)
    if match:
        amount = float(match.group(1))
        unit = match.group(2).lower().rstrip("s")  # normalise plural → singular
        # Re-add 's' for units whose key includes it
        unit_key = match.group(2).lower()
        multiplier = _UNIT_TO_ML.get(unit_key) or _UNIT_TO_ML.get(unit, 1)
        ml = int(amount * multiplier)
        if 1 <= ml <= 5000:
            return ml
        return None  # out of validation range — let AI handle it

    # Water keyword present but no recognised unit — look for a bare integer
    bare = re.search(r"\b(\d{2,4})\b", text)  # 2-4 digit number (50–9999)
    if bare:
        ml = int(bare.group(1))
        if 50 <= ml <= 5000:
            return ml

    return None


def format_food_confirmation(entry: NutritionInfo) -> str:
    meal_emoji = {
        "breakfast": "🍳",
        "lunch": "🍛",
        "dinner": "🍽",
        "snack": "🍿",
    }.get(entry.meal_type, "🍽")

    return (
        f"{meal_emoji} <b>Logged: {escape(entry.food)}</b>\n"
        f"<i>Serving: {escape(entry.serving_size)}  •  {entry.meal_type.title()}</i>\n\n"
        f"Calories: <b>{entry.calories:.0f} kcal</b>\n"
        f"Protein: {entry.protein:.1f} g\n"
        f"Carbs: {entry.carbs:.1f} g\n"
        f"Fat: {entry.fat:.1f} g\n"
        f"Fiber: {entry.fiber:.1f} g"
    )


def format_water_confirmation(amount_ml: int, total_today_ml: int, goal_ml: int) -> str:
    liters = total_today_ml / 1000
    goal_liters = goal_ml / 1000
    remaining = max(goal_ml - total_today_ml, 0)
    progress = min(total_today_ml / goal_ml, 1.0) if goal_ml else 0
    filled = int(progress * 10)
    bar = "🟦" * filled + "⬜" * (10 - filled)

    lines = [
        f"💧 <b>+{amount_ml} ml logged</b>",
        "",
        f"{bar}",
        f"Today: <b>{liters:.2f} L</b> / {goal_liters:.2f} L goal",
    ]
    if remaining > 0:
        lines.append(f"Remaining: {remaining} ml")
    else:
        lines.append("🎉 Goal reached for today!")
    return "\n".join(lines)


def format_daily_summary(summary: DailySummaryData, water_goal_ml: int, calorie_goal: int) -> str:
    water_liters = summary.water_ml / 1000
    water_goal_liters = water_goal_ml / 1000

    calorie_note = ""
    if calorie_goal:
        diff = summary.total_calories - calorie_goal
        if abs(diff) < 50:
            calorie_note = " (on target)"
        elif diff > 0:
            calorie_note = f" (+{diff:.0f} over goal)"
        else:
            calorie_note = f" ({abs(diff):.0f} under goal)"

    return (
        "📊 <b>Daily Summary</b>\n\n"
        f"Calories: <b>{summary.total_calories:.0f} kcal</b>{calorie_note}\n"
        f"Protein: {summary.total_protein:.1f} g\n"
        f"Carbs: {summary.total_carbs:.1f} g\n"
        f"Fat: {summary.total_fat:.1f} g\n"
        f"Fiber: {summary.total_fiber:.1f} g\n\n"
        f"Water: {water_liters:.2f} L / {water_goal_liters:.2f} L\n\n"
        f"Meals Logged: {summary.meals_logged}"
    )


def format_today_message(food_rows: list[dict[str, Any]], water_ml: int) -> str:
    if not food_rows:
        return "📋 <b>Today's Log</b>\n\nNo meals logged yet today. Send me what you're eating!"

    meal_order = ["breakfast", "lunch", "dinner", "snack"]
    meal_emoji = {"breakfast": "🍳", "lunch": "🍛", "dinner": "🍽", "snack": "🍿"}

    grouped: dict[str, list[dict[str, Any]]] = {m: [] for m in meal_order}
    for row in food_rows:
        meal_type = str(row.get("meal_type", "snack")).lower()
        if meal_type not in grouped:
            meal_type = "snack"
        grouped[meal_type].append(row)

    lines = ["📋 <b>Today's Log</b>\n"]
    total_calories = 0.0
    for meal_type in meal_order:
        items = grouped[meal_type]
        if not items:
            continue
        lines.append(f"{meal_emoji[meal_type]} <b>{meal_type.title()}:</b>")
        for item in items:
            calories = float(item.get("calories", 0) or 0)
            total_calories += calories
            lines.append(f"  • {escape(str(item.get('food', 'Unknown')))} ({calories:.0f} kcal)")
        lines.append("")

    lines.append(f"💧 Water: {water_ml / 1000:.2f} L")
    lines.append(f"<b>Total Calories: {total_calories:.0f}</b>")
    return "\n".join(lines)


def format_analysis_message(analysis: dict[str, Any]) -> str:
    score = analysis.get("nutrition_score", "N/A")
    lines = [f"🧠 <b>AI Daily Analysis</b>\n", f"Nutrition Score: <b>{score}/10</b>\n"]

    protein_adequacy = analysis.get("protein_adequacy")
    hydration_status = analysis.get("hydration_status")
    calorie_balance = analysis.get("calorie_balance")
    if protein_adequacy or hydration_status or calorie_balance:
        if protein_adequacy:
            lines.append(f"Protein: {escape(str(protein_adequacy))}")
        if hydration_status:
            lines.append(f"Hydration: {escape(str(hydration_status))}")
        if calorie_balance:
            lines.append(f"Calorie balance: {escape(str(calorie_balance))}")
        lines.append("")

    strengths = analysis.get("strengths") or []
    if strengths:
        lines.append("<b>Strengths:</b>")
        lines.extend(f"✅ {escape(str(s))}" for s in strengths)
        lines.append("")

    improvements = analysis.get("improvements") or []
    if improvements:
        lines.append("<b>Improvements:</b>")
        lines.extend(f"⚠️ {escape(str(s))}" for s in improvements)
        lines.append("")

    missing = analysis.get("missing_nutrients") or []
    if missing:
        lines.append("<b>Possibly missing:</b>")
        lines.extend(f"• {escape(str(s))}" for s in missing)
        lines.append("")

    recommendation = analysis.get("recommendation")
    if recommendation:
        lines.append("<b>Recommendation:</b>")
        lines.append(escape(str(recommendation)))

    return "\n".join(lines).strip()


def format_help_message() -> str:
    return (
        "🥗 <b>Diet Tracker Bot</b>\n\n"
        "Just send me what you ate in plain English and I'll log the nutrition automatically, e.g.:\n"
        "<i>2 eggs and 1 banana</i>\n"
        "<i>Chicken biryani 300g</i>\n\n"
        "You can also log water in plain text:\n"
        "<i>drank 500ml water</i>\n"
        "<i>2 glasses of water</i>\n"
        "<i>had 1 litre of water</i>\n\n"
        "<b>Commands:</b>\n"
        "/water - Log water intake (with quick-add buttons)\n"
        "/summary - Today's nutrition summary\n"
        "/analyze - AI analysis of today's diet\n"
        "/today - See everything logged today\n"
        "/help - Show this message"
    )


def format_welcome_message() -> str:
    return (
        "👋 Welcome to your Diet Tracker Bot!\n\n"
        "You're authenticated. Send me any food you eat (e.g. <i>'2 eggs and toast'</i>) "
        "and I'll log its nutrition automatically.\n\n"
        "Type /help to see all available commands."
    )
