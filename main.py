"""
Diet Tracker Bot — entry point.

Deployment modes
----------------
Webhook mode  (Render / any public HTTPS host)
    Set WEBHOOK_URL=https://your-service.onrender.com in environment vars.
    Render injects PORT automatically.  The bot registers the webhook with
    Telegram on startup and listens on 0.0.0.0:PORT.

Polling mode  (local development, no public URL needed)
    Leave WEBHOOK_URL unset (or empty).  The bot long-polls Telegram.

Run:
    python main.py
"""

from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.config import ConfigError, load_config
from bot.groq_client import GroqNutritionClient
from bot.handlers import (
    analyze_command,
    error_handler,
    food_confirm_callback,
    handle_text,
    help_command,
    start,
    summary_command,
    today_command,
    water_callback,
    water_command,
)
from bot.logger import setup_logging
from bot.scheduler import schedule_sheet_poller
from bot.sheets_client import SheetsClient, SheetsError

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Post-init hook (runs once before the bot starts accepting updates)
# --------------------------------------------------------------------------- #

async def _post_init(application: Application) -> None:
    sheets_client: SheetsClient = application.bot_data["sheets_client"]

    logger.info("Connecting to Google Sheets…")
    try:
        await sheets_client.connect()
    except SheetsError as exc:
        logger.critical("Could not connect to Google Sheets: %s", exc, exc_info=True)
        raise

    await application.bot.set_my_commands(
        [
            BotCommand("start",   "Authenticate / welcome message"),
            BotCommand("help",    "List all commands"),
            BotCommand("water",   "Log water intake"),
            BotCommand("summary", "Today's nutrition summary"),
            BotCommand("analyze", "AI analysis of today's diet"),
            BotCommand("today",   "See everything logged today"),
        ]
    )

    # Start the sheet-change poller — detects rows added manually in Google Sheets
    schedule_sheet_poller(application)

    logger.info("Bot initialised and ready.")


# --------------------------------------------------------------------------- #
# Application builder
# --------------------------------------------------------------------------- #

def build_application() -> Application:
    config = load_config()
    setup_logging(config.log_level, config.log_file)

    logger.info(
        "Starting Diet Tracker Bot | timezone=%s | model=%s | mode=%s",
        config.app_timezone,
        config.groq_model,
        "webhook" if config.use_webhook else "polling",
    )

    groq_client = GroqNutritionClient(
        api_key=config.groq_api_key,
        model=config.groq_model,
        max_retries=config.max_retries,
        backoff_seconds=config.retry_backoff_seconds,
    )
    sheets_client = SheetsClient(
        service_account_info=config.service_account_info(),
        sheet_id=config.google_sheet_id,
        sheet_name=config.google_sheet_name,
        max_retries=config.max_retries,
        backoff_seconds=config.retry_backoff_seconds,
    )

    application = (
        ApplicationBuilder()
        .token(config.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    # Shared services — accessed in every handler via context.application.bot_data
    application.bot_data["config"] = config
    application.bot_data["groq_client"] = groq_client
    application.bot_data["sheets_client"] = sheets_client

    # Commands
    application.add_handler(CommandHandler("start",   start))
    application.add_handler(CommandHandler("help",    help_command))
    application.add_handler(CommandHandler("water",   water_command))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(CommandHandler("analyze", analyze_command))
    application.add_handler(CommandHandler("today",   today_command))

    # Inline keyboard callbacks
    application.add_handler(CallbackQueryHandler(water_callback, pattern=r"^water_"))
    application.add_handler(CallbackQueryHandler(food_confirm_callback, pattern=r"^food_(confirm|undo)$"))

    # Free text  →  password attempt OR food entry
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    application.add_error_handler(error_handler)

    return application


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    try:
        application = build_application()
    except ConfigError as exc:
        print(f"[FATAL] Configuration error: {exc}")
        logging.getLogger(__name__).critical("Configuration error: %s", exc)
        raise SystemExit(1) from exc

    config = application.bot_data["config"]

    if config.use_webhook:
        # ----------------------------------------------------------------
        # Webhook mode — used on Render and any other public HTTPS host.
        #
        # Render provides:
        #   • An automatic public HTTPS URL  (set as WEBHOOK_URL env var)
        #   • A PORT env var for the process to bind on
        #
        # PTB registers the webhook with Telegram automatically when
        # webhook_url is passed to run_webhook().
        # ----------------------------------------------------------------
        logger.info(
            "Starting in WEBHOOK mode | url=%s | port=%d",
            config.full_webhook_url,
            config.webhook_port,
        )
        application.run_webhook(
            listen="0.0.0.0",
            port=config.webhook_port,
            url_path=config.webhook_path,
            webhook_url=config.full_webhook_url,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
    else:
        # ----------------------------------------------------------------
        # Polling mode — local development (no public URL required).
        # ----------------------------------------------------------------
        logger.info("Starting in POLLING mode (local dev).")
        application.run_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
