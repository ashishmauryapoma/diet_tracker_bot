"""
Groq AI integration.

Responsible for:
  1. Turning a free-text food description into structured nutrition data.
  2. Analyzing a full day's worth of logged food + water into a coaching
     summary (nutrition score, strengths, improvements, recommendation).

All network calls are wrapped with retry logic (exponential backoff) since
LLM APIs occasionally return transient 429/5xx errors.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from groq import APIConnectionError, APIStatusError, AsyncGroq, RateLimitError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.models import NutritionInfo, ValidationError

logger = logging.getLogger(__name__)


class GroqAnalysisError(RuntimeError):
    """Raised when Groq fails to produce usable nutrition data after retries."""


FOOD_ANALYSIS_SYSTEM_PROMPT = """\
You are a professional nutritionist and food-data analyst with deep knowledge \
of standard nutrition databases (USDA FoodData Central, Indian Food Composition \
Tables / IFCT, and common restaurant/home-cooked portion sizes across global \
and Indian cuisine).

Given a free-text description of food or a meal (which may include quantities, \
multiple items, brand names, or colloquial names), estimate its total \
nutritional content as accurately as possible.

Respond with ONLY a single valid JSON object and nothing else — no markdown \
fences, no explanation, no extra text. The JSON object must have exactly \
these keys:

{
  "food": "<cleaned, human-readable description of the food>",
  "serving_size": "<estimated total serving size, e.g. '350g' or '2 pieces (120g)'>",
  "calories": <number, total kcal for the whole described portion>,
  "protein": <number, grams>,
  "carbs": <number, grams>,
  "fat": <number, grams>,
  "fiber": <number, grams>,
  "meal_type": "<one of: breakfast, lunch, dinner, snack>"
}

Rules:
- All numeric fields must be realistic, non-negative numbers (decimals allowed).
- If the description contains multiple items, sum their nutrition values together.
- If quantity is not specified, assume one standard/average serving.
- Use the provided local time of day to help decide meal_type when the food \
itself doesn't make it obvious (e.g. before 11am leans breakfast, 11am-4pm \
leans lunch, 4pm-9pm leans dinner, otherwise snack).
- Never include any commentary, units inside numbers, or markdown formatting.
"""

DAILY_ANALYSIS_SYSTEM_PROMPT = """\
You are an expert dietitian and supportive nutrition coach reviewing a single \
day of a client's logged food and water intake.

Respond with ONLY a single valid JSON object and nothing else — no markdown \
fences, no explanation outside the JSON. The JSON object must have exactly \
these keys:

{
  "nutrition_score": <number from 0 to 10, one decimal place>,
  "protein_adequacy": "<short verdict, e.g. 'Adequate' or 'Below target'>",
  "hydration_status": "<short verdict, e.g. 'Well hydrated' or 'Needs more water'>",
  "calorie_balance": "<short verdict relative to the daily goal>",
  "strengths": ["<short positive point>", "..."],
  "improvements": ["<short actionable improvement point>", "..."],
  "missing_nutrients": ["<nutrient or food group that seems lacking>", "..."],
  "recommendation": "<one concrete, practical recommendation for the rest of the day or tomorrow>"
}

Be encouraging but honest. Base your analysis on standard adult nutrition \
guidelines unless the data clearly suggests otherwise. Keep each list to at \
most 4 short items (a few words to one short sentence each).
"""


class GroqNutritionClient:
    """Thin async wrapper around the Groq chat-completions API."""

    def __init__(self, api_key: str, model: str, max_retries: int = 3, backoff_seconds: float = 2.0) -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._model = model
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    def _retryer(self) -> AsyncRetrying:
        return AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=self._backoff_seconds, min=1, max=30),
            retry=retry_if_exception_type((APIConnectionError, RateLimitError, APIStatusError, json.JSONDecodeError)),
            reraise=True,
        )

    async def _chat_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Call Groq with JSON-mode and return the parsed JSON object."""
        last_error: Exception | None = None
        try:
            async for attempt in self._retryer():
                with attempt:
                    logger.debug("Calling Groq model=%s", self._model)
                    response = await self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.2,
                        max_tokens=800,
                        response_format={"type": "json_object"},
                    )
                    content = response.choices[0].message.content
                    if not content:
                        raise GroqAnalysisError("Groq returned an empty response.")
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError as exc:
                        logger.warning("Groq returned non-JSON content, will retry: %s", content[:200])
                        raise exc
        except Exception as exc:  # noqa: BLE001 - we want to wrap *any* failure after retries
            last_error = exc
            logger.error("Groq API call failed after retries: %s", exc, exc_info=True)
            raise GroqAnalysisError(f"Groq API call failed: {exc}") from last_error

        # Unreachable, but keeps type-checkers happy.
        raise GroqAnalysisError("Groq API call failed for an unknown reason.")

    async def analyze_food(self, text: str, logged_at: datetime) -> NutritionInfo:
        """Analyze a free-text food description and return structured nutrition data."""
        text = text.strip()
        if not text:
            raise ValidationError("Food description cannot be empty.")
        if len(text) > 500:
            raise ValidationError("Food description is too long (max 500 characters).")

        user_prompt = f"Local time: {logged_at.strftime('%H:%M')}. Food entry: {text}"

        data = await self._chat_json(FOOD_ANALYSIS_SYSTEM_PROMPT, user_prompt)
        try:
            return NutritionInfo.from_groq_json(data, original_text=text, logged_at=logged_at)
        except ValidationError:
            logger.error("Groq returned invalid nutrition data for input %r: %s", text, data)
            raise

    async def analyze_day(
        self,
        food_rows: list[dict[str, Any]],
        water_ml: int,
        calorie_goal: int,
        water_goal_ml: int,
    ) -> dict[str, Any]:
        """Analyze a full day's food + water log and return a coaching summary."""
        if not food_rows:
            raise ValidationError("No food entries logged today; nothing to analyze.")

        totals = {
            "calories": sum(float(r.get("calories", 0)) for r in food_rows),
            "protein": sum(float(r.get("protein", 0)) for r in food_rows),
            "carbs": sum(float(r.get("carbs", 0)) for r in food_rows),
            "fat": sum(float(r.get("fat", 0)) for r in food_rows),
            "fiber": sum(float(r.get("fiber", 0)) for r in food_rows),
        }
        meals_text = "\n".join(
            f"- [{r.get('meal_type', 'unknown')}] {r.get('food', 'unknown')} "
            f"({r.get('calories', 0)} kcal, P:{r.get('protein', 0)}g, "
            f"C:{r.get('carbs', 0)}g, F:{r.get('fat', 0)}g, Fiber:{r.get('fiber', 0)}g)"
            for r in food_rows
        )

        user_prompt = (
            f"Daily calorie goal: {calorie_goal} kcal\n"
            f"Daily water goal: {water_goal_ml} ml\n\n"
            f"Totals so far today:\n"
            f"- Calories: {totals['calories']:.0f} kcal\n"
            f"- Protein: {totals['protein']:.1f} g\n"
            f"- Carbs: {totals['carbs']:.1f} g\n"
            f"- Fat: {totals['fat']:.1f} g\n"
            f"- Fiber: {totals['fiber']:.1f} g\n"
            f"- Water intake: {water_ml} ml\n\n"
            f"Meals logged today:\n{meals_text}\n"
        )

        return await self._chat_json(DAILY_ANALYSIS_SYSTEM_PROMPT, user_prompt)
