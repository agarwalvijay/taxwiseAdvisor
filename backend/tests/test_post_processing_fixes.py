"""
Tests for post-processing fixes (4 new behaviours):

test_comparison_not_zero:
    with_plan_* fields are strictly less than do_nothing_* where expected,
    and roth_at_73_with_plan > roth_at_73_without_plan.

test_bracket_shows_value:
    Marginal bracket in Financial Snapshot section is not "—" or "0%",
    and has a "%" suffix, for both fallback paths.

test_tlh_not_priority_one:
    When Roth conversion estimated_benefit >> TLH estimated_benefit,
    Roth is re-sorted to priority 1 regardless of Claude's original ordering.

test_no_repeated_notes:
    Illustrative banner appears exactly once; row Notes column is blank ("—").
"""
import copy
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from backend.models.plan import (
    ConversionOptimizerOutput,
    ConversionTableSummary,
    DoNothingComparison,
    PlanSynthesizerOutput,
    TaxTrajectoryOutput,
    TLHAdvisorOutput,
    YearlyConversionRow,
)
from backend.models.snapshot import ClientFinancialSnapshotSchema
from backend.reasoning.plan_synthesizer import _post_process, _calculate_with_plan_comparison

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

def _make_snapshot() -> ClientFinancialSnapshotSchema:
    return ClientFinancialSnapshotSchema(**SYNTHETIC_SNAPSHOT)


def _make_trajectory() -> TaxTrajectoryOutput:
    return TaxTrajectoryOutput(**TRAJECTORY_RESPONSE)


def _make_conversions() -> ConversionOptimizerOutput:
    return ConversionOptimizerOutput(**CONVERSION_RESPONSE)


def _make_tlh() -> TLHAdvisorOutput:
    return TLHAdvisorOutput(**TLH_RESPONSE)


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


def _render_template(step4: dict, snapshot_data: dict | None = None, step1_data: dict | None = None) -> str:
    env = _jinja_env()
    template = env.get_template("report.html")
    return template.render(
        advisor_name="Jane Advisor",
        client_name="John Client",
        analysis_date="March 18, 2026",
        snapshot=snapshot_data or {
            "personal": {"age": 54, "filing_status": "married_filing_jointly", "state": "IL", "retirement_target_age": 62},
            "income": {"current_year_agi": 271000.0},
            "accounts": {
                "traditional_ira": [{"institution": "Vanguard", "balance": 1057000.0}],
                "roth_ira": [{"institution": "Vanguard", "balance": 88000.0}],
                "taxable_brokerage": [{"institution": "Fidelity", "total_value": 487250.0}],
            },
            "tax_profile": {},  # intentionally empty to force step1 fallback
            "rmd_profile": {"years_until_rmd": 19},
        },
        step1=step1_data or {},
        step2={},
        step3={"tlh_section_complete": False, "tlh_unavailable_reason": "No data."},
        step4=step4,
    )


# ---------------------------------------------------------------------------
# Test 1: Comparison table with_plan fields differ from do_nothing values
# ---------------------------------------------------------------------------

def test_comparison_not_zero():
    result = _make_result()
    snapshot = _make_snapshot()
    trajectory = _make_trajectory()
    conversions = _make_conversions()

    processed = _post_process(result, snapshot, trajectory, conversions)
    processed = _calculate_with_plan_comparison(processed, snapshot, trajectory, conversions)
    dnc = processed.do_nothing_comparison

    # with_plan pre-tax at 73 must be less than do_nothing (conversions reduce it)
    assert dnc.pretax_at_73_with_plan < dnc.pretax_at_73_without_plan, (
        f"pretax_at_73_with_plan ({dnc.pretax_at_73_with_plan}) should be < "
        f"pretax_at_73_without_plan ({dnc.pretax_at_73_without_plan})"
    )

    # first RMD is smaller when pre-tax balance is reduced
    assert dnc.first_rmd_with_plan < dnc.first_rmd_without_plan

    # Roth balance at 73 is higher with the plan (includes converted amounts, grown at 6%)
    assert dnc.roth_at_73_with_plan > dnc.roth_at_73_without_plan

    # Annual RMD tax is lower with the plan
    assert dnc.annual_rmd_tax_with_plan < dnc.annual_rmd_tax_without_plan

    # No column should be identical (except IRMAA which is boolean)
    assert dnc.pretax_at_73_with_plan != dnc.pretax_at_73_without_plan
    assert dnc.first_rmd_with_plan != dnc.first_rmd_without_plan
    assert dnc.roth_at_73_with_plan != dnc.roth_at_73_without_plan

    # Sanity: with_plan values must be positive
    assert dnc.pretax_at_73_with_plan > 0
    assert dnc.first_rmd_with_plan > 0
    assert dnc.roth_at_73_with_plan > 0


# ---------------------------------------------------------------------------
# Test 2: Marginal bracket renders with a real value (not "—" or "0%")
# ---------------------------------------------------------------------------

def test_bracket_shows_value():
    # Path 1: bracket comes from step1.current_bracket (tax_profile empty)
    step4 = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    snapshot_no_tax_profile = {
        "personal": {"age": 54, "filing_status": "married_filing_jointly", "state": "IL", "retirement_target_age": 62},
        "income": {"current_year_agi": 271000.0},
        "accounts": {},
        "tax_profile": {},
        "rmd_profile": {},
    }
    html1 = _render_template(step4, snapshot_data=snapshot_no_tax_profile, step1_data={"current_bracket": 0.32})
    assert "32.0%" in html1, "Bracket from step1.current_bracket must render as 32.0%"
    assert "Marginal Federal Bracket" in html1

    # Path 2: bracket comes from tax_profile.current_marginal_bracket (no step1 bracket)
    snapshot_with_bracket = {
        "personal": {"age": 54, "filing_status": "married_filing_jointly", "state": "IL", "retirement_target_age": 62},
        "income": {"current_year_agi": 271000.0},
        "accounts": {},
        "tax_profile": {"current_marginal_bracket": 0.24},
        "rmd_profile": {},
    }
    html2 = _render_template(step4, snapshot_data=snapshot_with_bracket, step1_data={})
    assert "24.0%" in html2, "Bracket from tax_profile must render as 24.0%"

    # Neither path should show "—" or "0%" when bracket data is present
    assert "—" not in html1.split("Marginal Federal Bracket")[1].split("</div>")[0]
    assert "—" not in html2.split("Marginal Federal Bracket")[1].split("</div>")[0]

    # Fallback path: both sources missing — renders the text fallback
    html3 = _render_template(step4, snapshot_data=snapshot_no_tax_profile, step1_data={})
    assert "See tax trajectory analysis" in html3


# ---------------------------------------------------------------------------
# Test 3: Roth conversion action is priority 1 after sorting by dollar impact
# ---------------------------------------------------------------------------

def test_tlh_not_priority_one():
    """Claude returns TLH as priority 1, but after sorting by dollar amount Roth wins."""
    result_data = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    # Swap Claude's ordering: TLH first (priority 1), Roth second (priority 2)
    result_data["priority_actions"] = [
        {
            "priority": 1,
            "category": "tlh",
            "action": "Harvest $8,447.50 in long-term losses.",
            "rationale": "Positions are at a loss.",
            "consequence": "If you don't act: $1,909 in savings forfeited.",
            "estimated_benefit": "$1,588 in immediate tax savings",
            "urgency": "this_year",
            "confidence": "high",
        },
        {
            "priority": 2,
            "category": "roth_conversion",
            "action": "Convert $182,000 to Roth in 2028.",
            "rationale": "Low-income window.",
            "consequence": "If you don't act: $140,000 more in lifetime taxes.",
            "estimated_benefit": "$140,000+ in lifetime tax savings",
            "urgency": "immediate",
            "confidence": "high",
        },
        {
            "priority": 3,
            "category": "asset_location",
            "action": "Move BND to IRA.",
            "rationale": "Tax drag reduction.",
            "consequence": "If you don't act: $8,000+ in avoidable tax drag over 15 years.",
            "estimated_benefit": "$500 per year in reduced tax drag",
            "urgency": "multi_year",
            "confidence": "medium",
        },
    ]

    result = PlanSynthesizerOutput(**result_data)
    snapshot = _make_snapshot()
    trajectory = _make_trajectory()
    conversions = _make_conversions()

    processed = _post_process(result, snapshot, trajectory, conversions)

    actions_by_priority = {a.priority: a for a in processed.priority_actions}
    assert actions_by_priority[1].category == "roth_conversion", (
        "Roth conversion ($140,000) should be priority 1 after sorting by dollar impact"
    )
    assert actions_by_priority[2].category == "tlh", (
        "TLH ($1,588) should be priority 2 after sorting"
    )
    assert actions_by_priority[3].category == "asset_location", (
        "Asset location ($500) should be priority 3 after sorting"
    )


# ---------------------------------------------------------------------------
# Test 4: Illustrative note appears exactly once (banner only, not in every row)
# ---------------------------------------------------------------------------

def test_no_repeated_notes():
    """The illustrative disclaimer should appear in the amber banner only."""
    step4 = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    step4["conversion_table"] = {
        "rows": [
            {
                "year": 2028,
                "pre_conversion_income": 0.0,
                "convert_amount": 212000.0,
                "post_conversion_agi": 212000.0,
                "federal_tax": 30146.0,
                "state_tax": 10494.0,
                "total_tax": 40640.0,
                "effective_rate_pct": 19.2,
                "cumulative_converted": 212000.0,
                "irmaa_safe": True,
                "note": None,  # no per-row illustrative text
            },
            {
                "year": 2029,
                "pre_conversion_income": 0.0,
                "convert_amount": 212000.0,
                "post_conversion_agi": 212000.0,
                "federal_tax": 30146.0,
                "state_tax": 10494.0,
                "total_tax": 40640.0,
                "effective_rate_pct": 19.2,
                "cumulative_converted": 424000.0,
                "irmaa_safe": True,
                "note": None,
            },
        ],
        "total_converted": 424000.0,
        "total_tax_paid": 81280.0,
        "blended_effective_rate_pct": 19.2,
        "il_state_tax_note": "Illinois taxes Roth conversions at 4.95%.",
        "illustrative": True,
    }

    html = _render_template(step4)

    # Banner should appear exactly once
    banner_count = html.count("assumes $0 retirement income")
    assert banner_count == 1, (
        f"Illustrative disclaimer must appear exactly once (banner); found {banner_count}"
    )

    # The per-row notes column should show "—" for null notes (not the disclaimer text)
    # Count rows with substantive notes
    assert html.count("ILLUSTRATIVE PROJECTION") == 1, (
        "ILLUSTRATIVE PROJECTION banner must appear exactly once"
    )
