"""Inline keyboard builders for the bot."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

WATER_CALLBACK_PREFIX = "water_add:"
WATER_CUSTOM_CALLBACK = "water_custom"

FOOD_CONFIRM_CALLBACK = "food_confirm"
FOOD_UNDO_CALLBACK    = "food_undo"


def water_quick_add_keyboard() -> InlineKeyboardMarkup:
    """Quick-add buttons for common water amounts."""
    buttons = [
        InlineKeyboardButton("250 ml", callback_data=f"{WATER_CALLBACK_PREFIX}250"),
        InlineKeyboardButton("500 ml", callback_data=f"{WATER_CALLBACK_PREFIX}500"),
        InlineKeyboardButton("750 ml", callback_data=f"{WATER_CALLBACK_PREFIX}750"),
        InlineKeyboardButton("1000 ml", callback_data=f"{WATER_CALLBACK_PREFIX}1000"),
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("✏️ Custom amount", callback_data=WATER_CUSTOM_CALLBACK)])
    return InlineKeyboardMarkup(rows)


def food_confirm_keyboard() -> InlineKeyboardMarkup:
    """Submit / Undo buttons shown after a food preview."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Log it", callback_data=FOOD_CONFIRM_CALLBACK),
            InlineKeyboardButton("❌ Discard", callback_data=FOOD_UNDO_CALLBACK),
        ]
    ])
