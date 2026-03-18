"""Step 3: TLH & Asset Location Advisor."""
import json
import logging
import time
from pathlib import Path

import anthropic

from backend.config import settings
from backend.extraction.extractors.base import extract_json_from_response
from backend.models.plan import TLHAdvisorOutput
from backend.models.snapshot import ClientFinancialSnapshotSchema
from backend.reasoning.tax_trajectory import ReasoningStepError

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "tlh_advisor.txt"
_MODEL = "claude-sonnet-4-5"
_SYSTEM = (
    "You are a tax analysis engine for a financial planning software tool. "
    "You perform calculations and analysis only. You do not give financial advice. "
    "Output valid JSON only."
)


class TLHAdvisor:
    async def run(self, snapshot: ClientFinancialSnapshotSchema) -> TLHAdvisorOutput:
        """Check cost basis availability, then call Claude for TLH analysis."""
        accounts = snapshot.accounts

        # Check if any holding has cost basis data
        has_cost_basis = False
        for brokerage in accounts.taxable_brokerage:
            for holding in brokerage.holdings:
                if holding.cost_basis is not None:
                    has_cost_basis = True
                    break
            if has_cost_basis:
                break

        # Short-circuit: no cost basis → cannot do TLH
        if not has_cost_basis:
            return TLHAdvisorOutput(
                tlh_section_complete=False,
                tlh_unavailable_reason=(
                    "No cost basis data is available for any taxable brokerage holding. "
                    "Tax-loss harvesting analysis requires cost basis information to identify "
                    "unrealized losses. Please provide cost basis data to enable TLH recommendations."
                ),
                tlh_opportunities=[],
                total_harvestable_losses=0.0,
                estimated_total_tax_benefit=0.0,
                asset_location_moves=[],
                narrative="TLH analysis could not be completed due to missing cost basis data.",
                confidence=0.0,
                data_gaps=["cost_basis_total", "lot_level_cost_basis"],
            )

        personal = snapshot.personal
        tax_profile = snapshot.tax_profile

        # Build holdings summary for all taxable accounts
        holdings_summary = []
        for brokerage in accounts.taxable_brokerage:
            for holding in brokerage.holdings:
                holdings_summary.append({
                    "institution": brokerage.institution,
                    "symbol": holding.symbol,
                    "description": holding.description,
                    "market_value": holding.market_value,
                    "cost_basis": holding.cost_basis,
                    "unrealized_gain_loss": holding.unrealized_gain_loss,
                    "holding_period": holding.holding_period,
                })

        # Account type summary for asset location
        account_types = {
            "taxable_brokerage": [
                {"institution": b.institution, "total_value": b.total_value}
                for b in accounts.taxable_brokerage
            ],
            "traditional_401k": [
                {"institution": a.institution, "balance": a.balance}
                for a in accounts.traditional_401k
            ],
            "traditional_ira": [
                {"institution": a.institution, "balance": a.balance}
                for a in accounts.traditional_ira
            ],
            "roth_ira": [
                {"institution": a.institution, "balance": a.balance}
                for a in accounts.roth_ira
            ],
            "roth_401k": [
                {"institution": a.institution, "balance": a.balance}
                for a in accounts.roth_401k
            ],
        }

        input_slice = {
            "filing_status": personal.filing_status,
            "state": personal.state,
            "current_agi": snapshot.income.current_year_agi,
            "ltcg_rate": tax_profile.ltcg_rate if tax_profile else 0.15,
            "niit_exposure": tax_profile.niit_exposure if tax_profile else False,
            "niit_threshold_mfj": tax_profile.niit_threshold_mfj if tax_profile else 250000.0,
            "taxable_holdings": holdings_summary,
            "account_types": account_types,
        }

        prompt_template = _PROMPT_PATH.read_text()
        user_message = (
            f"{prompt_template}\n\n"
            f"<input_data>\n{json.dumps(input_slice, indent=2)}\n</input_data>\n\n"
            "Output valid JSON matching the schema exactly."
        )

        return await self._call_with_retry(user_message)

    async def _call_with_retry(self, user_message: str) -> TLHAdvisorOutput:
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
                    "tlh_advisor attempt=%d input_tokens=%d output_tokens=%d latency_ms=%d success=True",
                    attempt + 1,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                )
            except Exception as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.error(
                    "tlh_advisor attempt=%d latency_ms=%d success=False error=%s",
                    attempt + 1,
                    latency_ms,
                    exc,
                )
                if attempt == 1:
                    raise ReasoningStepError("tlh_advisor", f"Claude API error: {exc}")
                user_message += f"\n\nPrevious attempt failed with API error: {exc}. Please retry."
                continue

            data = extract_json_from_response(raw)
            if data is None:
                if attempt == 1:
                    raise ReasoningStepError(
                        "tlh_advisor",
                        "Claude did not return parseable JSON after retry.",
                    )
                user_message += (
                    f"\n\nPrevious response was not valid JSON. Raw response: {raw[:500]}. "
                    "Output valid JSON only."
                )
                continue

            try:
                return TLHAdvisorOutput(**data)
            except Exception as exc:
                if attempt == 1:
                    raise ReasoningStepError(
                        "tlh_advisor",
                        f"Schema validation failed: {exc}. Data: {data}",
                    )
                user_message += (
                    f"\n\nPrevious JSON failed schema validation: {exc}. "
                    "Fix the output to match the schema exactly."
                )

        raise ReasoningStepError("tlh_advisor", "Exhausted retries.")
