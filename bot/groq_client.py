"""
Groq AI integration.

Responsible for:
  1. Turning a free-text food description into structured nutrition data.
  2. Detecting water intake from plain text and extracting the amount in ml.
  3. Analyzing a full day's worth of logged food + water into a coaching
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


# --------------------------------------------------------------------------- #
# System prompts
# --------------------------------------------------------------------------- #

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

WATER_INTENT_SYSTEM_PROMPT = """\
You are a health-tracking assistant. Determine whether a user's message is \
describing drinking water or a beverage that is essentially water \
(e.g. plain water, sparkling water, coconut water, herbal tea — \
NOT sugary drinks, milk, juice, or smoothies).

Respond with ONLY a single valid JSON object and nothing else — no markdown \
fences, no extra text:

{
  "is_water": <true or false>,
  "amount_ml": <integer millilitres if is_water is true, otherwise 0>
}

Conversion rules:
- 1 L / litre / liter = 1000 ml
- 1 glass = 250 ml
- 1 cup = 240 ml
- 1 bottle = 500 ml
- If no amount is mentioned but is_water is true, use 500 (one glass default).
- amount_ml must be between 1 and 5000; clamp if out of range.
- Set is_water to false for food items that happen to contain water (e.g. soup, fruit).
"""

INTENT_CLASSIFY_SYSTEM_PROMPT = """\
You are a classifier for a diet-tracking bot. Given a user message, decide \
whether it is:
  - "water" : the user is describing drinking water or water-like beverages \
              (plain water, sparkling water, coconut water, herbal tea — \
              NOT sugary drinks, milk, juice, or smoothies)
  - "food"  : the user is describing food or other beverages \
              (food, meal, snack, juice, milk, coffee, tea with sugar, etc.)
  - "unknown": the message is NOT related to food or drink at all \
               (e.g. greetings, questions, random text, commands, math, code)

Respond with ONLY a single valid JSON object:

{
  "intent": "water", "food", or "unknown"
}

Examples:
  "drank 500ml water"         → {"intent": "water"}
  "water"                     → {"intent": "water"}
  "waetr"                     → {"intent": "water"}
  "pani"                      → {"intent": "water"}
  "h2o"                       → {"intent": "water"}
  "1 glass"                   → {"intent": "water"}
  "2 eggs and toast"          → {"intent": "food"}
  "chicken biryani 300g"      → {"intent": "food"}
  "hello how are you"         → {"intent": "unknown"}
  "what is 2+2"               → {"intent": "unknown"}
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


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

class GroqNutritionClient:
    """Thin async wrapper around the Groq chat-completions API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
    ) -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._model = model
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    def _retryer(self) -> AsyncRetrying:
        return AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=self._backoff_seconds, min=1, max=30),
            retry=retry_if_exception_type(
                (APIConnectionError, RateLimitError, APIStatusError, json.JSONDecodeError)
            ),
            reraise=True,
        )

    async def _chat_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Call Groq in JSON mode and return the parsed JSON object."""
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
                        logger.warning(
                            "Groq returned non-JSON content, will retry: %s", content[:200]
                        )
                        raise exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.error("Groq API call failed after retries: %s", exc, exc_info=True)
            raise GroqAnalysisError(f"Groq API call failed: {exc}") from last_error

        raise GroqAnalysisError("Groq API call failed for an unknown reason.")

    # ------------------------------------------------------------------ #
    # Public methods
    # ------------------------------------------------------------------ #

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
            logger.error(
                "Groq returned invalid nutrition data for input %r: %s", text, data
            )
            raise

    async def detect_water_intake(self, text: str) -> tuple[bool, int]:
        """
        Ask Groq whether a plain-text message is about drinking water.

        Returns:
            (is_water, amount_ml) — e.g. (True, 500) or (False, 0).

        This is only called when the fast regex in utils.try_parse_water_ml()
        returns None but the text still contains water-related keywords.
        """
        text = text.strip()
        if not text:
            return False, 0

        try:
            data = await self._chat_json(WATER_INTENT_SYSTEM_PROMPT, text)
        except GroqAnalysisError:
            # If AI fails, fall back gracefully — treat as not water
            return False, 0

        is_water = bool(data.get("is_water", False))
        try:
            amount_ml = max(0, min(5000, int(data.get("amount_ml", 0))))
        except (TypeError, ValueError):
            amount_ml = 0

        return is_water, amount_ml

    async def classify_intent(self, text: str) -> str:
        """
        Classify whether a user message is water, food, or completely unknown.

        Returns:
            "water"   — treat as a water entry (500ml default)
            "food"    — treat as a food entry (AI analysis)
            "unknown" — not related to diet tracking at all
        """
        text = text.strip()
        if not text:
            return "unknown"
        try:
            data = await self._chat_json(INTENT_CLASSIFY_SYSTEM_PROMPT, text)
            intent = str(data.get("intent", "food")).lower()
            return intent if intent in ("water", "food", "unknown") else "food"
        except GroqAnalysisError:
            # On failure, assume food so we don't silently drop entries
            return "food"

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
            "protein":  sum(float(r.get("protein",  0)) for r in food_rows),
            "carbs":    sum(float(r.get("carbs",    0)) for r in food_rows),
            "fat":      sum(float(r.get("fat",      0)) for r in food_rows),
            "fiber":    sum(float(r.get("fiber",    0)) for r in food_rows),
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
            f"- Protein:  {totals['protein']:.1f} g\n"
            f"- Carbs:    {totals['carbs']:.1f} g\n"
            f"- Fat:      {totals['fat']:.1f} g\n"
            f"- Fiber:    {totals['fiber']:.1f} g\n"
            f"- Water intake: {water_ml} ml\n\n"
            f"Meals logged today:\n{meals_text}\n"
        )

        return await self._chat_json(DAILY_ANALYSIS_SYSTEM_PROMPT, user_prompt)
