"""
Configuration loader for the Diet Tracker Bot.

All runtime config comes from environment variables (.env locally,
Render's "Environment" tab in production).  No JSON files needed —
Google service-account credentials are stored field-by-field in env vars
and assembled into a credentials dict at runtime.

Render-specific additions
--------------------------
WEBHOOK_URL   – the public HTTPS URL Render assigns to your service
                (e.g. https://diet-tracker-bot.onrender.com).
                When set the bot runs in webhook mode (Render-ready).
                When absent it falls back to long-polling (local dev).
PORT          – Render injects this automatically; defaults to 8443.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _get_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required environment variable '{name}'. "
            f"Please set it in your .env file or on Render's Environment tab."
        )
    return value


def _get_optional(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value if value else default


def _get_optional_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable '{name}' must be an integer, got '{raw}'."
        ) from exc


def _get_optional_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable '{name}' must be a number, got '{raw}'."
        ) from exc


def _get_private_key(name: str = "GOOGLE_PRIVATE_KEY") -> str:
    """
    Read the RSA private key from an env var and normalise newlines.

    .env files and Render env vars store the key with literal \\n sequences;
    google-auth needs real newline characters inside the PEM block.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        raise ConfigError(
            f"Missing required environment variable '{name}'. "
            f"Paste the full private key from your service-account JSON."
        )
    # Replace escaped newlines (\\n) with real newlines (\n).
    return raw.replace("\\n", "\n")


# --------------------------------------------------------------------------- #
# Config dataclass
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Config:
    """Strongly-typed application configuration."""

    # Telegram
    telegram_bot_token: str
    auth_password: str

    # Webhook / Render  (empty string = use polling locally)
    webhook_url: str
    webhook_port: int
    webhook_path: str

    # Groq AI
    groq_api_key: str
    groq_model: str

    # Google Sheets — individual service-account fields (no JSON file)
    google_project_id: str
    google_private_key_id: str
    google_private_key: str
    google_client_email: str
    google_client_id: str
    google_client_x509_cert_url: str
    google_sheet_id: str
    google_sheet_name: str

    # App behaviour
    app_timezone: str
    log_level: str
    log_file: str
    daily_water_goal_ml: int
    daily_calorie_goal: int
    daily_protein_goal_g: int
    daily_carbs_goal_g: int
    daily_fat_goal_g: int
    daily_fiber_goal_g: int

    # Reliability
    max_retries: int
    retry_backoff_seconds: float

    reminder_hours: tuple[int, ...] = field(default=(9, 12, 15, 18, 21))

    # ------------------------------------------------------------------ #
    # Derived helpers
    # ------------------------------------------------------------------ #

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_url)

    @property
    def full_webhook_url(self) -> str:
        """The URL Telegram will POST updates to."""
        return f"{self.webhook_url.rstrip('/')}/{self.webhook_path.lstrip('/')}"

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.app_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(
                f"Invalid APP_TIMEZONE '{self.app_timezone}'. "
                f"Use an IANA timezone name, e.g. 'Asia/Kolkata'."
            ) from exc

    def service_account_info(self) -> dict[str, str]:
        """Build the dict that google-auth's from_service_account_info() expects."""
        return {
            "type": "service_account",
            "project_id": self.google_project_id,
            "private_key_id": self.google_private_key_id,
            "private_key": self.google_private_key,
            "client_email": self.google_client_email,
            "client_id": self.google_client_id,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": self.google_client_x509_cert_url,
        }

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate(self) -> None:
        if len(self.auth_password) < 4:
            raise ConfigError("AUTH_PASSWORD must be at least 4 characters long.")

        if not self.google_sheet_id and not self.google_sheet_name:
            raise ConfigError(
                "Either GOOGLE_SHEET_ID or GOOGLE_SHEET_NAME must be set."
            )

        if not self.google_private_key.startswith("-----BEGIN"):
            raise ConfigError(
                "GOOGLE_PRIVATE_KEY does not look like a valid PEM key. "
                "Make sure you copied the full value from your service-account JSON "
                "and that \\n sequences are preserved."
            )

        _ = self.tzinfo  # validate timezone

        if self.daily_water_goal_ml <= 0:
            raise ConfigError("DAILY_WATER_GOAL_ML must be a positive integer.")

        if self.daily_calorie_goal <= 0:
            raise ConfigError("DAILY_CALORIE_GOAL must be a positive integer.")

        if self.daily_protein_goal_g <= 0:
            raise ConfigError("DAILY_PROTEIN_GOAL_G must be a positive integer.")

        if self.daily_carbs_goal_g <= 0:
            raise ConfigError("DAILY_CARBS_GOAL_G must be a positive integer.")

        if self.daily_fat_goal_g <= 0:
            raise ConfigError("DAILY_FAT_GOAL_G must be a positive integer.")

        if self.daily_fiber_goal_g <= 0:
            raise ConfigError("DAILY_FIBER_GOAL_G must be a positive integer.")

        if self.use_webhook:
            if not self.webhook_url.startswith("https://"):
                raise ConfigError(
                    "WEBHOOK_URL must start with https:// — Telegram only "
                    "delivers webhooks over HTTPS."
                )


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

def load_config() -> Config:
    """Load and validate configuration from the process environment."""
    config = Config(
        # Telegram
        telegram_bot_token=_get_required("TELEGRAM_BOT_TOKEN"),
        auth_password=_get_required("AUTH_PASSWORD"),

        # Webhook (Render sets PORT automatically; WEBHOOK_URL is user-supplied)
        webhook_url=_get_optional("WEBHOOK_URL", ""),
        webhook_port=_get_optional_int("PORT", 8443),
        webhook_path=_get_optional("WEBHOOK_PATH", "webhook"),

        # Groq
        groq_api_key=_get_required("GROQ_API_KEY"),
        groq_model=_get_optional("GROQ_MODEL", "llama-3.3-70b-versatile"),

        # Google service account (inline — no JSON file)
        google_project_id=_get_required("GOOGLE_PROJECT_ID"),
        google_private_key_id=_get_required("GOOGLE_PRIVATE_KEY_ID"),
        google_private_key=_get_private_key("GOOGLE_PRIVATE_KEY"),
        google_client_email=_get_required("GOOGLE_CLIENT_EMAIL"),
        google_client_id=_get_required("GOOGLE_CLIENT_ID"),
        google_client_x509_cert_url=_get_required("GOOGLE_CLIENT_X509_CERT_URL"),
        google_sheet_id=_get_optional("GOOGLE_SHEET_ID", ""),
        google_sheet_name=_get_optional("GOOGLE_SHEET_NAME", ""),

        # App behaviour
        app_timezone=_get_optional("APP_TIMEZONE", "Asia/Kolkata"),
        log_level=_get_optional("LOG_LEVEL", "INFO").upper(),
        log_file=_get_optional("LOG_FILE", "logs/bot.log"),
        daily_water_goal_ml=_get_optional_int("DAILY_WATER_GOAL_ML", 3000),
        daily_calorie_goal=_get_optional_int("DAILY_CALORIE_GOAL", 2000),
        daily_protein_goal_g=_get_optional_int("DAILY_PROTEIN_GOAL_G", 150),
        daily_carbs_goal_g=_get_optional_int("DAILY_CARBS_GOAL_G", 225),
        daily_fat_goal_g=_get_optional_int("DAILY_FAT_GOAL_G", 65),
        daily_fiber_goal_g=_get_optional_int("DAILY_FIBER_GOAL_G", 25),

        # Reliability
        max_retries=_get_optional_int("MAX_RETRIES", 3),
        retry_backoff_seconds=_get_optional_float("RETRY_BACKOFF_SECONDS", 2.0),
    )
    config.validate()
    return config
