"""
Google Sheets integration.

Credentials come entirely from environment variables (assembled in
Config.service_account_info()) — no JSON key file needed.  This makes
deployment on Render (or any 12-factor host) straightforward: just paste
the service-account fields into the platform's env-var UI.

Three worksheets are managed inside a single spreadsheet:

  Food_Log       Date | Time | Food | Calories | Protein | Carbs | Fat | Fiber | Meal Type
  Water_Log      Date | Time | Amount (ml)
  Daily_Summary  Date | Total Calories | Total Protein | Total Carbs | Total Fat | Total Fiber | Water Intake

gspread is synchronous; every call runs in a background thread via
asyncio.to_thread so the Telegram event loop is never blocked.
All network calls are wrapped with tenacity retry + exponential backoff.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import gspread
from google.auth.exceptions import GoogleAuthError
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, WorksheetNotFound
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.models import DailySummaryData, NutritionInfo, WaterEntry

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

FOOD_LOG_SHEET = "Food_Log"
WATER_LOG_SHEET = "Water_Log"
DAILY_SUMMARY_SHEET = "Daily_Summary"

FOOD_LOG_HEADERS = [
    "Date", "Time", "Food", "Calories", "Protein",
    "Carbs", "Fat", "Fiber", "Meal Type",
]
WATER_LOG_HEADERS = ["Date", "Time", "Amount (ml)"]
DAILY_SUMMARY_HEADERS = [
    "Date", "Total Calories", "Total Protein", "Total Carbs",
    "Total Fat", "Total Fiber", "Water Intake",
]


class SheetsError(RuntimeError):
    """Raised when a Google Sheets operation fails after all retries."""


class SheetsClient:
    """Async-friendly gspread wrapper for the Diet Tracker bot."""

    def __init__(
        self,
        service_account_info: dict[str, str],
        sheet_id: str = "",
        sheet_name: str = "",
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
    ) -> None:
        self._service_account_info = service_account_info
        self._sheet_id = sheet_id
        self._sheet_name = sheet_name
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._gc: gspread.Client | None = None
        self._spreadsheet: gspread.Spreadsheet | None = None

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _retryer(self) -> Retrying:
        return Retrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=self._backoff_seconds, min=1, max=30),
            retry=retry_if_exception_type((APIError, ConnectionError, TimeoutError)),
            reraise=True,
        )

    def _connect_sync(self) -> None:
        """Authenticate and open the target spreadsheet (blocking)."""
        try:
            credentials = Credentials.from_service_account_info(
                self._service_account_info, scopes=SCOPES
            )
            self._gc = gspread.authorize(credentials)
        except (GoogleAuthError, ValueError, KeyError) as exc:
            raise SheetsError(
                f"Google auth failed — check your GOOGLE_* env vars: {exc}"
            ) from exc

        try:
            for attempt in self._retryer():
                with attempt:
                    if self._sheet_id:
                        self._spreadsheet = self._gc.open_by_key(self._sheet_id)
                    else:
                        self._spreadsheet = self._gc.open(self._sheet_name)
        except APIError as exc:
            raise SheetsError(
                f"Could not open spreadsheet "
                f"(id={self._sheet_id!r}, name={self._sheet_name!r}): {exc}"
            ) from exc

        logger.info("Connected to Google Spreadsheet: %s", self._spreadsheet.title)

    def _ensure_worksheets_sync(self) -> None:
        """Create missing worksheets / fix wrong headers (blocking)."""
        assert self._spreadsheet is not None
        specs = {
            FOOD_LOG_SHEET: FOOD_LOG_HEADERS,
            WATER_LOG_SHEET: WATER_LOG_HEADERS,
            DAILY_SUMMARY_SHEET: DAILY_SUMMARY_HEADERS,
        }
        for title, headers in specs.items():
            try:
                ws = self._spreadsheet.worksheet(title)
            except WorksheetNotFound:
                logger.info("Worksheet '%s' not found — creating it.", title)
                ws = self._spreadsheet.add_worksheet(
                    title=title, rows=1000, cols=len(headers) + 2
                )
                ws.append_row(headers, value_input_option="USER_ENTERED")
                continue

            first_row = ws.row_values(1)
            if [h.strip() for h in first_row] != headers:
                logger.info("Worksheet '%s' — fixing headers.", title)
                ws.update(
                    range_name="A1",
                    values=[headers],
                    value_input_option="USER_ENTERED",
                )

    async def connect(self) -> None:
        """Public async entry point — authenticate and prepare worksheets."""
        await asyncio.to_thread(self._connect_sync)
        await asyncio.to_thread(self._ensure_worksheets_sync)

    def _worksheet(self, title: str) -> gspread.Worksheet:
        if self._spreadsheet is None:
            raise SheetsError("SheetsClient not connected — call connect() first.")
        return self._spreadsheet.worksheet(title)

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    def _append_row_sync(self, sheet_title: str, row: list[str]) -> None:
        ws = self._worksheet(sheet_title)
        for attempt in self._retryer():
            with attempt:
                ws.append_row(row, value_input_option="USER_ENTERED")

    async def append_food_log(self, entry: NutritionInfo) -> None:
        try:
            await asyncio.to_thread(
                self._append_row_sync, FOOD_LOG_SHEET, entry.to_sheet_row()
            )
            logger.info("Food logged: %s (%.0f kcal)", entry.food, entry.calories)
        except APIError as exc:
            logger.error("append_food_log failed: %s", exc, exc_info=True)
            raise SheetsError(f"Could not save food entry: {exc}") from exc

    async def append_water_log(self, entry: WaterEntry) -> None:
        try:
            await asyncio.to_thread(
                self._append_row_sync, WATER_LOG_SHEET, entry.to_sheet_row()
            )
            logger.info("Water logged: %d ml", entry.amount_ml)
        except APIError as exc:
            logger.error("append_water_log failed: %s", exc, exc_info=True)
            raise SheetsError(f"Could not save water entry: {exc}") from exc

    def _upsert_daily_summary_sync(self, summary: DailySummaryData) -> None:
        ws = self._worksheet(DAILY_SUMMARY_SHEET)
        for attempt in self._retryer():
            with attempt:
                all_values = ws.get_all_values()
                row_index: int | None = None
                for idx, row in enumerate(all_values[1:], start=2):
                    if row and row[0] == summary.date:
                        row_index = idx
                        break
                row_data = summary.to_sheet_row()
                if row_index is not None:
                    ws.update(
                        range_name=f"A{row_index}",
                        values=[row_data],
                        value_input_option="USER_ENTERED",
                    )
                else:
                    ws.append_row(row_data, value_input_option="USER_ENTERED")

    async def upsert_daily_summary(self, summary: DailySummaryData) -> None:
        try:
            await asyncio.to_thread(self._upsert_daily_summary_sync, summary)
            logger.info("Daily_Summary updated for %s", summary.date)
        except APIError as exc:
            logger.error("upsert_daily_summary failed: %s", exc, exc_info=True)
            raise SheetsError(f"Could not update daily summary: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def _get_all_records_sync(self, sheet_title: str) -> list[dict[str, Any]]:
        ws = self._worksheet(sheet_title)
        for attempt in self._retryer():
            with attempt:
                return ws.get_all_records()
        return []

    async def get_today_food_rows(self, date_str: str) -> list[dict[str, Any]]:
        try:
            records = await asyncio.to_thread(
                self._get_all_records_sync, FOOD_LOG_SHEET
            )
        except APIError as exc:
            logger.error("get_today_food_rows failed: %s", exc, exc_info=True)
            raise SheetsError(f"Could not read food log: {exc}") from exc

        return [
            {
                "date": r.get("Date", ""),
                "time": r.get("Time", ""),
                "food": r.get("Food", ""),
                "calories": r.get("Calories", 0),
                "protein": r.get("Protein", 0),
                "carbs": r.get("Carbs", 0),
                "fat": r.get("Fat", 0),
                "fiber": r.get("Fiber", 0),
                "meal_type": r.get("Meal Type", ""),
            }
            for r in records
            if str(r.get("Date", "")) == date_str
        ]

    async def get_today_water_total(self, date_str: str) -> int:
        try:
            records = await asyncio.to_thread(
                self._get_all_records_sync, WATER_LOG_SHEET
            )
        except APIError as exc:
            logger.error("get_today_water_total failed: %s", exc, exc_info=True)
            raise SheetsError(f"Could not read water log: {exc}") from exc

        total = 0
        for r in records:
            if str(r.get("Date", "")) == date_str:
                try:
                    total += int(float(r.get("Amount (ml)", 0)))
                except (TypeError, ValueError):
                    continue
        return total

    async def compute_today_summary(self, date_str: str) -> DailySummaryData:
        food_rows = await self.get_today_food_rows(date_str)
        water_total = await self.get_today_water_total(date_str)

        summary = DailySummaryData(
            date=date_str, water_ml=water_total, meals_logged=len(food_rows)
        )
        for row in food_rows:
            summary.total_calories += float(row.get("calories", 0) or 0)
            summary.total_protein += float(row.get("protein", 0) or 0)
            summary.total_carbs += float(row.get("carbs", 0) or 0)
            summary.total_fat += float(row.get("fat", 0) or 0)
            summary.total_fiber += float(row.get("fiber", 0) or 0)
        return summary

    async def refresh_daily_summary(self, date_str: str) -> DailySummaryData:
        summary = await self.compute_today_summary(date_str)
        await self.upsert_daily_summary(summary)
        return summary
