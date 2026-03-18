"""Step 2: Roth Conversion Optimizer."""
import json
import logging
import time
from pathlib import Path

import anthropic

from backend.config import settings
from backend.extraction.extractors.base import extract_json_from_response
from backend.models.plan import ConversionOptimizerOutput, TaxTrajectoryOutput
from backend.models.snapshot import ClientFinancialSnapshotSchema
from backend.reasoning.tax_trajectory import ReasoningStepError

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "conversion_optimizer.txt"
_MODEL = "claude-sonnet-4-5"
_SYSTEM = (
    "You are a tax analysis engine for a financial planning software tool. "
    "You perform calculations and analysis only. You do not give financial advice. "
    "Output valid JSON only."
)


class ConversionOptimizer:
    async def run(
        self,
        snapshot: ClientFinancialSnapshotSchema,
        trajectory: TaxTrajectoryOutput,
    ) -> ConversionOptimizerOutput:
        """Build input slice and call Claude for Step 2."""
        personal = snapshot.personal
        income = snapshot.income
        accounts = snapshot.accounts
        tax_profile = snapshot.tax_profile

        pretax_balance = sum(
            (a.balance or 0) for a in accounts.traditional_401k
        ) + sum(
            (a.balance or 0) for a in accounts.traditional_ira
        )

        taxable_brokerage_total = sum(
            (a.total_value or 0) for a in accounts.taxable_brokerage
        )

        input_slice = {
            "filing_status": personal.filing_status,
            "state": personal.state,
            "age": personal.age,
            "retirement_target_age": personal.retirement_target_age,
            "income_projections": [p.model_dump() for p in income.projections],
            "social_security": income.social_security.model_dump() if income.social_security else None,
            "total_pretax_balance": pretax_balance,
            "taxable_brokerage_total": taxable_brokerage_total,
            "aca_relevant": tax_profile.aca_relevant if tax_profile else False,
            "niit_exposure": tax_profile.niit_exposure if tax_profile else False,
            "current_agi": income.current_year_agi,
            "tax_trajectory": trajectory.model_dump(),
        }

        prompt_template = _PROMPT_PATH.read_text()
        user_message = (
            f"{prompt_template}\n\n"
            f"<input_data>\n{json.dumps(input_slice, indent=2)}\n</input_data>\n\n"
            "Output valid JSON matching the schema exactly."
        )

        result = await self._call_with_retry(user_message)

        # Post-processing assertions
        for entry in result.conversion_plan:
            if entry.convert_amount < 0:
                raise ReasoningStepError(
                    "conversion_optimizer",
                    f"convert_amount for year {entry.year} is negative: {entry.convert_amount}",
                )

        if result.total_converted > pretax_balance:
            raise ReasoningStepError(
                "conversion_optimizer",
                f"total_converted ({result.total_converted}) exceeds pretax_balance ({pretax_balance}). "
                "Cannot convert more than is available.",
            )

        if result.liquidity_check_passed:
            if result.estimated_total_tax_on_conversions > taxable_brokerage_total:
                raise ReasoningStepError(
                    "conversion_optimizer",
                    f"liquidity_check_passed=True but estimated_total_tax_on_conversions "
                    f"({result.estimated_total_tax_on_conversions}) exceeds taxable_brokerage_total "
                    f"({taxable_brokerage_total}).",
                )

        return result

    async def _call_with_retry(self, user_message: str) -> ConversionOptimizerOutput:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        for attempt in range(2):
            t0 = time.monotonic()
            try:
                response = await client.messages.create(
                    model=_MODEL,
                    max_tokens=3000,
                    system=_SYSTEM,
                    messages=[{"role": "user", "content": user_message}],
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                raw = response.content[0].text.strip()
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                logger.info(
                    "conversion_optimizer attempt=%d input_tokens=%d output_tokens=%d latency_ms=%d success=True",
                    attempt + 1,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                )
            except Exception as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.error(
                    "conversion_optimizer attempt=%d latency_ms=%d success=False error=%s",
                    attempt + 1,
                    latency_ms,
                    exc,
                )
                if attempt == 1:
                    raise ReasoningStepError("conversion_optimizer", f"Claude API error: {exc}")
                user_message += f"\n\nPrevious attempt failed with API error: {exc}. Please retry."
                continue

            data = extract_json_from_response(raw)
            if data is None:
                if attempt == 1:
                    raise ReasoningStepError(
                        "conversion_optimizer",
                        "Claude did not return parseable JSON after retry.",
                    )
                user_message += (
                    f"\n\nPrevious response was not valid JSON. Raw response: {raw[:500]}. "
                    "Output valid JSON only."
                )
                continue

            try:
                return ConversionOptimizerOutput(**data)
            except Exception as exc:
                if attempt == 1:
                    raise ReasoningStepError(
                        "conversion_optimizer",
                        f"Schema validation failed: {exc}. Data: {data}",
                    )
                user_message += (
                    f"\n\nPrevious JSON failed schema validation: {exc}. "
                    "Fix the output to match the schema exactly."
                )

        raise ReasoningStepError("conversion_optimizer", "Exhausted retries.")
