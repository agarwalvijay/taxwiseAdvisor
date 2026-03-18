"""Step 1: Tax Trajectory Analyzer."""
import json
import logging
import time
from pathlib import Path

import anthropic

from backend.config import settings
from backend.extraction.extractors.base import extract_json_from_response
from backend.models.plan import TaxTrajectoryOutput
from backend.models.snapshot import ClientFinancialSnapshotSchema

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "tax_trajectory.txt"
_MODEL = "claude-sonnet-4-5"
_SYSTEM = (
    "You are a tax analysis engine for a financial planning software tool. "
    "You perform calculations and analysis only. You do not give financial advice. "
    "Output valid JSON only."
)


class ReasoningStepError(Exception):
    """Raised when a reasoning step fails after retry."""

    def __init__(self, step_name: str, detail: str):
        self.step_name = step_name
        self.detail = detail
        super().__init__(f"Reasoning step '{step_name}' failed: {detail}")


class TaxTrajectoryAnalyzer:
    async def run(self, snapshot: ClientFinancialSnapshotSchema) -> TaxTrajectoryOutput:
        """Extract targeted input slice and call Claude for Step 1."""
        personal = snapshot.personal
        income = snapshot.income
        accounts = snapshot.accounts
        rmd_profile = snapshot.rmd_profile

        pretax_balance = sum(
            (a.balance or 0) for a in accounts.traditional_401k
        ) + sum(
            (a.balance or 0) for a in accounts.traditional_ira
        )
        roth_balance = sum(
            (a.balance or 0) for a in accounts.roth_ira
        ) + sum(
            (a.balance or 0) for a in accounts.roth_401k
        )

        input_slice = {
            "age": personal.age,
            "spouse_age": personal.spouse_age,
            "filing_status": personal.filing_status,
            "state": personal.state,
            "retirement_target_age": personal.retirement_target_age,
            "income_projections": [p.model_dump() for p in income.projections],
            "current_agi": income.current_year_agi,
            "social_security": income.social_security.model_dump() if income.social_security else None,
            "total_pretax_balance": pretax_balance,
            "total_roth_balance": roth_balance,
            "rmd_start_age": rmd_profile.rmd_start_age if rmd_profile else 73,
        }

        prompt_template = _PROMPT_PATH.read_text()
        user_message = (
            f"{prompt_template}\n\n"
            f"<input_data>\n{json.dumps(input_slice, indent=2)}\n</input_data>\n\n"
            "Output valid JSON matching the schema exactly."
        )

        return await self._call_with_retry(user_message, input_slice)

    async def _call_with_retry(
        self, user_message: str, input_slice: dict
    ) -> TaxTrajectoryOutput:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        for attempt in range(2):
            t0 = time.monotonic()
            try:
                response = await client.messages.create(
                    model=_MODEL,
                    max_tokens=2048,
                    system=_SYSTEM,
                    messages=[{"role": "user", "content": user_message}],
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                raw = response.content[0].text.strip()
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                logger.info(
                    "tax_trajectory attempt=%d input_tokens=%d output_tokens=%d latency_ms=%d success=True",
                    attempt + 1,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                )
            except Exception as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.error(
                    "tax_trajectory attempt=%d latency_ms=%d success=False error=%s",
                    attempt + 1,
                    latency_ms,
                    exc,
                )
                if attempt == 1:
                    raise ReasoningStepError("tax_trajectory", f"Claude API error: {exc}")
                user_message += f"\n\nPrevious attempt failed with API error: {exc}. Please retry."
                continue

            data = extract_json_from_response(raw)
            if data is None:
                if attempt == 1:
                    raise ReasoningStepError(
                        "tax_trajectory",
                        "Claude did not return parseable JSON after retry.",
                    )
                user_message += (
                    f"\n\nPrevious response was not valid JSON. Raw response: {raw[:500]}. "
                    "Output valid JSON only."
                )
                continue

            try:
                return TaxTrajectoryOutput(**data)
            except Exception as exc:
                if attempt == 1:
                    raise ReasoningStepError(
                        "tax_trajectory",
                        f"Schema validation failed: {exc}. Data: {data}",
                    )
                user_message += (
                    f"\n\nPrevious JSON failed schema validation: {exc}. "
                    "Fix the output to match the schema exactly."
                )

        raise ReasoningStepError("tax_trajectory", "Exhausted retries.")
