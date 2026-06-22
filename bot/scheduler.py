"""
Scheduled reminders and sheet-change polling.

Water reminders
---------------
Uses python-telegram-bot's built-in JobQueue (backed by APScheduler) to send
water reminders five times a day at fixed local times: 9 AM, 12 PM, 3 PM,
6 PM, and 9 PM, in the timezone configured via APP_TIMEZONE.

Reminders are (re)scheduled once a chat is authenticated. Because sessions
are in-memory only, reminders need to be re-armed on every bot restart - this
happens automatically the next time the user authenticates with /start.

Sheet poller
------------
A repeating job runs every SHEET_POLL_INTERVAL_SECONDS (default 60 s) and
compares the current row counts in Food_Log and Water_Log against a snapshot
taken at the previous tick.  Any new rows are sent to the user as Telegram
notifications, giving the impression of real-time sync from manual sheet edits.
"""

from __future__ import annotations

import logging
from datetime import datetime, time

from telegram.ext import Application, ContextTypes

from bot.auth import get_active_chat_id
from bot.config import Config
from bot.sheets_client import SheetsClient, SheetsError

logger = logging.getLogger(__name__)

WATER_REMINDER_TEXT = "💧 <b>Water Reminder</b>\n\nHave you had enough water today?"

_JOB_NAME_PREFIX = "water_reminder_"
_SHEET_POLL_JOB = "sheet_poller"
_SCHEDULED_FLAG = "reminders_scheduled_for_chat"

# Keys stored in bot_data to track last-known row counts
_FOOD_ROW_KEY = "poller_food_rows"
_WATER_ROW_KEY = "poller_water_rows"

SHEET_POLL_INTERVAL_SECONDS = 60  # how often to check for new sheet rows


# --------------------------------------------------------------------------- #
# Water reminders
# --------------------------------------------------------------------------- #

async def _send_water_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    try:
        await context.bot.send_message(chat_id=chat_id, text=WATER_REMINDER_TEXT, parse_mode="HTML")
        logger.info("Sent water reminder to chat_id=%s", chat_id)
    except Exception as exc:  # noqa: BLE001 - reminders must never crash the scheduler
        logger.error("Failed to send water reminder to chat_id=%s: %s", chat_id, exc, exc_info=True)


# --------------------------------------------------------------------------- #
# Sheet poller
# --------------------------------------------------------------------------- #

def _fmt_food_notification(row: dict) -> str:
    """Format a manually-entered food row into a Telegram notification."""
    try:
        cal = float(row.get("calories") or 0)
        prot = float(row.get("protein") or 0)
        carbs = float(row.get("carbs") or 0)
        fat = float(row.get("fat") or 0)
        fiber = float(row.get("fiber") or 0)
    except (TypeError, ValueError):
        cal = prot = carbs = fat = fiber = 0.0

    meal = row.get("meal_type", "").capitalize() or "Unknown"
    food = row.get("food", "Unknown")
    date = row.get("date", "")
    time_str = row.get("time", "")
    ts = f"{date} {time_str}".strip()

    return (
        f"📋 <b>Sheet entry detected</b>\n"
        f"🕐 {ts}\n\n"
        f"🍽 <b>{food}</b>  •  {meal}\n\n"
        f"Calories: <b>{cal:.1f} kcal</b>\n"
        f"Protein:  {prot:.1f} g\n"
        f"Carbs:    {carbs:.1f} g\n"
        f"Fat:      {fat:.1f} g\n"
        f"Fiber:    {fiber:.1f} g"
    )


def _fmt_water_notification(row: dict) -> str:
    """Format a manually-entered water row into a Telegram notification."""
    try:
        ml = int(float(row.get("amount_ml") or 0))
    except (TypeError, ValueError):
        ml = 0
    date = row.get("date", "")
    time_str = row.get("time", "")
    ts = f"{date} {time_str}".strip()
    return (
        f"📋 <b>Sheet entry detected</b>\n"
        f"🕐 {ts}\n\n"
        f"💧 <b>{ml} ml</b> water logged manually"
    )


async def _poll_sheet_changes(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Periodic job: check Google Sheets for new rows added manually and notify
    the authenticated user via Telegram.
    """
    bot_data = context.application.bot_data
    sheets_client: SheetsClient = bot_data.get("sheets_client")
    config: Config = bot_data.get("config")

    if sheets_client is None or config is None:
        return

    chat_id = get_active_chat_id(context)
    if chat_id is None:
        # Nobody is authenticated yet — nothing to notify
        return

    # Read last-known row counts (default 0 → will grab everything on first run,
    # then immediately update the baseline so we don't flood on startup)
    prev_food: int = bot_data.get(_FOOD_ROW_KEY, -1)
    prev_water: int = bot_data.get(_WATER_ROW_KEY, -1)

    try:
        cur_food, cur_water = await sheets_client.get_row_counts()
    except SheetsError as exc:
        logger.warning("Sheet poller: could not read row counts: %s", exc)
        return

    # First ever tick — just set the baseline without sending notifications
    if prev_food == -1 or prev_water == -1:
        bot_data[_FOOD_ROW_KEY] = cur_food
        bot_data[_WATER_ROW_KEY] = cur_water
        logger.debug("Sheet poller baseline set: food=%d, water=%d", cur_food, cur_water)
        return

    # ---- Check for new food rows ----------------------------------------
    if cur_food > prev_food:
        try:
            new_food = await sheets_client.get_new_food_rows(prev_food)
        except SheetsError as exc:
            logger.warning("Sheet poller: could not read new food rows: %s", exc)
            new_food = []

        for row in new_food:
            msg = _fmt_food_notification(row)
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001
                logger.error("Sheet poller: failed to notify food row: %s", exc)

        bot_data[_FOOD_ROW_KEY] = cur_food

    # ---- Check for new water rows ----------------------------------------
    if cur_water > prev_water:
        try:
            new_water = await sheets_client.get_new_water_rows(prev_water)
        except SheetsError as exc:
            logger.warning("Sheet poller: could not read new water rows: %s", exc)
            new_water = []

        for row in new_water:
            msg = _fmt_water_notification(row)
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001
                logger.error("Sheet poller: failed to notify water row: %s", exc)

        bot_data[_WATER_ROW_KEY] = cur_water


# --------------------------------------------------------------------------- #
# Public scheduling helpers
# --------------------------------------------------------------------------- #

def schedule_reminders_for_chat(application: Application, chat_id: int, config: Config) -> None:
    """Schedule (or re-schedule) the daily water reminders for a given chat."""
    if application.job_queue is None:
        logger.warning(
            "JobQueue is not available (install python-telegram-bot[job-queue]); "
            "scheduled reminders are disabled."
        )
        return

    already_scheduled = application.bot_data.get(_SCHEDULED_FLAG)
    if already_scheduled == chat_id:
        logger.debug("Reminders already scheduled for chat_id=%s; skipping.", chat_id)
        return

    # Remove any previously scheduled reminder jobs (e.g. for a different chat_id).
    for job in application.job_queue.jobs():
        if job.name and job.name.startswith(_JOB_NAME_PREFIX):
            job.schedule_removal()

    tz = config.tzinfo
    for hour in config.reminder_hours:
        job_name = f"{_JOB_NAME_PREFIX}{hour}"
        application.job_queue.run_daily(
            _send_water_reminder,
            time=time(hour=hour, minute=0, tzinfo=tz),
            chat_id=chat_id,
            name=job_name,
        )
        logger.info("Scheduled water reminder at %02d:00 (%s) for chat_id=%s", hour, config.app_timezone, chat_id)

    application.bot_data[_SCHEDULED_FLAG] = chat_id


def schedule_sheet_poller(application: Application) -> None:
    """
    Start the repeating sheet-change polling job.

    Safe to call multiple times — duplicate jobs are removed first.
    The poller runs regardless of whether a user is authenticated; it simply
    does nothing until get_active_chat_id() returns a valid chat ID.
    """
    if application.job_queue is None:
        logger.warning(
            "JobQueue is not available; sheet polling is disabled."
        )
        return

    # Remove existing poller job to avoid duplicates on re-auth
    for job in application.job_queue.jobs():
        if job.name == _SHEET_POLL_JOB:
            job.schedule_removal()

    application.job_queue.run_repeating(
        _poll_sheet_changes,
        interval=SHEET_POLL_INTERVAL_SECONDS,
        first=10,  # first run 10 s after scheduling
        name=_SHEET_POLL_JOB,
    )
    logger.info(
        "Sheet poller scheduled — checking every %ds for manual entries.",
        SHEET_POLL_INTERVAL_SECONDS,
    )
