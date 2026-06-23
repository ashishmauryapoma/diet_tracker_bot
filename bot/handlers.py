"""
Telegram update handlers: commands, free-text food logging, keyboard callbacks.
"""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from bot.auth import ACCESS_DENIED_MESSAGE, authenticate, is_authenticated, require_auth
from bot.config import Config
from bot.groq_client import GroqAnalysisError, GroqNutritionClient
from bot.keyboards import (
    FOOD_CONFIRM_CALLBACK,
    FOOD_UNDO_CALLBACK,
    WATER_CONFIRM_CALLBACK,
    WATER_UNDO_CALLBACK,
    food_confirm_keyboard,
    water_confirm_keyboard,
)
from bot.models import NutritionInfo, ValidationError, WaterEntry
from bot.scheduler import schedule_reminders_for_chat
from bot.sheets_client import SheetsClient, SheetsError
from bot.utils import (
    format_analysis_message,
    format_daily_goals_with_progress,
    format_food_confirmation,
    format_help_message,
    format_today_message,
    format_water_confirmation,
    format_welcome_message,
    try_parse_water_ml,
)

logger = logging.getLogger(__name__)

_PENDING_FOOD_KEY = "pending_food"
_PENDING_FOOD_GLOBAL_KEY = "pending_food_global"
_PENDING_WATER_KEY = "pending_water"
_PENDING_WATER_GLOBAL_KEY = "pending_water_global"

GENERIC_ERROR_MESSAGE = (
    "⚠️ Something went wrong while processing that. "
    "I've logged the error — please try again in a moment."
)


def _services(context: ContextTypes.DEFAULT_TYPE) -> tuple[Config, GroqNutritionClient, SheetsClient]:
    bot_data = context.application.bot_data
    return bot_data["config"], bot_data["groq_client"], bot_data["sheets_client"]


def _today_str(config: Config) -> str:
    return datetime.now(config.tzinfo).strftime("%Y-%m-%d")


def _now(config: Config) -> datetime:
    return datetime.now(config.tzinfo)


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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat
    if user is None or message is None or chat is None or not message.text:
        return

    text = message.text.strip()

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

    if len(text) < 2:
        await message.reply_text(
            "⚠️ Please enter a valid food item."
        )
        return
    if len(text) > 500:
        await message.reply_text(
            "⚠️ Entry is too long. Please keep it under 500 characters."
        )
        return

    # Check if user just sent "water" or "1 glass" alone - default to 500ml
    if text.lower() in ["water", "1 glass"]:
        water_ml = 500
    else:
        # Try to detect water entry first (fast regex, no API call)
        water_ml = try_parse_water_ml(text)
    
    if water_ml is not None:
        try:
            water_entry = WaterEntry(amount_ml=water_ml, logged_at=_now(config))
        except ValidationError as exc:
            await message.reply_text(f"⚠️ {exc}")
            return

        # Get current water total for preview
        date_str = _today_str(config)
        try:
            total_water_ml = await sheets_client.get_today_water_total(date_str)
        except SheetsError:
            total_water_ml = 0

        preview_text = (
            f"{format_water_confirmation(water_ml, total_water_ml + water_ml, config.daily_water_goal_ml)}\n\n"
            "<i>Tap ✅ Log it to save, or ❌ Discard to cancel.</i>"
        )
        sent = await message.reply_text(
            preview_text,
            parse_mode=ParseMode.HTML,
            reply_markup=water_confirm_keyboard(),
        )

        pending: dict = context.user_data.setdefault(_PENDING_WATER_KEY, {})
        pending[sent.message_id] = water_entry

        global_pending: dict = context.application.bot_data.setdefault(_PENDING_WATER_GLOBAL_KEY, {})
        global_pending[sent.message_id] = water_entry

        logger.debug("Pending water entry stored (msg_id=%d): %d ml", sent.message_id, water_ml)
        return

    # Not water, proceed with food detection
    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)

    intent = await groq_client.classify_intent(text)
    if intent == "unknown":
        await message.reply_text(
            "⚠️ I couldn't recognize that as a food entry.\n\n"
            "Type /help for available commands.",
            parse_mode=ParseMode.HTML,
        )
        return

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

    preview_text = (
        f"{format_food_confirmation(nutrition)}\n\n"
        "<i>Tap ✅ Log it to save, or ❌ Discard to cancel.</i>"
    )
    sent = await message.reply_text(
        preview_text,
        parse_mode=ParseMode.HTML,
        reply_markup=food_confirm_keyboard(),
    )

    pending: dict = context.user_data.setdefault(_PENDING_FOOD_KEY, {})
    pending[sent.message_id] = nutrition

    global_pending: dict = context.application.bot_data.setdefault(_PENDING_FOOD_GLOBAL_KEY, {})
    global_pending[sent.message_id] = nutrition

    logger.debug("Pending food entry stored (msg_id=%d): %s", sent.message_id, nutrition.food)


async def food_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None or query.message is None:
        return

    if not is_authenticated(query.from_user.id, context):
        await query.answer(ACCESS_DENIED_MESSAGE, show_alert=True)
        return

    await query.answer()

    msg_id = query.message.message_id

    pending: dict = context.user_data.get(_PENDING_FOOD_KEY, {})
    nutrition: NutritionInfo | None = pending.pop(msg_id, None)

    global_pending: dict = context.application.bot_data.get(_PENDING_FOOD_GLOBAL_KEY, {})
    if nutrition is None:
        nutrition = global_pending.pop(msg_id, None)
    else:
        global_pending.pop(msg_id, None)

    logger.debug(
        "food_confirm_callback: data=%r msg_id=%d pending_keys=%s nutrition=%s",
        query.data, msg_id, list(pending.keys()), nutrition,
    )

    if query.data == FOOD_UNDO_CALLBACK:
        try:
            body = format_food_confirmation(nutrition) if nutrition else "Entry"
            await query.edit_message_text(
                f"{body}\n\n<i>❌ Entry discarded.</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.warning("Could not edit message on discard: %s", exc)
        logger.info("Food entry discarded by user (msg_id=%d).", msg_id)
        return

    if nutrition is None:
        await query.edit_message_text(
            "⚠️ Could not find this entry — the bot may have restarted. "
            "Please re-send your food message to log it again.",
            parse_mode=ParseMode.HTML,
        )
        return

    config, _groq, sheets_client = _services(context)
    try:
        await sheets_client.append_food_log(nutrition)
        await sheets_client.refresh_daily_summary(_today_str(config))
    except SheetsError as exc:
        logger.error("Failed to save confirmed food entry: %s", exc, exc_info=True)
        try:
            await query.edit_message_text(
                f"{format_food_confirmation(nutrition)}\n\n"
                "⚠️ Couldn't write to Google Sheets right now. Please try again shortly.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    try:
        await query.edit_message_text(
            f"{format_food_confirmation(nutrition)}\n\n"
            "✅ <b>Logged to your sheet!</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.warning("Could not edit message after confirm: %s", exc)

    logger.info(
        "Food entry confirmed and saved: %s (%.0f kcal)", nutrition.food, nutrition.calories
    )


async def water_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None or query.message is None:
        return

    if not is_authenticated(query.from_user.id, context):
        await query.answer(ACCESS_DENIED_MESSAGE, show_alert=True)
        return

    await query.answer()

    msg_id = query.message.message_id

    pending: dict = context.user_data.get(_PENDING_WATER_KEY, {})
    water: WaterEntry | None = pending.pop(msg_id, None)

    global_pending: dict = context.application.bot_data.get(_PENDING_WATER_GLOBAL_KEY, {})
    if water is None:
        water = global_pending.pop(msg_id, None)
    else:
        global_pending.pop(msg_id, None)

    logger.debug(
        "water_confirm_callback: data=%r msg_id=%d pending_keys=%s water=%s",
        query.data, msg_id, list(pending.keys()), water,
    )

    if query.data == WATER_UNDO_CALLBACK:
        try:
            await query.edit_message_text(
                f"💧 Entry discarded.",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.warning("Could not edit message on discard: %s", exc)
        logger.info("Water entry discarded by user (msg_id=%d).", msg_id)
        return

    if water is None:
        await query.edit_message_text(
            "⚠️ Could not find this entry — the bot may have restarted. "
            "Please re-send your water message to log it again.",
            parse_mode=ParseMode.HTML,
        )
        return

    config, _groq, sheets_client = _services(context)
    try:
        await sheets_client.append_water_log(water)
        await sheets_client.refresh_daily_summary(_today_str(config))
    except SheetsError as exc:
        logger.error("Failed to save confirmed water entry: %s", exc, exc_info=True)
        try:
            await query.edit_message_text(
                f"💧 +{water.amount_ml} ml\n\n"
                "⚠️ Couldn't write to Google Sheets right now. Please try again shortly.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    try:
        await query.edit_message_text(
            f"💧 <b>+{water.amount_ml} ml logged to your sheet!</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.warning("Could not edit message after water confirm: %s", exc)

    logger.info("Water entry confirmed and saved: %d ml", water.amount_ml)


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
        await message.reply_text(
            "⚠️ Couldn't reach Google Sheets right now. Please try /analyze again shortly."
        )
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
            "⚠️ I couldn't generate today's analysis right now. Please try again shortly."
        )
        return

    await message.reply_text(format_analysis_message(analysis), parse_mode=ParseMode.HTML)


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
        await message.reply_text(
            "⚠️ Couldn't reach Google Sheets right now. Please try /today again shortly."
        )
        return

    await message.reply_text(format_today_message(food_rows, water_ml), parse_mode=ParseMode.HTML)


@require_auth
async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    config, _groq, sheets_client = _services(context)
    try:
        summary = await sheets_client.compute_today_summary(_today_str(config))
    except SheetsError:
        await message.reply_text(
            "⚠️ Couldn't reach Google Sheets right now. Please try /goal again shortly."
        )
        return

    await message.reply_text(
        format_daily_goals_with_progress(
            summary,
            config.daily_water_goal_ml,
            config.daily_calorie_goal,
            config.daily_protein_goal_g,
            config.daily_carbs_goal_g,
            config.daily_fat_goal_g,
            config.daily_fiber_goal_g,
        ),
        parse_mode=ParseMode.HTML,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(
        "Unhandled exception while processing update: %s",
        context.error,
        exc_info=context.error,
    )
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(GENERIC_ERROR_MESSAGE)
        except Exception:
            logger.exception("Failed to notify user about an unhandled error.")
