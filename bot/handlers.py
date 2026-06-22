"""
Telegram update handlers: commands, free-text food/water logging, inline
keyboard callbacks, and the food confirm/undo flow.

Flow for plain-text food entries
---------------------------------
1. Classify intent — unknown text gets a friendly "I don't understand" reply.
2. Water detection (regex → AI fallback) logs water immediately.
3. Food entries: Groq analyses the text, shows a preview with
   [✅ Log it] [❌ Discard] buttons.  The entry is only written to Sheets
   when the user taps "Log it".  "Discard" silently removes the buttons.

Pending food entries are stored in context.user_data keyed by the
preview message_id so multiple rapid entries don't interfere with each other.
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
from bot.keyboards import (
    FOOD_CONFIRM_CALLBACK,
    FOOD_UNDO_CALLBACK,
    WATER_CALLBACK_PREFIX,
    WATER_CUSTOM_CALLBACK,
    WATER_CONFIRM_CALLBACK,
    WATER_UNDO_CALLBACK,
    food_confirm_keyboard,
    water_confirm_keyboard,
    water_quick_add_keyboard,
)
from bot.models import NutritionInfo, ValidationError, WaterEntry
from bot.scheduler import schedule_reminders_for_chat
from bot.sheets_client import SheetsClient, SheetsError
from bot.utils import (
    format_analysis_message,
    format_daily_summary,
    format_daily_goals_with_progress,
    format_food_confirmation,
    format_help_message,
    format_nutritional_goals,
    format_today_message,
    format_water_confirmation,
    format_welcome_message,
    try_parse_water_ml,
)

logger = logging.getLogger(__name__)

_AWAITING_WATER_CUSTOM = "awaiting_water_custom"
# Maps preview message_id → NutritionInfo (pending confirmation)
# Stored in BOTH user_data (primary) and bot_data (fallback for callback context)
_PENDING_FOOD_KEY = "pending_food"
_PENDING_FOOD_GLOBAL_KEY = "pending_food_global"

# Maps preview message_id → WaterEntry (pending confirmation)
_PENDING_WATER_KEY = "pending_water"
_PENDING_WATER_GLOBAL_KEY = "pending_water_global"

_WATER_HINT_WORDS = (
    "water", "drink", "drank", "glass", "bottle", "fluid",
    "hydrat", "h2o", "sip", "gulp", "aqua", "litre", "liter",
)

GENERIC_ERROR_MESSAGE = (
    "⚠️ Something went wrong while processing that. "
    "I've logged the error — please try again in a moment."
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

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
# Free-text handler
# --------------------------------------------------------------------------- #

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat
    if user is None or message is None or chat is None or not message.text:
        return

    text = message.text.strip()

    # ── Unauthenticated ────────────────────────────────────────────────────
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

    # ── Mid-flow: awaiting custom water amount ─────────────────────────────
    if context.user_data.get(_AWAITING_WATER_CUSTOM):
        context.user_data[_AWAITING_WATER_CUSTOM] = False
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            await message.reply_text(
                "That doesn't look like a number. Please send the amount in ml, e.g. 350"
            )
            context.user_data[_AWAITING_WATER_CUSTOM] = True
            return
        await _log_water_with_preview(int(digits), update, context)
        return

    # ── Basic guards ───────────────────────────────────────────────────────
    if len(text) < 2:
        await message.reply_text(
            "Please describe what you ate or drank, e.g. '2 eggs' or 'drank 500ml water'."
        )
        return
    if len(text) > 500:
        await message.reply_text(
            "That description is too long (max 500 characters). Please shorten it."
        )
        return

    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)

    # ── Step 1: fast regex water check (zero API calls) ────────────────────
    water_ml_fast = try_parse_water_ml(text)
    if water_ml_fast is not None:
        logger.info("Water detected via regex: %d ml from %r", water_ml_fast, text)
        await _log_water_with_preview(water_ml_fast, update, context)
        return

    # ── Step 2: intent classification — is this food/water or something else?
    intent = await groq_client.classify_intent(text)
    if intent == "unknown":
        await message.reply_text(
            "🤔 I didn't quite catch that as a food or drink entry.\n\n"
            "Try something like:\n"
            "  • <i>2 eggs and toast</i>\n"
            "  • <i>Chicken biryani 300g</i>\n"
            "  • <i>Drank 500ml water</i>\n\n"
            "Type /help to see all commands.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Step 3: AI water-intent check (for water text that passed regex) ───
    if any(w in text.lower() for w in _WATER_HINT_WORDS):
        try:
            is_water, ai_water_ml = await groq_client.detect_water_intake(text)
        except Exception:  # noqa: BLE001
            is_water, ai_water_ml = False, 0

        if is_water and ai_water_ml > 0:
            logger.info("Water detected via AI: %d ml from %r", ai_water_ml, text)
            await _log_water_with_preview(ai_water_ml, update, context)
            return

    # ── Step 4: food analysis → show preview + confirm/undo buttons ────────
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

    # Store pending entry in user_data AND bot_data so the callback can find it
    # regardless of which context dict PTB resolves for the callback query.
    pending: dict = context.user_data.setdefault(_PENDING_FOOD_KEY, {})
    pending[sent.message_id] = nutrition

    global_pending: dict = context.application.bot_data.setdefault(_PENDING_FOOD_GLOBAL_KEY, {})
    global_pending[sent.message_id] = nutrition

    logger.debug("Pending food entry stored (msg_id=%d): %s", sent.message_id, nutrition.food)


# --------------------------------------------------------------------------- #
# Food confirm / undo callback
# --------------------------------------------------------------------------- #

async def food_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None or query.message is None:
        return

    if not is_authenticated(query.from_user.id, context):
        await query.answer(ACCESS_DENIED_MESSAGE, show_alert=True)
        return

    await query.answer()

    # Retrieve the pending NutritionInfo stored at preview time.
    # Check user_data first (primary), then fall back to bot_data (global store).
    msg_id = query.message.message_id

    pending: dict = context.user_data.get(_PENDING_FOOD_KEY, {})
    nutrition: NutritionInfo | None = pending.pop(msg_id, None)

    # Fallback: check global bot_data store
    global_pending: dict = context.application.bot_data.get(_PENDING_FOOD_GLOBAL_KEY, {})
    if nutrition is None:
        nutrition = global_pending.pop(msg_id, None)
    else:
        global_pending.pop(msg_id, None)  # keep in sync

    logger.debug(
        "food_confirm_callback: data=%r msg_id=%d pending_keys=%s nutrition=%s",
        query.data, msg_id, list(pending.keys()), nutrition,
    )

    # ── Discard ─────────────────────────────────────────────────────────────
    if query.data == FOOD_UNDO_CALLBACK:
        try:
            body = format_food_confirmation(nutrition) if nutrition else "Entry"
            await query.edit_message_text(
                f"{body}\n\n<i>❌ Entry discarded.</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not edit message on discard: %s", exc)
        logger.info("Food entry discarded by user (msg_id=%d).", msg_id)
        return

    # ── Confirm ─────────────────────────────────────────────────────────────
    if nutrition is None:
        # Entry was already saved or bot restarted (user_data cleared on restart)
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
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        await query.edit_message_text(
            f"{format_food_confirmation(nutrition)}\n\n"
            "✅ <b>Logged to your sheet!</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not edit message after confirm: %s", exc)

    logger.info(
        "Food entry confirmed and saved: %s (%.0f kcal)", nutrition.food, nutrition.calories
    )


# --------------------------------------------------------------------------- #
# Water confirm / undo callback
# --------------------------------------------------------------------------- #

async def water_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None or query.message is None:
        return

    if not is_authenticated(query.from_user.id, context):
        await query.answer(ACCESS_DENIED_MESSAGE, show_alert=True)
        return

    await query.answer()

    # Retrieve the pending WaterEntry stored at preview time.
    # Check user_data first (primary), then fall back to bot_data (global store).
    msg_id = query.message.message_id

    pending: dict = context.user_data.get(_PENDING_WATER_KEY, {})
    entry: WaterEntry | None = pending.pop(msg_id, None)

    # Fallback: check global bot_data store
    global_pending: dict = context.application.bot_data.get(_PENDING_WATER_GLOBAL_KEY, {})
    if entry is None:
        entry = global_pending.pop(msg_id, None)
    else:
        global_pending.pop(msg_id, None)  # keep in sync

    logger.debug("water_confirm_callback: data=%r msg_id=%d entry=%s", query.data, msg_id, entry)

    # ── Discard ─────────────────────────────────────────────────────────────
    if query.data == WATER_UNDO_CALLBACK:
        try:
            await query.edit_message_text(
                "💧 Entry\n\n<i>❌ Entry discarded.</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not edit message on water discard: %s", exc)
        logger.info("Water entry discarded by user (msg_id=%d).", msg_id)
        return

    # ── Confirm ─────────────────────────────────────────────────────────────
    if entry is None:
        await query.edit_message_text(
            "⚠️ Could not find this entry — the bot may have restarted. "
            "Please re-send your water message to log it again.",
            parse_mode=ParseMode.HTML,
        )
        return

    config, _groq, sheets_client = _services(context)
    try:
        await sheets_client.append_water_log(entry)
        summary = await sheets_client.refresh_daily_summary(_today_str(config))
    except SheetsError as exc:
        logger.error("Failed to save confirmed water entry: %s", exc, exc_info=True)
        try:
            await query.edit_message_text(
                f"💧 +{entry.amount_ml} ml\n\n"
                "⚠️ Couldn't write to Google Sheets right now. Please try again shortly.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:  # noqa: BLE001
            pass
        return

    confirmation = format_water_confirmation(entry.amount_ml, summary.water_ml, config.daily_water_goal_ml)
    try:
        await query.edit_message_text(
            f"{confirmation}\n\n✅ <b>Logged to your sheet!</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not edit message after water confirm: %s", exc)

    logger.info("Water entry confirmed and saved: %d ml", entry.amount_ml)


# --------------------------------------------------------------------------- #
# /water command + callbacks
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
        await _log_water_direct(int(digits), update, context)
        return

    await message.reply_text(
        "💧 Usage: /water <amount in ml>\n\n"
        "Examples:\n"
        "  /water 250\n"
        "  /water 500\n"
        "  /water 1000\n\n"
        "Or send water in plain text:\n"
        "  drank 500ml water\n"
        "  2 glasses of water"
    )


async def water_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return

    if not is_authenticated(query.from_user.id, context):
        await query.answer(ACCESS_DENIED_MESSAGE, show_alert=True)
        return

    await query.answer()

    # ── Quick-add buttons (from /water command) ────────────────────────────
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
        await _log_water_direct(amount_ml, update, context, edit_query=query)
        return

    # ── Water confirm/undo buttons (from preview) ──────────────────────────
    # Delegate to water_confirm_callback for the confirm/undo flow
    await water_confirm_callback(update, context)


async def _log_water_with_preview(
    amount_ml: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Show a water entry preview with [✅ Log it] [❌ Discard] buttons.
    Entry is only saved when user taps Log it.
    """
    config = _services(context)[0]
    message = update.effective_message
    if message is None:
        return

    try:
        entry = WaterEntry(amount_ml=amount_ml, logged_at=_now(config))
    except ValidationError as exc:
        await message.reply_text(f"⚠️ {exc}")
        return

    preview_text = (
        f"💧 <b>+{amount_ml} ml</b>\n\n"
        "<i>Tap ✅ Log it to save, or ❌ Discard to cancel.</i>"
    )
    sent = await message.reply_text(
        preview_text,
        parse_mode=ParseMode.HTML,
        reply_markup=water_confirm_keyboard(),
    )

    # Store pending entry in user_data AND bot_data
    pending: dict = context.user_data.setdefault(_PENDING_WATER_KEY, {})
    pending[sent.message_id] = entry

    global_pending: dict = context.application.bot_data.setdefault(_PENDING_WATER_GLOBAL_KEY, {})
    global_pending[sent.message_id] = entry

    logger.debug("Pending water entry stored (msg_id=%d): %d ml", sent.message_id, amount_ml)


async def _log_water_direct(
    amount_ml: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit_query: CallbackQuery | None = None,
) -> None:
    """
    Directly log water without confirmation (used by /water command and callbacks).
    """
    config, _groq, sheets_client = _services(context)
    message = update.effective_message

    try:
        entry = WaterEntry(amount_ml=amount_ml, logged_at=_now(config))
    except ValidationError as exc:
        reply = f"⚠️ {exc}"
        if edit_query is not None:
            await edit_query.edit_message_text(reply)
        elif message is not None:
            await message.reply_text(reply)
        return

    try:
        await sheets_client.append_water_log(entry)
        summary = await sheets_client.refresh_daily_summary(_today_str(config))
    except SheetsError as exc:
        logger.error("Failed to persist water entry: %s", exc, exc_info=True)
        reply = "⚠️ Couldn't save your water log right now. Please try again shortly."
        if edit_query is not None:
            await edit_query.edit_message_text(reply)
        elif message is not None:
            await message.reply_text(reply)
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
        format_daily_summary(
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
        await message.reply_text(
            "⚠️ Couldn't reach Google Sheets right now. Please try /today again shortly."
        )
        return

    await message.reply_text(format_today_message(food_rows, water_ml), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# /goal
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Global error handler
# --------------------------------------------------------------------------- #

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(
        "Unhandled exception while processing update: %s",
        context.error,
        exc_info=context.error,
    )
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(GENERIC_ERROR_MESSAGE)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to notify user about an unhandled error.")
