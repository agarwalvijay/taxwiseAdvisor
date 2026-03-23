"""Step 2: Roth Conversion Optimizer."""
import datetime
import json
import logging
import time
from pathlib import Path

import anthropic

from backend.config import settings
from backend.extraction.extractors.base import extract_json_from_response
from backend.models.plan import ConversionOptimizerOutput, TaxTrajectoryOutput, YearlyConversion
from backend.models.snapshot import ClientFinancialSnapshotSchema
from backend.reasoning.tax_trajectory import ReasoningStepError

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "conversion_optimizer.txt"


def _project_balance(current_balance: float, years: int, annual_growth_rate: float = 0.06) -> float:
    """Compound a balance forward at a fixed annual growth rate. Always deterministic."""
    return round(current_balance * ((1 + annual_growth_rate) ** years), 2)


def _ensure_window_complete(
    result: ConversionOptimizerOutput,
    window_years: list[int],
) -> ConversionOptimizerOutput:
    """Add $0 placeholder rows for any conversion window years missing from conversion_plan."""
    if not window_years:
        return result
    covered = {entry.year for entry in result.conversion_plan}
    missing = sorted(set(window_years) - covered)
    for yr in missing:
        result.conversion_plan.append(
            YearlyConversion(
                year=yr,
                convert_amount=0.0,
                estimated_federal_tax=0.0,
                estimated_state_tax=0.0,
                bracket_used="no conversion planned",
                post_conversion_agi=0.0,
                irmaa_safe=True,
                aca_safe=True,
                net_benefit_note=(
                    "Conversion amount not modeled — Social Security start age and benefit "
                    "unknown for this year. Provide SS data to generate a personalized "
                    "conversion amount for this year."
                ),
            )
        )
    if missing:
        result.conversion_plan.sort(key=lambda e: e.year)
        logger.info(
            "conversion_optimizer _ensure_window_complete: added %d placeholder rows for years %s",
            len(missing),
            missing,
        )
    return result


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

        # Pre-calculate conversion window and balance projection (Fix 2 + Fix 4)
        current_year = datetime.date.today().year
        retirement_year = current_year + (personal.retirement_target_age - personal.age)
        rmd_start_year = current_year + (73 - personal.age)
        window_years = list(range(retirement_year, rmd_start_year))
        projected_pretax_at_rmd = _project_balance(pretax_balance, 73 - personal.age)

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
            "conversion_window_years": window_years,
            "retirement_year": retirement_year,
            "rmd_start_year": rmd_start_year,
            "projected_pretax_at_rmd": projected_pretax_at_rmd,
        }

        prompt_template = _PROMPT_PATH.read_text()
        window_instruction = (
            f"\n\n## MANDATORY CONVERSION WINDOW\n\n"
            f"The client's conversion window runs from {retirement_year} through {rmd_start_year - 1} "
            f"({len(window_years)} years). You MUST include a conversion_plan entry for EVERY year: "
            f"{', '.join(str(y) for y in window_years)}. "
            f"Set convert_amount=0.0 for any year where conversion is not recommended.\n\n"
            f"## HARD CAP — TOTAL CONVERSION LIMIT\n\n"
            f"The total_pretax_balance available to convert is ${pretax_balance:,.2f}. "
            f"The SUM of all convert_amount values across ALL years MUST NOT exceed ${pretax_balance:,.2f}. "
            f"total_converted in your output must equal exactly sum(convert_amounts) and be ≤ ${pretax_balance:,.2f}.\n"
        )
        user_message = (
            f"{prompt_template}{window_instruction}\n\n"
            f"<input_data>\n{json.dumps(input_slice, indent=2)}\n</input_data>\n\n"
            "Output valid JSON matching the schema exactly."
        )

        result = await self._call_with_retry(user_message)

        # Deterministic window completeness check (Fix 2)
        result = _ensure_window_complete(result, window_years)

        # Post-processing assertions
        for entry in result.conversion_plan:
            if entry.convert_amount < 0:
                raise ReasoningStepError(
                    "conversion_optimizer",
                    f"convert_amount for year {entry.year} is negative: {entry.convert_amount}",
                )

        if result.total_converted > pretax_balance:
            # Deterministic correction: scale every conversion down proportionally so
            # total_converted ≤ pretax_balance. Preserves year ordering and tax ratios.
            scale = pretax_balance / result.total_converted
            for entry in result.conversion_plan:
                entry.convert_amount = round(entry.convert_amount * scale, 2)
                entry.estimated_federal_tax = round(entry.estimated_federal_tax * scale, 2)
                entry.estimated_state_tax = round(entry.estimated_state_tax * scale, 2)
            result.total_converted = round(
                sum(e.convert_amount for e in result.conversion_plan), 2
            )
            result.estimated_total_tax_on_conversions = round(
                sum(e.estimated_federal_tax + e.estimated_state_tax for e in result.conversion_plan),
                2,
            )
            logger.warning(
                "conversion_optimizer: total_converted exceeded pretax_balance (%.2f > %.2f); "
                "scaled down by factor %.4f to %.2f",
                result.total_converted / scale,
                pretax_balance,
                scale,
                result.total_converted,
            )

        if result.liquidity_check_passed:
            if result.estimated_total_tax_on_conversions > taxable_brokerage_total:
                # Flip the flag rather than failing the plan — the plan synthesizer will
                # surface this as a data gap / liquidity constraint in its narrative.
                result.liquidity_check_passed = False
                result.liquidity_note = (
                    (result.liquidity_note or "") +
                    f" Liquidity flag corrected: estimated tax ({result.estimated_total_tax_on_conversions:,.0f}) "
                    f"exceeds taxable brokerage ({taxable_brokerage_total:,.0f})."
                ).strip()
                logger.warning(
                    "conversion_optimizer: liquidity_check_passed=True but tax %.2f > brokerage %.2f; "
                    "overriding to False",
                    result.estimated_total_tax_on_conversions,
                    taxable_brokerage_total,
                )

        return result

    async def _call_with_retry(self, user_message: str) -> ConversionOptimizerOutput:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        for attempt in range(2):
            t0 = time.monotonic()
            try:
                response = await client.messages.create(
                    model=_MODEL,
                    max_tokens=6000,
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
