"""
Tests for Session 11 fixes:

test_balance_consistent_across_steps:
    _calculate_with_plan_comparison uses trajectory.projected_pretax_at_rmd,
    which must equal _project_balance(pretax, years_to_rmd).

test_conversion_window_complete:
    _ensure_window_complete adds $0 placeholder rows for missing window years;
    placeholder notes mention "Social Security".

test_single_tlh_action:
    _consolidate_tlh_actions merges 3 TLH actions into 1 with summed benefit.

test_irmaa_label_accuracy:
    Three-way IRMAA label: "Likely (with SS income)" when RMD alone < threshold
    but RMD + full SS annual > threshold.

test_effective_rate_label:
    Conversion table column header reads "Total Rate", not "Eff. Rate".

test_no_multiple_tlh_actions:
    _post_process with 3 TLH priority_actions produces exactly 1 TLH action.
"""
import copy
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from backend.models.plan import (
    ConversionOptimizerOutput,
    PlanSynthesizerOutput,
    PriorityAction,
    TaxTrajectoryOutput,
    TLHAdvisorOutput,
)
from backend.models.snapshot import ClientFinancialSnapshotSchema
from backend.reasoning.conversion_optimizer import _ensure_window_complete
from backend.reasoning.plan_synthesizer import (
    _calculate_with_plan_comparison,
    _consolidate_tlh_actions,
    _post_process,
    _project_balance,
)

from backend.tests.test_reasoning import (
    SYNTHETIC_SNAPSHOT,
    TRAJECTORY_RESPONSE,
    CONVERSION_RESPONSE,
    TLH_RESPONSE,
)
from backend.tests.test_plan_synthesizer_v2 import SYNTHESIS_V2_RESPONSE

TEMPLATES_DIR = Path("backend/reports/templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(overrides: dict | None = None) -> ClientFinancialSnapshotSchema:
    data = copy.deepcopy(SYNTHETIC_SNAPSHOT)
    if overrides:
        data.update(overrides)
    return ClientFinancialSnapshotSchema(**data)


def _make_trajectory(overrides: dict | None = None) -> TaxTrajectoryOutput:
    data = copy.deepcopy(TRAJECTORY_RESPONSE)
    if overrides:
        data.update(overrides)
    return TaxTrajectoryOutput(**data)


def _make_conversions(overrides: dict | None = None) -> ConversionOptimizerOutput:
    data = copy.deepcopy(CONVERSION_RESPONSE)
    if overrides:
        data.update(overrides)
    return ConversionOptimizerOutput(**data)


def _make_result(overrides: dict | None = None) -> PlanSynthesizerOutput:
    data = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    if overrides:
        data.update(overrides)
    return PlanSynthesizerOutput(**data)


def _jinja_env() -> Environment:
    from backend.reports.generator import (
        _filter_currency,
        _filter_percentage,
        _filter_irmaa_safe,
        _filter_urgency_class,
    )
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters["currency"] = _filter_currency
    env.filters["percentage"] = _filter_percentage
    env.filters["irmaa_safe"] = _filter_irmaa_safe
    env.filters["urgency_class"] = _filter_urgency_class
    return env


# ---------------------------------------------------------------------------
# Test 1: Balance consistency across steps
# ---------------------------------------------------------------------------

def test_balance_consistent_across_steps():
    """do_nothing_pretax_at_rmd must equal _project_balance(pretax, years_to_rmd)."""
    snapshot = _make_snapshot()
    pretax = (
        sum(a.balance or 0 for a in snapshot.accounts.traditional_401k)
        + sum(a.balance or 0 for a in snapshot.accounts.traditional_ira)
    )
    years_to_rmd = 73 - snapshot.personal.age
    expected = _project_balance(pretax, years_to_rmd)

    # Inject the deterministic value as trajectory.projected_pretax_at_rmd
    # (tax_trajectory.py overrides Claude's output with this value in production)
    trajectory = _make_trajectory({"projected_pretax_at_rmd": expected})
    result = _make_result()
    conversions = _make_conversions()

    result = _calculate_with_plan_comparison(result, snapshot, trajectory, conversions)

    dnc = result.do_nothing_comparison
    assert dnc.do_nothing_pretax_at_rmd == expected, (
        f"do_nothing_pretax_at_rmd ({dnc.do_nothing_pretax_at_rmd}) "
        f"must equal _project_balance result ({expected})"
    )


# ---------------------------------------------------------------------------
# Test 2: Conversion window completeness — placeholder rows with SS note
# ---------------------------------------------------------------------------

def test_conversion_window_complete():
    """Missing window years receive $0 placeholder rows mentioning Social Security."""
    # CONVERSION_RESPONSE has 2028, 2029, 2030; window also includes 2031, 2032
    conversions = _make_conversions()
    window_years = [2028, 2029, 2030, 2031, 2032]

    result = _ensure_window_complete(conversions, window_years)

    covered_years = {e.year for e in result.conversion_plan}
    assert covered_years == set(window_years), (
        f"Expected all window years {window_years}, got {sorted(covered_years)}"
    )

    for entry in result.conversion_plan:
        if entry.year in (2031, 2032):
            assert entry.convert_amount == 0.0, (
                f"Placeholder year {entry.year} must have convert_amount=0.0"
            )
            assert "Social Security" in (entry.net_benefit_note or ""), (
                f"Year {entry.year} placeholder note must mention Social Security; "
                f"got: '{entry.net_benefit_note}'"
            )


# ---------------------------------------------------------------------------
# Test 3: TLH consolidation — multiple actions become one
# ---------------------------------------------------------------------------

def test_single_tlh_action():
    """_consolidate_tlh_actions merges 3 TLH actions into 1 with summed benefit."""
    tlh_actions = [
        PriorityAction(
            priority=1, category="tlh",
            action="Sell 45 shares AMZN.",
            rationale="Position at a loss.",
            estimated_benefit="$588 in tax savings",
            urgency="this_year", confidence="high",
            consequence="If you don't act: $588 in savings forfeited.",
        ),
        PriorityAction(
            priority=2, category="tlh",
            action="Sell 200 shares INTC.",
            rationale="Position at a loss.",
            estimated_benefit="$1,000 in tax savings",
            urgency="this_year", confidence="high",
            consequence="If you don't act: $1,000 in savings forfeited.",
        ),
        PriorityAction(
            priority=3, category="tlh",
            action="Sell 350 shares BND.",
            rationale="Position at a loss.",
            estimated_benefit="$300 in tax savings",
            urgency="this_year", confidence="high",
            consequence="If you don't act: $300 in savings forfeited.",
        ),
    ]
    non_tlh = PriorityAction(
        priority=4, category="roth_conversion",
        action="Convert $182,000 to Roth.",
        rationale="Low income window.",
        estimated_benefit="$140,000 lifetime savings",
        urgency="immediate", confidence="high",
        consequence="If you don't act: $140,000 more in lifetime taxes.",
    )

    result = _consolidate_tlh_actions(tlh_actions + [non_tlh])

    tlh_in_result = [a for a in result if a.category == "tlh"]
    assert len(tlh_in_result) == 1, (
        f"Expected exactly 1 TLH action after consolidation, got {len(tlh_in_result)}"
    )
    # $588 + $1,000 + $300 = $1,888
    assert "$1,888" in tlh_in_result[0].estimated_benefit, (
        f"Consolidated TLH benefit must sum to $1,888; got: '{tlh_in_result[0].estimated_benefit}'"
    )
    # Non-TLH action preserved
    non_tlh_in_result = [a for a in result if a.category != "tlh"]
    assert len(non_tlh_in_result) == 1


# ---------------------------------------------------------------------------
# Test 4: IRMAA three-way label accuracy
# ---------------------------------------------------------------------------

def test_irmaa_label_accuracy():
    """Label is 'Likely (with SS income)' when RMD alone < threshold but RMD + SS > threshold."""
    # Set projected_pretax_at_rmd so first RMD ≈ 136,826
    # 136,826 < 212,000 (RMD alone is safe)
    # 136,826 + 78,000 (SS annual @ $6,500/mo) = 214,826 > 212,000 → "Likely"
    target_rmd = 136_826.0
    target_pretax_at_rmd = round(target_rmd * 26.5, 2)

    snapshot_data = copy.deepcopy(SYNTHETIC_SNAPSHOT)
    snapshot_data["income"]["social_security"]["monthly_benefit_estimate"] = 6500.0
    snapshot = ClientFinancialSnapshotSchema(**snapshot_data)

    trajectory = _make_trajectory({"projected_pretax_at_rmd": target_pretax_at_rmd})

    result = _make_result()
    conversions = _make_conversions({"total_converted": 0.0, "conversion_plan": []})

    result = _calculate_with_plan_comparison(result, snapshot, trajectory, conversions)

    dnc = result.do_nothing_comparison
    assert dnc.do_nothing_irmaa_label == "Likely (with SS income)", (
        f"Expected 'Likely (with SS income)', got '{dnc.do_nothing_irmaa_label}'. "
        f"do_nothing_first_rmd={dnc.do_nothing_first_rmd:.0f}, ss_annual=78000"
    )


# ---------------------------------------------------------------------------
# Test 5: Effective rate column header
# ---------------------------------------------------------------------------

def test_effective_rate_label():
    """Conversion table must use 'Total Rate' not 'Eff. Rate'."""
    env = _jinja_env()
    template = env.get_template("report.html")
    html = template.render(
        advisor_name="Test Advisor",
        client_name="Test Client",
        analysis_date="March 18, 2026",
        snapshot={
            "personal": {
                "age": 54,
                "filing_status": "married_filing_jointly",
                "state": "IL",
                "retirement_target_age": 62,
            },
            "income": {"current_year_agi": 271000.0},
            "accounts": {},
            "tax_profile": {},
            "rmd_profile": {},
        },
        step1={},
        step2={},
        step3={"tlh_section_complete": False, "tlh_unavailable_reason": "No data."},
        step4=SYNTHESIS_V2_RESPONSE,
    )
    assert "Total Rate" in html, "Conversion table column header must read 'Total Rate'"
    assert "Eff. Rate" not in html, "Old 'Eff. Rate' header must not appear in rendered HTML"


# ---------------------------------------------------------------------------
# Test 6: No multiple TLH actions after _post_process (regression)
# ---------------------------------------------------------------------------

def test_no_multiple_tlh_actions():
    """_post_process must consolidate multiple TLH priority_actions into exactly one."""
    result_data = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    result_data["priority_actions"] = [
        {
            "priority": 1, "category": "tlh",
            "action": "Sell AMZN.",
            "rationale": "Loss.",
            "consequence": "If you don't act: $500 forfeited.",
            "estimated_benefit": "$500 in tax savings",
            "urgency": "this_year", "confidence": "high",
        },
        {
            "priority": 2, "category": "tlh",
            "action": "Sell INTC.",
            "rationale": "Loss.",
            "consequence": "If you don't act: $900 forfeited.",
            "estimated_benefit": "$900 in tax savings",
            "urgency": "this_year", "confidence": "high",
        },
        {
            "priority": 3, "category": "tlh",
            "action": "Sell BND.",
            "rationale": "Loss.",
            "consequence": "If you don't act: $200 forfeited.",
            "estimated_benefit": "$200 in tax savings",
            "urgency": "this_year", "confidence": "high",
        },
        {
            "priority": 4, "category": "roth_conversion",
            "action": "Convert $182,000 to Roth.",
            "rationale": "Low income window.",
            "consequence": "If you don't act: $140,000 more in lifetime taxes.",
            "estimated_benefit": "$140,000 in lifetime savings",
            "urgency": "immediate", "confidence": "high",
        },
    ]

    result = PlanSynthesizerOutput(**result_data)
    snapshot = _make_snapshot()
    trajectory = _make_trajectory()
    conversions = _make_conversions()

    processed = _post_process(result, snapshot, trajectory, conversions)

    tlh_actions = [a for a in processed.priority_actions if a.category == "tlh"]
    assert len(tlh_actions) == 1, (
        f"Expected exactly 1 TLH action after _post_process, got {len(tlh_actions)}: "
        + str([a.action for a in tlh_actions])
    )
