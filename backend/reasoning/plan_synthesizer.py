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

# IL state flat income tax rate (applied to conversion income, not retirement distributions)
_IL_STATE_TAX_RATE = 0.0495

# IRS Uniform Lifetime Table factor at age 73
_RMD_FACTOR_73 = 26.5


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
        hsa_balance = sum(
            (a.balance or 0) for a in accounts.hsa
        )

        snapshot_summary = {
            "age": personal.age,
            "spouse_age": personal.spouse_age,
            "filing_status": personal.filing_status,
            "state": personal.state,
            "retirement_target_age": personal.retirement_target_age,
            "years_to_retirement": personal.retirement_target_age - personal.age,
            "current_agi": snapshot.income.current_year_agi,
            "total_pretax_balance": pretax_balance,
            "total_roth_balance": roth_balance,
            "total_taxable_balance": taxable_balance,
            "total_hsa_balance": hsa_balance,
            "cash_savings": accounts.cash_savings,
            "income_projections": [
                p.model_dump() for p in (snapshot.income.projections or [])
            ],
            "social_security": (
                snapshot.income.social_security.model_dump()
                if snapshot.income.social_security
                else None
            ),
            "tax_profile": (
                snapshot.tax_profile.model_dump()
                if snapshot.tax_profile
                else None
            ),
            "rmd_profile": (
                snapshot.rmd_profile.model_dump()
                if snapshot.rmd_profile
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

        # Deterministic post-processing
        result = _post_process(result, snapshot, trajectory, conversions)

        # Assertions
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
                    max_tokens=8192,
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


def _post_process(
    result: PlanSynthesizerOutput,
    snapshot: ClientFinancialSnapshotSchema,
    trajectory: TaxTrajectoryOutput,
    conversions: ConversionOptimizerOutput,
) -> PlanSynthesizerOutput:
    """
    Deterministic post-processing corrections applied after Claude's output.

    Step 1: Fill cumulative_converted as running total across conversion rows.
    Step 2: Override state_tax for IL clients (must equal convert_amount × 0.0495).
    Step 3: Recalculate total_tax and effective_rate_pct per row.
    Step 4: Recalculate conversion table summary totals.
    Step 5: Verify RMD math — log warning if projected_first_rmd is significantly off.
    """
    state = snapshot.personal.state.upper() if snapshot.personal.state else ""
    rows = result.conversion_table.rows

    # Step 1 & 2 & 3: Iterate rows, fix state_tax (IL), recalculate totals
    running_total = 0.0
    for row in rows:
        # Step 2: IL state tax override
        if state == "IL":
            expected_state_tax = round(row.convert_amount * _IL_STATE_TAX_RATE, 2)
            if abs(row.state_tax - expected_state_tax) > 1.0:
                logger.info(
                    "plan_synthesizer post_process: correcting IL state_tax for year=%d "
                    "from %.2f to %.2f",
                    row.year, row.state_tax, expected_state_tax,
                )
                row.state_tax = expected_state_tax

        # Step 3: Recalculate total_tax and effective_rate_pct
        row.total_tax = round(row.federal_tax + row.state_tax, 2)
        if row.convert_amount > 0:
            row.effective_rate_pct = round(row.total_tax / row.convert_amount * 100, 1)

        # Step 1: cumulative_converted running total
        running_total += row.convert_amount
        row.cumulative_converted = round(running_total, 2)

    # Step 4: Recalculate conversion table summary
    ct = result.conversion_table
    ct.total_converted = round(sum(r.convert_amount for r in rows), 2)
    ct.total_tax_paid = round(sum(r.total_tax for r in rows), 2)
    if ct.total_converted > 0:
        ct.blended_effective_rate_pct = round(ct.total_tax_paid / ct.total_converted * 100, 1)

    # Verify total_converted matches step_2 (log discrepancy if > $1)
    step2_total = conversions.total_converted
    if abs(ct.total_converted - step2_total) > 1.0:
        logger.warning(
            "plan_synthesizer post_process: conversion_table.total_converted=%.2f "
            "differs from step_2.total_converted=%.2f by %.2f",
            ct.total_converted, step2_total, abs(ct.total_converted - step2_total),
        )

    # Step 5: RMD math verification
    rmd_profile = snapshot.rmd_profile
    if rmd_profile and rmd_profile.projected_pretax_balance_at_rmd and rmd_profile.projected_first_rmd:
        expected_rmd = rmd_profile.projected_pretax_balance_at_rmd / _RMD_FACTOR_73
        actual_rmd = rmd_profile.projected_first_rmd
        if abs(actual_rmd - expected_rmd) / max(expected_rmd, 1) > 0.10:
            logger.warning(
                "plan_synthesizer post_process: projected_first_rmd=%.2f differs from "
                "expected (balance/26.5)=%.2f by more than 10%% — verify RMD calculation",
                actual_rmd, expected_rmd,
            )

    return result
