"""
Telegram update handlers: commands, free-text food logging, and inline
keyboard callbacks (water quick-add buttons).

All handlers are async and use the services stored in
`context.application.bot_data` (config, GroqNutritionClient, SheetsClient),
which are wired up once at startup in main.py.
"""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import CallbackQuery, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from bot.auth import ACCESS_DENIED_MESSAGE, authenticate, is_authenticated, require_auth
from bot.config import Config
from bot.groq_client import GroqAnalysisError, GroqNutritionClient
from bot.keyboards import WATER_CALLBACK_PREFIX, WATER_CUSTOM_CALLBACK, water_quick_add_keyboard
from bot.models import ValidationError, WaterEntry
from bot.scheduler import schedule_reminders_for_chat
from bot.sheets_client import SheetsClient, SheetsError
from bot.utils import (
    format_analysis_message,
    format_daily_summary,
    format_food_confirmation,
    format_help_message,
    format_today_message,
    format_water_confirmation,
    format_welcome_message,
)

logger = logging.getLogger(__name__)

_AWAITING_WATER_CUSTOM = "awaiting_water_custom"

GENERIC_ERROR_MESSAGE = (
    "⚠️ Something went wrong while processing that. I've logged the error — please try again in a moment."
)


def _services(context: ContextTypes.DEFAULT_TYPE) -> tuple[Config, GroqNutritionClient, SheetsClient]:
    bot_data = context.application.bot_data
    return bot_data["config"], bot_data["groq_client"], bot_data["sheets_client"]


def _today_str(config: Config) -> str:
    return datetime.now(config.tzinfo).strftime("%Y-%m-%d")


def _now(config: Config) -> datetime:
    return datetime.now(config.tzinfo)


# --------------------------------------------------------------------------- #
# /start and authentication
# --------------------------------------------------------------------------- #


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None or update.effective_message is None:
        return

    if is_authenticated(user.id, context):
        await update.effective_message.reply_text(
            format_welcome_message(), parse_mode=ParseMode.HTML
        )
        return

    logger.info("New /start from user_id=%s, awaiting password.", user.id)
    await update.effective_message.reply_text(
        "🔒 Welcome to Diet Tracker Bot. Please enter the password to continue."
    )


@require_auth
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(format_help_message(), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# Free-text handling: password entry, water custom amount, or food logging
# --------------------------------------------------------------------------- #


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat
    if user is None or message is None or chat is None or not message.text:
        return

    text = message.text.strip()

    # --- Unauthenticated: treat any text as a password attempt ---
    if not is_authenticated(user.id, context):
        config, *_ = _services(context)
        if text == config.auth_password:
            authenticate(user.id, chat.id, context)
            await message.reply_text(format_welcome_message(), parse_mode=ParseMode.HTML)
            schedule_reminders_for_chat(context.application, chat.id, config)
        else:
            logger.warning("Failed password attempt from user_id=%s", user.id)
            await message.reply_text(ACCESS_DENIED_MESSAGE)
        return

    config, groq_client, sheets_client = _services(context)

    # --- Authenticated user is mid-flow entering a custom water amount ---
    if context.user_data.get(_AWAITING_WATER_CUSTOM):
        context.user_data[_AWAITING_WATER_CUSTOM] = False
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            await message.reply_text(
                "That doesn't look like a number. Please send the amount in ml, e.g. 350"
            )
            context.user_data[_AWAITING_WATER_CUSTOM] = True
            return
        await _log_water(int(digits), update, context)
        return

    # --- Otherwise: treat as a food log entry ---
    if len(text) < 2:
        await message.reply_text("Please describe what you ate, e.g. '2 eggs and 1 banana'.")
        return
    if len(text) > 500:
        await message.reply_text("That description is too long (max 500 characters). Please shorten it.")
        return

    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)

    try:
        nutrition = await groq_client.analyze_food(text, logged_at=_now(config))
    except ValidationError as exc:
        await message.reply_text(f"⚠️ {exc}")
        return
    except GroqAnalysisError as exc:
        logger.error("Food analysis failed for %r: %s", text, exc, exc_info=True)
        await message.reply_text(
            "⚠️ I couldn't analyze that food entry right now (AI service unavailable). "
            "Please try again in a moment."
        )
        return

    try:
        await sheets_client.append_food_log(nutrition)
        await sheets_client.refresh_daily_summary(_today_str(config))
    except SheetsError as exc:
        logger.error("Failed to persist food entry: %s", exc, exc_info=True)
        await message.reply_text(
            f"{format_food_confirmation(nutrition)}\n\n"
            "⚠️ Note: I couldn't save this to Google Sheets due to a connection issue. "
            "The values above were calculated but not stored."
        )
        return

    await message.reply_text(format_food_confirmation(nutrition), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# /water
# --------------------------------------------------------------------------- #


@require_auth
async def water_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    if context.args:
        digits = "".join(ch for ch in context.args[0] if ch.isdigit())
        if not digits:
            await message.reply_text("Usage: /water <amount in ml>, e.g. /water 500")
            return
        await _log_water(int(digits), update, context)
        return

    await message.reply_text(
        "💧 How much water would you like to log?", reply_markup=water_quick_add_keyboard()
    )


async def water_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return

    if not is_authenticated(query.from_user.id, context):
        await query.answer(ACCESS_DENIED_MESSAGE, show_alert=True)
        return

    await query.answer()

    if query.data == WATER_CUSTOM_CALLBACK:
        context.user_data[_AWAITING_WATER_CUSTOM] = True
        if query.message is not None:
            await query.edit_message_text("✏️ Send the amount in ml (e.g. 350).")
        return

    if query.data.startswith(WATER_CALLBACK_PREFIX):
        amount_str = query.data.removeprefix(WATER_CALLBACK_PREFIX)
        try:
            amount_ml = int(amount_str)
        except ValueError:
            return
        await _log_water(amount_ml, update, context, edit_query=query)


async def _log_water(
    amount_ml: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit_query: CallbackQuery | None = None,
) -> None:
    config, _groq, sheets_client = _services(context)
    message = update.effective_message

    try:
        entry = WaterEntry(amount_ml=amount_ml, logged_at=_now(config))
    except ValidationError as exc:
        text = f"⚠️ {exc}"
        if edit_query is not None:
            await edit_query.edit_message_text(text)
        elif message is not None:
            await message.reply_text(text)
        return

    try:
        await sheets_client.append_water_log(entry)
        summary = await sheets_client.refresh_daily_summary(_today_str(config))
    except SheetsError as exc:
        logger.error("Failed to persist water entry: %s", exc, exc_info=True)
        text = "⚠️ Couldn't save your water log right now due to a connection issue. Please try again shortly."
        if edit_query is not None:
            await edit_query.edit_message_text(text)
        elif message is not None:
            await message.reply_text(text)
        return

    confirmation = format_water_confirmation(amount_ml, summary.water_ml, config.daily_water_goal_ml)
    if edit_query is not None:
        await edit_query.edit_message_text(confirmation, parse_mode=ParseMode.HTML)
    elif message is not None:
        await message.reply_text(confirmation, parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# /summary
# --------------------------------------------------------------------------- #


@require_auth
async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    config, _groq, sheets_client = _services(context)
    try:
        summary = await sheets_client.compute_today_summary(_today_str(config))
    except SheetsError:
        await message.reply_text(
            "⚠️ Couldn't reach Google Sheets right now. Please try /summary again shortly."
        )
        return

    await message.reply_text(
        format_daily_summary(summary, config.daily_water_goal_ml, config.daily_calorie_goal),
        parse_mode=ParseMode.HTML,
    )


# --------------------------------------------------------------------------- #
# /analyze
# --------------------------------------------------------------------------- #


@require_auth
async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    config, groq_client, sheets_client = _services(context)
    date_str = _today_str(config)

    try:
        food_rows = await sheets_client.get_today_food_rows(date_str)
        water_ml = await sheets_client.get_today_water_total(date_str)
    except SheetsError:
        await message.reply_text("⚠️ Couldn't reach Google Sheets right now. Please try /analyze again shortly.")
        return

    if not food_rows:
        await message.reply_text("📭 No meals logged today yet — nothing to analyze yet!")
        return

    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)

    try:
        analysis = await groq_client.analyze_day(
            food_rows,
            water_ml=water_ml,
            calorie_goal=config.daily_calorie_goal,
            water_goal_ml=config.daily_water_goal_ml,
        )
    except (GroqAnalysisError, ValidationError) as exc:
        logger.error("Daily analysis failed: %s", exc, exc_info=True)
        await message.reply_text(
            "⚠️ I couldn't generate today's analysis right now (AI service unavailable). Please try again shortly."
        )
        return

    await message.reply_text(format_analysis_message(analysis), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# /today
# --------------------------------------------------------------------------- #


@require_auth
async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    config, _groq, sheets_client = _services(context)
    date_str = _today_str(config)

    try:
        food_rows = await sheets_client.get_today_food_rows(date_str)
        water_ml = await sheets_client.get_today_water_total(date_str)
    except SheetsError:
        await message.reply_text("⚠️ Couldn't reach Google Sheets right now. Please try /today again shortly.")
        return

    await message.reply_text(format_today_message(food_rows, water_ml), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# Error handler
# --------------------------------------------------------------------------- #


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception while processing update: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(GENERIC_ERROR_MESSAGE)
        except Exception:  # noqa: BLE001 - last-resort logging, never raise from error handler
            logger.exception("Failed to notify user about an unhandled error.")
