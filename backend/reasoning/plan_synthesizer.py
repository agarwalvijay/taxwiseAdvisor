"""Step 4: Plan Synthesizer."""
import json
import logging
import time
from pathlib import Path

import anthropic

from backend.config import settings
from backend.extraction.extractors.base import extract_json_from_response
from backend.models.plan import (
    ConversionOptimizerOutput,
    PlanSynthesizerOutput,
    TaxTrajectoryOutput,
    TLHAdvisorOutput,
)
from backend.models.snapshot import ClientFinancialSnapshotSchema
from backend.reasoning.tax_trajectory import ReasoningStepError

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "plan_synthesizer.txt"
_MODEL = "claude-sonnet-4-5"
_SYSTEM = (
    "You are a tax analysis engine for a financial planning software tool. "
    "You perform calculations and analysis only. You do not give financial advice. "
    "Output valid JSON only."
)


class PlanSynthesizer:
    async def run(
        self,
        snapshot: ClientFinancialSnapshotSchema,
        trajectory: TaxTrajectoryOutput,
        conversions: ConversionOptimizerOutput,
        tlh: TLHAdvisorOutput,
    ) -> PlanSynthesizerOutput:
        """Synthesize all prior step outputs into a final prioritized plan."""
        personal = snapshot.personal
        accounts = snapshot.accounts

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
        taxable_balance = sum(
            (a.total_value or 0) for a in accounts.taxable_brokerage
        )

        snapshot_summary = {
            "age": personal.age,
            "spouse_age": personal.spouse_age,
            "filing_status": personal.filing_status,
            "state": personal.state,
            "retirement_target_age": personal.retirement_target_age,
            "current_agi": snapshot.income.current_year_agi,
            "total_pretax_balance": pretax_balance,
            "total_roth_balance": roth_balance,
            "total_taxable_balance": taxable_balance,
            "cash_savings": accounts.cash_savings,
            "social_security": (
                snapshot.income.social_security.model_dump()
                if snapshot.income.social_security
                else None
            ),
        }

        input_slice = {
            "snapshot_summary": snapshot_summary,
            "step_1_tax_trajectory": trajectory.model_dump(),
            "step_2_conversion_optimizer": conversions.model_dump(),
            "step_3_tlh_advisor": tlh.model_dump(),
        }

        prompt_template = _PROMPT_PATH.read_text()
        user_message = (
            f"{prompt_template}\n\n"
            f"<input_data>\n{json.dumps(input_slice, indent=2)}\n</input_data>\n\n"
            "Output valid JSON matching the schema exactly."
        )

        result = await self._call_with_retry(user_message)

        # Post-processing assertions
        if not result.priority_actions:
            raise ReasoningStepError(
                "plan_synthesizer",
                "priority_actions is empty — plan must contain at least one action.",
            )
        if not result.disclaimer:
            raise ReasoningStepError(
                "plan_synthesizer",
                "disclaimer field is empty — it must contain the required disclaimer text.",
            )

        return result

    async def _call_with_retry(self, user_message: str) -> PlanSynthesizerOutput:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        for attempt in range(2):
            t0 = time.monotonic()
            try:
                response = await client.messages.create(
                    model=_MODEL,
                    max_tokens=4096,
                    system=_SYSTEM,
                    messages=[{"role": "user", "content": user_message}],
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                raw = response.content[0].text.strip()
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                logger.info(
                    "plan_synthesizer attempt=%d input_tokens=%d output_tokens=%d latency_ms=%d success=True",
                    attempt + 1,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                )
            except Exception as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.error(
                    "plan_synthesizer attempt=%d latency_ms=%d success=False error=%s",
                    attempt + 1,
                    latency_ms,
                    exc,
                )
                if attempt == 1:
                    raise ReasoningStepError("plan_synthesizer", f"Claude API error: {exc}")
                user_message += f"\n\nPrevious attempt failed with API error: {exc}. Please retry."
                continue

            data = extract_json_from_response(raw)
            if data is None:
                if attempt == 1:
                    raise ReasoningStepError(
                        "plan_synthesizer",
                        "Claude did not return parseable JSON after retry.",
                    )
                user_message += (
                    f"\n\nPrevious response was not valid JSON. Raw response: {raw[:500]}. "
                    "Output valid JSON only."
                )
                continue

            try:
                return PlanSynthesizerOutput(**data)
            except Exception as exc:
                if attempt == 1:
                    raise ReasoningStepError(
                        "plan_synthesizer",
                        f"Schema validation failed: {exc}. Data: {data}",
                    )
                user_message += (
                    f"\n\nPrevious JSON failed schema validation: {exc}. "
                    "Fix the output to match the schema exactly."
                )

        raise ReasoningStepError("plan_synthesizer", "Exhausted retries.")
