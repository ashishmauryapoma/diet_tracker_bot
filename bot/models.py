"""
Typed data models used throughout the bot.

Keeping these as explicit dataclasses (rather than passing raw dicts around)
gives us validation in one place and much better editor/type-checker support.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

VALID_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")


class ValidationError(ValueError):
    """Raised when incoming data (from the user or from Groq) fails validation."""


def infer_meal_type_from_hour(hour: int) -> str:
    """Best-effort fallback meal-type classification based on local clock hour."""
    if 5 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 16:
        return "lunch"
    if 16 <= hour < 21:
        return "dinner"
    return "snack"


def _coerce_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Field '{field_name}' must be numeric, got {value!r}") from exc
    if result < 0:
        raise ValidationError(f"Field '{field_name}' cannot be negative, got {result}")
    return round(result, 1)


@dataclass
class NutritionInfo:
    """Structured nutrition data for a single logged food entry."""

    food: str
    serving_size: str
    calories: float
    protein: float
    carbs: float
    fat: float
    fiber: float
    meal_type: str
    logged_at: datetime

    @classmethod
    def from_groq_json(
        cls, data: dict[str, Any], original_text: str, logged_at: datetime
    ) -> "NutritionInfo":
        """Build and validate a NutritionInfo instance from a Groq JSON response."""
        food = str(data.get("food") or original_text).strip()[:200]
        serving_size = str(data.get("serving_size") or "1 serving").strip()[:50]

        calories = _coerce_float(data.get("calories", 0), "calories")
        protein = _coerce_float(data.get("protein", 0), "protein")
        carbs = _coerce_float(data.get("carbs", 0), "carbs")
        fat = _coerce_float(data.get("fat", 0), "fat")
        fiber = _coerce_float(data.get("fiber", 0), "fiber")

        meal_type = str(data.get("meal_type", "")).strip().lower()
        if meal_type not in VALID_MEAL_TYPES:
            meal_type = infer_meal_type_from_hour(logged_at.hour)

        if calories > 10000:
            raise ValidationError(f"Calories value {calories} is unrealistically high; rejecting entry.")

        return cls(
            food=food,
            serving_size=serving_size,
            calories=calories,
            protein=protein,
            carbs=carbs,
            fat=fat,
            fiber=fiber,
            meal_type=meal_type,
            logged_at=logged_at,
        )

    def to_sheet_row(self) -> list[str]:
        """Serialize to the row format expected by the Food_Log sheet."""
        return [
            self.logged_at.strftime("%d-%m-%Y"),
            self.logged_at.strftime("%H:%M:%S"),
            self.food,
            f"{self.calories:.1f}",
            f"{self.protein:.1f}",
            f"{self.carbs:.1f}",
            f"{self.fat:.1f}",
            f"{self.fiber:.1f}",
            self.meal_type,
        ]


@dataclass
class WaterEntry:
    """A single water-intake log entry."""

    amount_ml: int
    logged_at: datetime

    def __post_init__(self) -> None:
        if self.amount_ml <= 0:
            raise ValidationError("Water amount must be a positive number of millilitres.")
        if self.amount_ml > 5000:
            raise ValidationError("Water amount cannot exceed 5000ml in a single entry.")

    def to_sheet_row(self) -> list[str]:
        return [
            self.logged_at.strftime("%d-%m-%Y"),
            self.logged_at.strftime("%H:%M:%S"),
            str(self.amount_ml),
        ]


@dataclass
class DailySummaryData:
    """Aggregated nutrition + hydration totals for a single calendar day."""

    date: str
    total_calories: float = 0.0
    total_protein: float = 0.0
    total_carbs: float = 0.0
    total_fat: float = 0.0
    total_fiber: float = 0.0
    water_ml: int = 0
    meals_logged: int = 0

    def to_sheet_row(self) -> list[str]:
        return [
            self.date,
            f"{self.total_calories:.1f}",
            f"{self.total_protein:.1f}",
            f"{self.total_carbs:.1f}",
            f"{self.total_fat:.1f}",
            f"{self.total_fiber:.1f}",
            str(self.water_ml),
        ]
