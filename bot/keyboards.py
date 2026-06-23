"""Inline keyboard builders for the bot."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

FOOD_CONFIRM_CALLBACK = "food_confirm"
FOOD_UNDO_CALLBACK    = "food_undo"
WATER_CONFIRM_CALLBACK = "water_confirm"
WATER_UNDO_CALLBACK    = "water_undo"


def food_confirm_keyboard() -> InlineKeyboardMarkup:
    """Submit / Undo buttons shown after a food preview."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Log it", callback_data=FOOD_CONFIRM_CALLBACK),
            InlineKeyboardButton("❌ Discard", callback_data=FOOD_UNDO_CALLBACK),
        ]
    ])


def water_confirm_keyboard() -> InlineKeyboardMarkup:
    """Submit / Undo buttons shown after a water preview."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Log it", callback_data=WATER_CONFIRM_CALLBACK),
            InlineKeyboardButton("❌ Discard", callback_data=WATER_UNDO_CALLBACK),
        ]
    ])
