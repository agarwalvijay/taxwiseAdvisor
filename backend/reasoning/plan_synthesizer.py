"""Step 4: Plan Synthesizer."""
import datetime
import json
import logging
import re as _re
import time
from pathlib import Path

import anthropic

from backend.config import settings
from backend.extraction.extractors.base import extract_json_from_response
from backend.models.plan import (
    ConversionOptimizerOutput,
    PriorityAction,
    PlanSynthesizerOutput,
    TaxTrajectoryOutput,
    TLHAdvisorOutput,
    YearlyConversionRow,
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

# 2026 MFJ federal tax brackets (bracket top → rate) for illustrative table
_BRACKETS_2026_MFJ = [
    (23_200, 0.10),
    (94_300, 0.12),
    (201_050, 0.22),
    (383_900, 0.24),
    (487_450, 0.32),
    (731_200, 0.35),
    (float("inf"), 0.37),
]
_STANDARD_DEDUCTION_MFJ_2026 = 30_000
_IRMAA_TIER1_MFJ = 212_000
_IRMAA_ANNUAL_COST_MFJ = 1_776  # Part B + Part D combined MFJ annual surcharge (Tier 1)
_DISCLAIMER_TEXT = (
    "This analysis was prepared by [Advisor Name] using TaxWise Advisor software as a planning tool. "
    "It is intended to support informed discussion between you and your financial advisor and does not "
    "constitute independent financial, tax, or legal advice. Tax laws change frequently and individual "
    "circumstances vary. The projections and recommendations in this report are based on information "
    "provided as of [Report Date] and involve assumptions about future income, tax rates, and investment "
    "returns that may not materialize. Before implementing any strategy described in this report, please "
    "consult with a qualified tax professional. Your advisor is responsible for the recommendations made to you."
)


def _project_balance(current_balance: float, years: int, annual_growth_rate: float = 0.06) -> float:
    """Compound a balance forward at a fixed annual growth rate. Always deterministic."""
    return round(current_balance * ((1 + annual_growth_rate) ** years), 2)


# Keywords that indicate a data-collection request masquerading as a priority action
DATA_COLLECTION_KEYWORDS = [
    "gather", "collect", "provide", "upload", "obtain",
    "request", "submit", "consult", "contact", "ask",
    "reach out", "find out", "determine", "clarify",
    "confirm", "verify your", "check with",
]


def _is_data_collection_action(action_text: str) -> bool:
    """Return True if the action text is a data-collection request, not a tax-optimization move."""
    lower = action_text.lower()
    return any(kw in lower for kw in DATA_COLLECTION_KEYWORDS)


def _extract_dollar_amount(benefit_str: str) -> float:
    """Return largest dollar figure found in an estimated_benefit string."""
    matches = _re.findall(r'\$[\d,]+', benefit_str)
    if not matches:
        return 0.0
    return max(float(m.replace('$', '').replace(',', '')) for m in matches)


def _consolidate_tlh_actions(actions: list[PriorityAction]) -> list[PriorityAction]:
    """Merge multiple TLH priority actions into a single consolidated action (Fix 3)."""
    tlh_actions = [a for a in actions if a.category == "tlh"]
    non_tlh_actions = [a for a in actions if a.category != "tlh"]

    if len(tlh_actions) <= 1:
        return actions  # nothing to consolidate

    # Sum estimated benefits across all TLH actions
    total_savings = sum(_extract_dollar_amount(a.estimated_benefit) for a in tlh_actions)
    if total_savings > 0:
        benefit_str = f"${total_savings:,.0f} in total tax savings from all positions"
    else:
        benefit_str = tlh_actions[0].estimated_benefit

    # Best consequence (largest dollar mentioned)
    best_consequence = max(
        tlh_actions, key=lambda a: _extract_dollar_amount(a.consequence)
    ).consequence or tlh_actions[0].consequence

    consolidated = PriorityAction(
        priority=0,  # will be re-numbered in step 8
        category="tlh",
        action=" | ".join(a.action for a in tlh_actions),
        rationale=tlh_actions[0].rationale,
        estimated_benefit=benefit_str,
        urgency="this_year",
        confidence="high",
        consequence=best_consequence,
    )

    result = non_tlh_actions + [consolidated]
    logger.info(
        "plan_synthesizer _consolidate_tlh_actions: consolidated %d TLH actions into 1",
        len(tlh_actions),
    )
    return result


def _calc_federal_tax_mfj_2026(agi: float) -> float:
    """Approximate 2026 MFJ federal income tax given AGI (after standard deduction)."""
    taxable = max(0.0, agi - _STANDARD_DEDUCTION_MFJ_2026)
    tax = 0.0
    prev = 0.0
    for bracket_top, rate in _BRACKETS_2026_MFJ:
        if taxable <= prev:
            break
        in_bracket = min(taxable, bracket_top) - prev
        tax += in_bracket * rate
        prev = bracket_top
    return round(tax, 2)


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

        # Pre-calculate projected_pretax_at_rmd deterministically (Fix 4)
        years_to_rmd = max(0, 73 - personal.age)
        projected_pretax_at_rmd = _project_balance(pretax_balance, years_to_rmd)

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
            "projected_pretax_at_rmd": projected_pretax_at_rmd,
            "years_to_rmd": years_to_rmd,
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
        precalc_note = (
            f"\n\nIMPORTANT — PRE-CALCULATED VALUES (use exactly as provided, do NOT recalculate):\n"
            f"- projected_pretax_at_rmd: ${projected_pretax_at_rmd:,.0f} "
            f"(current pre-tax ${pretax_balance:,.0f} compounded at 6% for {years_to_rmd} years to age 73)\n"
            f"\nDO NOT include data-gathering steps in priority_actions. "
            f"Only include actionable tax optimization strategies.\n"
        )
        user_message = (
            f"{prompt_template}{precalc_note}\n\n"
            f"<input_data>\n{json.dumps(input_slice, indent=2)}\n</input_data>\n\n"
            "Output valid JSON matching the schema exactly."
        )

        result = await self._call_with_retry(user_message)

        # Deterministic post-processing
        result = _post_process(result, snapshot, trajectory, conversions)

        # Deterministic with-plan comparison (Fix 1)
        result = _calculate_with_plan_comparison(result, snapshot, trajectory, conversions)

        # Final deterministic sync to prevent cross-section contradictions in the report.
        result = _enforce_consistency(result, snapshot)

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

    Step 0: Pass-through placeholder rows from conversions.conversion_plan for any
            window years missing from the conversion table (Fix 2).
    Step 1: Fill cumulative_converted as running total across conversion rows.
    Step 2: Override state_tax for IL clients (must equal convert_amount × 0.0495).
    Step 3: Recalculate total_tax and effective_rate_pct per row.
    Step 4: Recalculate conversion table summary totals.
    Step 5: Verify RMD math — log warning if projected_first_rmd is significantly off.
    Step 6: Generate illustrative conversion rows when window years exist but rows are empty.
    Step 7: Filter data-collection actions from priority_actions.
    Step 7.5: Consolidate multiple TLH actions into one (Fix 3).
    Step 8: Re-sort remaining priority_actions by estimated dollar impact; re-number.
    NOTE: DoNothingComparison fields are set by _calculate_with_plan_comparison(),
          called separately in run() after _post_process().
    """
    state = snapshot.personal.state.upper() if snapshot.personal.state else ""

    # Step 0: Treat step_2 conversion_plan as authoritative table source.
    # Rebuild rows from step_2 to avoid drift between narrative and math sections.
    ct_pre = result.conversion_table
    if conversions.conversion_plan:
        ct_pre.rows = [
            YearlyConversionRow(
                year=entry.year,
                pre_conversion_income=0.0,
                convert_amount=entry.convert_amount,
                post_conversion_agi=entry.post_conversion_agi,
                federal_tax=entry.estimated_federal_tax,
                state_tax=entry.estimated_state_tax,
                total_tax=round(entry.estimated_federal_tax + entry.estimated_state_tax, 2),
                effective_rate_pct=0.0,
                cumulative_converted=0.0,  # recalculated in Step 1
                irmaa_safe=entry.irmaa_safe,
                note=entry.net_benefit_note if entry.net_benefit_note else None,
            )
            for entry in sorted(conversions.conversion_plan, key=lambda e: e.year)
        ]
        logger.info(
            "plan_synthesizer _post_process step0: rebuilt conversion_table from step_2 rows (count=%d)",
            len(ct_pre.rows),
        )

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

    # Step 6: Illustrative conversion table when rows are empty but window years exist
    if not ct.rows and trajectory.conversion_window_years:
        is_il = state == "IL"
        illustrative_rows: list[YearlyConversionRow] = []
        running = 0.0
        for yr in sorted(trajectory.conversion_window_years):
            conv = float(_IRMAA_TIER1_MFJ)
            fed = _calc_federal_tax_mfj_2026(conv)
            st_tax = round(conv * _IL_STATE_TAX_RATE, 2) if is_il else 0.0
            total_t = round(fed + st_tax, 2)
            eff = round(total_t / conv * 100, 1) if conv > 0 else 0.0
            running += conv
            illustrative_rows.append(
                YearlyConversionRow(
                    year=yr,
                    pre_conversion_income=0.0,
                    convert_amount=conv,
                    post_conversion_agi=conv,
                    federal_tax=fed,
                    state_tax=st_tax,
                    total_tax=total_t,
                    effective_rate_pct=eff,
                    cumulative_converted=round(running, 2),
                    irmaa_safe=True,
                    note=None,
                )
            )
        ct.rows = illustrative_rows
        ct.total_converted = round(running, 2)
        ct.total_tax_paid = round(sum(r.total_tax for r in illustrative_rows), 2)
        if ct.total_converted > 0:
            ct.blended_effective_rate_pct = round(
                ct.total_tax_paid / ct.total_converted * 100, 1
            )
        ct.illustrative = True
        if is_il and not ct.il_state_tax_note:
            ct.il_state_tax_note = (
                "Illinois taxes Roth conversions as ordinary income at 4.95%, "
                "but future Roth IRA withdrawals are Illinois state-tax-free — "
                "this asymmetry increases the net lifetime benefit of each conversion."
            )
        logger.info(
            "plan_synthesizer post_process: generated %d illustrative conversion rows "
            "for window years %s",
            len(illustrative_rows),
            trajectory.conversion_window_years,
        )

    # Step 7: Filter data-collection actions from priority_actions

    if result.priority_actions:
        filtered = [a for a in result.priority_actions if not _is_data_collection_action(a.action)]
        if filtered:
            removed = len(result.priority_actions) - len(filtered)
            if removed:
                logger.info(
                    "plan_synthesizer post_process: removed %d data-collection action(s) from priority_actions",
                    removed,
                )
            result.priority_actions = filtered
        else:
            logger.warning(
                "plan_synthesizer post_process: all priority_actions were data-collection — keeping original"
            )

    # Step 7.5: Consolidate multiple TLH actions into one (Fix 3)
    if result.priority_actions:
        result.priority_actions = _consolidate_tlh_actions(result.priority_actions)

    # Step 8: Re-sort priority_actions by estimated lifetime dollar impact; re-number
    if result.priority_actions:
        result.priority_actions.sort(
            key=lambda a: _extract_dollar_amount(a.estimated_benefit),
            reverse=True,
        )
        for i, action in enumerate(result.priority_actions):
            action.priority = i + 1

    return result


def _calculate_with_plan_comparison(
    result: PlanSynthesizerOutput,
    snapshot: ClientFinancialSnapshotSchema,
    trajectory: TaxTrajectoryOutput,
    conversions: ConversionOptimizerOutput,
) -> PlanSynthesizerOutput:
    """
    Deterministic calculation of DoNothingComparison with_plan_* fields.
    Always overrides ALL comparison fields after Claude returns.

    Steps:
    1. total_converted from ct.rows or conversions.total_converted
    2. do_nothing_pretax = trajectory.projected_pretax_at_rmd
    3. with_plan_pretax = max(0, do_nothing_pretax - total_converted × 1.5)
    4. RMD tax at rmd_bracket_estimate (federal only — IL exempts retirement income)
    5. IRMAA: (first_rmd + ss_annual × 0.85) > IRMAA_TIER1_MFJ threshold
    6. Roth at death: compound current_roth forward; with_plan adds total_converted
    7. lifetime_savings range string: annual_savings × 20 × [0.7, 1.3]
    8. Override ALL dnc fields (legacy + canonical)
    """
    ct = result.conversion_table
    dnc = result.do_nothing_comparison
    personal = snapshot.personal

    # Step 1: total_converted
    total_converted = ct.total_converted or conversions.total_converted or 0.0

    # Step 2: do_nothing pre-tax balance at RMD age
    do_nothing_pretax = trajectory.projected_pretax_at_rmd or 0.0

    # Step 3: with_plan pre-tax balance (1.5× accounts for forgone Roth growth)
    with_plan_pretax = max(0.0, do_nothing_pretax - total_converted * 1.5)

    # Step 4: RMD calculations (IL exempts retirement income — state_rmd_rate = 0)
    do_nothing_first_rmd = round(do_nothing_pretax / _RMD_FACTOR_73, 2)
    with_plan_first_rmd = round(with_plan_pretax / _RMD_FACTOR_73, 2)

    federal_rmd_rate = trajectory.rmd_bracket_estimate or 0.24
    do_nothing_rmd_annual_tax = round(do_nothing_first_rmd * federal_rmd_rate, 2)
    with_plan_rmd_annual_tax = round(with_plan_first_rmd * federal_rmd_rate, 2)

    # Step 5: IRMAA three-way label logic (Fix 4)
    ss = snapshot.income.social_security if snapshot.income else None
    ss_annual = float(ss.monthly_benefit_estimate * 12) if (ss and ss.monthly_benefit_estimate) else 0.0

    def _irmaa_check(rmd_alone: float, rmd_plus_ss: float, threshold: float) -> tuple[str, bool]:
        if rmd_alone > threshold:
            return "Yes", True
        elif rmd_plus_ss > threshold:
            return "Likely (with SS income)", True
        else:
            return "No", False

    do_nothing_irmaa_label, do_nothing_irmaa_triggered = _irmaa_check(
        do_nothing_first_rmd,
        do_nothing_first_rmd + ss_annual,
        _IRMAA_TIER1_MFJ,
    )
    with_plan_irmaa_label, with_plan_irmaa_triggered = _irmaa_check(
        with_plan_first_rmd,
        with_plan_first_rmd + ss_annual,
        _IRMAA_TIER1_MFJ,
    )

    do_nothing_irmaa_cost = float(_IRMAA_ANNUAL_COST_MFJ) if do_nothing_irmaa_triggered else None
    with_plan_irmaa_cost = float(_IRMAA_ANNUAL_COST_MFJ) if with_plan_irmaa_triggered else None

    # Step 6: Roth balance at RMD age (death proxy)
    years_to_rmd = trajectory.years_until_rmd or max(0, 73 - personal.age)
    current_roth = sum(
        (a.balance or 0) for a in snapshot.accounts.roth_ira
    ) + sum(
        (a.balance or 0) for a in snapshot.accounts.roth_401k
    )
    do_nothing_roth = round(current_roth * (1.06 ** years_to_rmd), 2)
    with_plan_roth = round((current_roth + total_converted) * (1.06 ** years_to_rmd), 2)

    # Step 7: Lifetime savings range string
    annual_savings = do_nothing_rmd_annual_tax - with_plan_rmd_annual_tax
    savings_20yr = annual_savings * 20
    low = round(savings_20yr * 0.7)
    high = round(savings_20yr * 1.3)
    lifetime_savings_str = f"${low:,.0f} \u2013 ${high:,.0f}"

    heir_benefit_delta = round(with_plan_roth - do_nothing_roth)
    heir_benefit_str = (
        f"Heirs inherit ~${with_plan_roth:,.0f} in tax-free Roth assets vs. "
        f"~${do_nothing_roth:,.0f} without the plan \u2014 a ~${heir_benefit_delta:,.0f} advantage."
    )

    # Step 8: Override ALL dnc fields (legacy + canonical)
    dnc.pretax_at_73_without_plan = round(do_nothing_pretax, 2)
    dnc.pretax_at_73_with_plan = round(with_plan_pretax, 2)
    dnc.first_rmd_without_plan = do_nothing_first_rmd
    dnc.first_rmd_with_plan = with_plan_first_rmd
    dnc.annual_rmd_tax_without_plan = do_nothing_rmd_annual_tax
    dnc.annual_rmd_tax_with_plan = with_plan_rmd_annual_tax
    dnc.roth_at_73_without_plan = do_nothing_roth
    dnc.roth_at_73_with_plan = with_plan_roth

    dnc.do_nothing_pretax_at_rmd = round(do_nothing_pretax, 2)
    dnc.do_nothing_first_rmd = do_nothing_first_rmd
    dnc.do_nothing_rmd_annual_tax = do_nothing_rmd_annual_tax
    dnc.do_nothing_irmaa_triggered = do_nothing_irmaa_triggered
    dnc.do_nothing_irmaa_annual_cost = do_nothing_irmaa_cost
    dnc.do_nothing_roth_at_death = do_nothing_roth

    dnc.with_plan_pretax_at_rmd = round(with_plan_pretax, 2)
    dnc.with_plan_first_rmd = with_plan_first_rmd
    dnc.with_plan_rmd_annual_tax = with_plan_rmd_annual_tax
    dnc.with_plan_irmaa_triggered = with_plan_irmaa_triggered
    dnc.with_plan_irmaa_annual_cost = with_plan_irmaa_cost
    dnc.with_plan_roth_at_death = with_plan_roth
    dnc.with_plan_lifetime_savings = lifetime_savings_str
    dnc.heir_benefit = heir_benefit_str
    dnc.do_nothing_irmaa_label = do_nothing_irmaa_label
    dnc.with_plan_irmaa_label = with_plan_irmaa_label
    dnc.irmaa_triggered = do_nothing_irmaa_triggered

    logger.info(
        "plan_synthesizer _calculate_with_plan_comparison: "
        "do_nothing_pretax=%.0f with_plan_pretax=%.0f total_converted=%.0f "
        "annual_savings=%.0f lifetime_savings=%s",
        do_nothing_pretax, with_plan_pretax, total_converted,
        annual_savings, lifetime_savings_str,
    )

    return result


def _enforce_consistency(
    result: PlanSynthesizerOutput,
    snapshot: ClientFinancialSnapshotSchema,
) -> PlanSynthesizerOutput:
    """
    Final deterministic harmonization to prevent contradictory report sections.
    """
    ct = result.conversion_table
    dnc = result.do_nothing_comparison

    # Keep legacy + canonical IRMAA fields aligned.
    dnc.irmaa_triggered = dnc.do_nothing_irmaa_triggered

    # Use deterministic annual-savings math for the legacy single-value savings field.
    annual_savings = max(0.0, dnc.annual_rmd_tax_without_plan - dnc.annual_rmd_tax_with_plan)
    dnc.estimated_lifetime_tax_savings = round(annual_savings * 20, 2)

    # Ensure legal text remains stable for reporting/compliance.
    result.disclaimer = _DISCLAIMER_TEXT

    # Deterministic summary text grounded in computed values.
    years = sorted(r.year for r in ct.rows)
    if years:
        start_year, end_year = years[0], years[-1]
    else:
        start_year = end_year = None

    if dnc.with_plan_lifetime_savings:
        savings_phrase = dnc.with_plan_lifetime_savings
    else:
        savings_phrase = f"${dnc.estimated_lifetime_tax_savings:,.0f}"

    conversion_desc = (
        f"${ct.total_converted:,.0f} over {len(years)} year(s)"
        if years else
        "$0 across 0 years"
    )
    window_desc = f"{start_year}-{end_year}" if start_year and end_year else "the conversion window"
    result.executive_summary = (
        f"Projected pre-tax balance at age 73 is ${dnc.pretax_at_73_without_plan:,.0f}, with a first-year "
        f"RMD of ${dnc.first_rmd_without_plan:,.0f}. This plan models Roth conversions of {conversion_desc} "
        f"during {window_desc} at a blended total rate of {ct.blended_effective_rate_pct:.1f}%. "
        f"Estimated lifetime tax savings: {savings_phrase}."
    )
    result.narrative = (
        f"Without action, projected pre-tax balance at age 73 is ${dnc.pretax_at_73_without_plan:,.0f}. "
        f"With the modeled conversion plan, projected pre-tax balance at age 73 falls to "
        f"${dnc.pretax_at_73_with_plan:,.0f}, reducing first-year RMD by "
        f"${max(0.0, dnc.first_rmd_without_plan - dnc.first_rmd_with_plan):,.0f}. "
        f"Estimated lifetime tax savings: {savings_phrase}."
    )

    # Normalize Roth-conversion action text so it reflects computed totals.
    current_year = datetime.date.today().year
    for action in result.priority_actions:
        if action.category != "roth_conversion":
            continue
        if years:
            start_year = years[0]
            end_year = years[-1]
            avg = (ct.total_converted / len(years)) if years else 0.0
            action.action = (
                f"Implement a systematic Roth conversion plan from {start_year} through {end_year}, "
                f"converting approximately ${avg:,.0f} per year (total ${ct.total_converted:,.0f}) "
                f"from pre-tax accounts to Roth accounts."
            )
            action.urgency = "this_year" if start_year <= current_year else "multi_year"
        action.rationale = (
            f"Modeled conversions reduce projected pre-tax balance at age 73 from "
            f"${dnc.pretax_at_73_without_plan:,.0f} to ${dnc.pretax_at_73_with_plan:,.0f}, "
            f"lowering projected RMD tax and improving Roth tax-free balance growth."
        )
        action.consequence = (
            "If you don't act: projected RMD-related tax remains higher and the plan's modeled "
            f"lifetime tax savings of {savings_phrase} may not be realized."
        )
        action.estimated_benefit = (
            f"{savings_phrase} estimated lifetime tax savings with lower projected RMD burden."
        )

    return result
