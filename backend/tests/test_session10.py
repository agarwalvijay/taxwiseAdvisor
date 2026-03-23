"""
Session 10 tests — 4 deterministic fixes:

test_comparison_table_not_zero:
    with_plan_* fields differ from do_nothing_* fields and are non-zero.

test_comparison_table_math_accuracy:
    with_plan_pretax = max(0, do_nothing_pretax - total_converted * 1.5) ± tolerance.

test_conversion_window_full:
    _ensure_window_complete adds placeholder rows for missing years;
    all 8 window years are present after the call.

test_no_data_collection_in_actions:
    Data-gathering strings are removed from priority_actions by _post_process.

test_balance_projection_deterministic:
    _project_balance returns identical results on repeated calls (pure function).

test_rmd_calculation_correct:
    do_nothing_first_rmd == do_nothing_pretax_at_rmd / 26.5 ± $1.
"""
import copy

import pytest

from backend.models.plan import (
    ConversionOptimizerOutput,
    PlanSynthesizerOutput,
    TaxTrajectoryOutput,
    TLHAdvisorOutput,
)
from backend.models.snapshot import ClientFinancialSnapshotSchema
from backend.reasoning.conversion_optimizer import _ensure_window_complete
from backend.reasoning.plan_synthesizer import (
    _calculate_with_plan_comparison,
    _post_process,
    _project_balance,
    _RMD_FACTOR_73,
)

# ---------------------------------------------------------------------------
# Fixtures — age=56, retirement_age=65, pretax=$1,346,700, IL MFJ
# ---------------------------------------------------------------------------

_SNAPSHOT_56 = {
    "client_id": "00000000-0000-0000-0000-000000000002",
    "snapshot_date": "2026-03-18",
    "personal": {
        "age": 56,
        "filing_status": "married_filing_jointly",
        "state": "IL",
        "retirement_target_age": 65,
    },
    "income": {
        "current_year_agi": 220000.0,
        "projections": [
            {"year": 2035, "estimated_income": 0.0, "notes": "retirement", "source": "advisor_input"},
            {"year": 2036, "estimated_income": 0.0, "notes": "retirement", "source": "advisor_input"},
            {"year": 2037, "estimated_income": 0.0, "notes": "retirement", "source": "advisor_input"},
        ],
        "social_security": {"start_age": 70, "monthly_benefit_estimate": 3000.0},
    },
    "accounts": {
        "traditional_ira": [{"institution": "Vanguard", "balance": 1346700.0}],
        "roth_ira": [{"institution": "Vanguard", "balance": 100000.0}],
        "taxable_brokerage": [{"institution": "Fidelity", "total_value": 400000.0}],
    },
    "tax_profile": {"current_marginal_bracket": 0.24},
    "rmd_profile": {"years_until_rmd": 17},
}

# projected_pretax_at_rmd = _project_balance(1346700, 17) ≈ $3,625,913
_PRETAX_AT_RMD = _project_balance(1346700.0, 17)

_TRAJECTORY_56 = {
    "current_bracket": 0.24,
    "current_agi": 220000.0,
    "retirement_bracket_estimate": 0.22,
    "rmd_bracket_estimate": 0.24,
    "irmaa_risk": {
        "flagged": True,
        "reason": "RMDs + SS will exceed IRMAA threshold",
        "tier_at_risk": 1,
    },
    "conversion_window_years": list(range(2035, 2043)),  # 8 years
    "conversion_window_rationale": "Zero income in retirement through RMD start",
    "years_until_rmd": 17,
    "projected_first_rmd": round(_PRETAX_AT_RMD / _RMD_FACTOR_73, 2),
    "projected_pretax_at_rmd": _PRETAX_AT_RMD,
    "urgency": "high",
    "ss_taxation_risk": True,
    "narrative": "Significant conversion opportunity.",
    "confidence": 0.88,
    "data_gaps": [],
}

_CONVERSION_56 = {
    "conversion_plan": [
        {
            "year": 2035,
            "convert_amount": 182000.0,
            "estimated_federal_tax": 29868.0,
            "estimated_state_tax": 9009.0,
            "bracket_used": "fills 22%",
            "post_conversion_agi": 182000.0,
            "irmaa_safe": True,
            "aca_safe": True,
            "net_benefit_note": "Strong conversion year.",
        },
        {
            "year": 2036,
            "convert_amount": 182000.0,
            "estimated_federal_tax": 29868.0,
            "estimated_state_tax": 9009.0,
            "bracket_used": "fills 22%",
            "post_conversion_agi": 182000.0,
            "irmaa_safe": True,
            "aca_safe": True,
            "net_benefit_note": "Second year.",
        },
    ],
    "total_converted": 364000.0,
    "estimated_total_tax_on_conversions": 77754.0,
    "liquidity_check_passed": True,
    "state_tax_note": "Illinois 4.95%.",
    "narrative": "Strong two-year conversion opportunity.",
    "confidence": 0.87,
    "data_gaps": [],
}

_TLH_56 = {
    "tlh_section_complete": False,
    "tlh_unavailable_reason": "No taxable holdings data provided.",
    "tlh_opportunities": [],
    "total_harvestable_losses": 0.0,
    "estimated_total_tax_benefit": 0.0,
    "asset_location_moves": [],
    "narrative": "No TLH available.",
    "confidence": 0.5,
    "data_gaps": [],
}

_SYNTHESIS_56 = {
    "executive_summary": "Client has a strong Roth conversion opportunity in early retirement.",
    "client_snapshot_summary": {
        "age": 56,
        "filing_status": "married_filing_jointly",
        "state": "IL",
        "retirement_target_age": 65,
        "years_to_retirement": 9,
        "current_agi": 220000.0,
        "total_pretax_balance": 1346700.0,
        "total_roth_balance": 100000.0,
        "total_taxable_balance": 400000.0,
        "total_hsa_balance": 0.0,
        "cash_savings": 0.0,
        "years_until_rmd": 17,
        "projected_first_rmd": round(_PRETAX_AT_RMD / _RMD_FACTOR_73, 2),
    },
    "do_nothing_comparison": {
        "projected_rmd_at_73": round(_PRETAX_AT_RMD / _RMD_FACTOR_73, 2),
        "rmd_bracket": 0.24,
        "irmaa_triggered": True,
        "estimated_lifetime_tax_savings": 120000.0,
        "narrative": "Without conversions, client faces large RMDs at high brackets.",
    },
    "priority_actions": [
        {
            "priority": 1,
            "category": "roth_conversion",
            "action": "Convert $182,000 from Vanguard Traditional IRA to Roth IRA in 2035.",
            "rationale": "Zero income year — best conversion rate.",
            "consequence": "If you don't act: pay an estimated $120,000 more in lifetime taxes.",
            "estimated_benefit": "$120,000+ in lifetime tax savings",
            "urgency": "immediate",
            "confidence": "high",
        },
    ],
    "conversion_table": {
        "rows": [
            {
                "year": 2035,
                "pre_conversion_income": 0.0,
                "convert_amount": 182000.0,
                "post_conversion_agi": 182000.0,
                "federal_tax": 29868.0,
                "state_tax": 9009.0,
                "total_tax": 38877.0,
                "effective_rate_pct": 16.4,
                "cumulative_converted": 182000.0,
                "irmaa_safe": True,
                "note": None,
            },
        ],
        "total_converted": 182000.0,
        "total_tax_paid": 38877.0,
        "blended_effective_rate_pct": 16.4,
        "il_state_tax_note": "Illinois taxes Roth conversions at 4.95%.",
        "illustrative": False,
    },
    "tlh_summary": {"available": False, "unavailable_reason": "No data."},
    "key_assumptions": ["6% nominal growth."],
    "data_gaps": [],
    "plan_confidence": 0.87,
    "urgency": "high",
    "disclaimer": (
        "This analysis was prepared by [Advisor Name] using TaxWise Advisor software as a planning tool. "
        "It is intended to support informed discussion between you and your financial advisor and does not "
        "constitute independent financial, tax, or legal advice. Tax laws change frequently and individual "
        "circumstances vary. The projections and recommendations in this report are based on information "
        "provided as of [Report Date] and involve assumptions about future income, tax rates, and investment "
        "returns that may not materialize. Before implementing any strategy described in this report, please "
        "consult with a qualified tax professional. Your advisor is responsible for the recommendations made to you."
    ),
    "narrative": "Strong conversion opportunity in retirement window.",
}


def _make_snapshot() -> ClientFinancialSnapshotSchema:
    return ClientFinancialSnapshotSchema(**_SNAPSHOT_56)


def _make_trajectory() -> TaxTrajectoryOutput:
    return TaxTrajectoryOutput(**_TRAJECTORY_56)


def _make_conversions() -> ConversionOptimizerOutput:
    return ConversionOptimizerOutput(**_CONVERSION_56)


def _make_tlh() -> TLHAdvisorOutput:
    return TLHAdvisorOutput(**_TLH_56)


def _make_result(overrides: dict | None = None) -> PlanSynthesizerOutput:
    data = copy.deepcopy(_SYNTHESIS_56)
    if overrides:
        data.update(overrides)
    return PlanSynthesizerOutput(**data)


# ---------------------------------------------------------------------------
# Test 1: Comparison table with_plan fields are non-zero and differ from do_nothing
# ---------------------------------------------------------------------------

def test_comparison_table_not_zero():
    result = _make_result()
    snapshot = _make_snapshot()
    trajectory = _make_trajectory()
    conversions = _make_conversions()

    result = _post_process(result, snapshot, trajectory, conversions)
    result = _calculate_with_plan_comparison(result, snapshot, trajectory, conversions)
    dnc = result.do_nothing_comparison

    # with_plan pre-tax must be less than do_nothing (conversions reduce it)
    assert dnc.pretax_at_73_with_plan < dnc.pretax_at_73_without_plan, (
        f"pretax_at_73_with_plan ({dnc.pretax_at_73_with_plan}) should be < "
        f"pretax_at_73_without_plan ({dnc.pretax_at_73_without_plan})"
    )

    # with_plan RMD must be smaller
    assert dnc.first_rmd_with_plan < dnc.first_rmd_without_plan

    # Roth balance is higher with the plan
    assert dnc.roth_at_73_with_plan > dnc.roth_at_73_without_plan

    # Annual RMD tax is lower with the plan
    assert dnc.annual_rmd_tax_with_plan < dnc.annual_rmd_tax_without_plan

    # All with_plan values must be positive
    assert dnc.pretax_at_73_with_plan > 0
    assert dnc.first_rmd_with_plan > 0
    assert dnc.roth_at_73_with_plan > 0

    # Canonical fields must also be populated
    assert dnc.do_nothing_pretax_at_rmd > 0
    assert dnc.with_plan_pretax_at_rmd > 0
    assert dnc.with_plan_lifetime_savings != ""
    assert "–" in dnc.with_plan_lifetime_savings or "\u2013" in dnc.with_plan_lifetime_savings


# ---------------------------------------------------------------------------
# Test 2: Comparison table math accuracy
# ---------------------------------------------------------------------------

def test_comparison_table_math_accuracy():
    result = _make_result()
    snapshot = _make_snapshot()
    trajectory = _make_trajectory()
    conversions = _make_conversions()

    result = _post_process(result, snapshot, trajectory, conversions)
    result = _calculate_with_plan_comparison(result, snapshot, trajectory, conversions)
    dnc = result.do_nothing_comparison

    total_converted = result.conversion_table.total_converted  # 182000 after post_process
    do_nothing_pretax = _PRETAX_AT_RMD

    # do_nothing_pretax must equal trajectory.projected_pretax_at_rmd
    assert abs(dnc.pretax_at_73_without_plan - do_nothing_pretax) < 1.0, (
        f"do_nothing pretax ({dnc.pretax_at_73_without_plan}) should equal "
        f"trajectory.projected_pretax_at_rmd ({do_nothing_pretax})"
    )

    # with_plan_pretax = max(0, do_nothing_pretax - total_converted * 1.5) ± $1
    expected_with_plan = max(0.0, do_nothing_pretax - total_converted * 1.5)
    assert abs(dnc.pretax_at_73_with_plan - expected_with_plan) < 1.0, (
        f"with_plan pretax ({dnc.pretax_at_73_with_plan}) should equal "
        f"max(0, {do_nothing_pretax:.0f} - {total_converted:.0f} * 1.5) = {expected_with_plan:.0f}"
    )

    # RMD = pretax / 26.5 ± $1
    assert abs(dnc.first_rmd_without_plan - do_nothing_pretax / _RMD_FACTOR_73) < 1.0
    assert abs(dnc.first_rmd_with_plan - expected_with_plan / _RMD_FACTOR_73) < 1.0


# ---------------------------------------------------------------------------
# Test 3: Conversion window completeness (all 8 years covered)
# ---------------------------------------------------------------------------

def test_conversion_window_full():
    """_ensure_window_complete adds $0 rows for any missing window years."""
    window_years = list(range(2035, 2043))  # 8 years: 2035-2042

    # Simulate Claude returning only 4 of the 8 years
    partial_plan = ConversionOptimizerOutput(**copy.deepcopy(_CONVERSION_56))
    # Only 2035 and 2036 are in _CONVERSION_56
    assert len(partial_plan.conversion_plan) == 2

    result = _ensure_window_complete(partial_plan, window_years)

    covered_years = {e.year for e in result.conversion_plan}
    for yr in window_years:
        assert yr in covered_years, f"Year {yr} is missing from conversion_plan after window completion"

    assert len(result.conversion_plan) == 8, (
        f"Expected 8 entries (all window years), got {len(result.conversion_plan)}"
    )

    # All added placeholder rows should have convert_amount=0
    for entry in result.conversion_plan:
        if entry.year not in {2035, 2036}:
            assert entry.convert_amount == 0.0, (
                f"Placeholder row for year {entry.year} should have convert_amount=0"
            )

    # Plan should be sorted by year
    years_in_order = [e.year for e in result.conversion_plan]
    assert years_in_order == sorted(years_in_order), "conversion_plan must be sorted by year"


# ---------------------------------------------------------------------------
# Test 4: No data-collection actions in priority_actions after _post_process
# ---------------------------------------------------------------------------

def test_no_data_collection_in_actions():
    """Data-gathering strings are removed from priority_actions by _post_process."""
    data_collection_actions = [
        {
            "priority": 1,
            "category": "other",
            "action": "Gather complete income projections for years 2035-2042 from your employer.",
            "rationale": "We need this data to optimize conversions.",
            "consequence": "If you don't act: incomplete planning.",
            "estimated_benefit": "$0",
            "urgency": "this_year",
            "confidence": "low",
        },
        {
            "priority": 2,
            "category": "roth_conversion",
            "action": "Convert $182,000 from Vanguard Traditional IRA to Roth IRA in 2035.",
            "rationale": "Zero income year.",
            "consequence": "If you don't act: pay $120,000 more in lifetime taxes.",
            "estimated_benefit": "$120,000+ in lifetime tax savings",
            "urgency": "immediate",
            "confidence": "high",
        },
        {
            "priority": 3,
            "category": "other",
            "action": "Contact your tax advisor to verify deductions.",
            "rationale": "Need to confirm.",
            "consequence": "If you don't act: unknown.",
            "estimated_benefit": "$0",
            "urgency": "this_year",
            "confidence": "low",
        },
    ]

    result = _make_result({"priority_actions": data_collection_actions})
    snapshot = _make_snapshot()
    trajectory = _make_trajectory()
    conversions = _make_conversions()

    processed = _post_process(result, snapshot, trajectory, conversions)

    # Only the Roth conversion should remain
    assert len(processed.priority_actions) == 1, (
        f"Expected 1 action after filtering, got {len(processed.priority_actions)}: "
        f"{[a.action for a in processed.priority_actions]}"
    )
    assert processed.priority_actions[0].category == "roth_conversion"

    # No data-collection keywords should appear in remaining actions
    from backend.reasoning.plan_synthesizer import DATA_COLLECTION_KEYWORDS, _is_data_collection_action
    for action in processed.priority_actions:
        assert not _is_data_collection_action(action.action), (
            f"Data-collection action survived filter: {action.action}"
        )


# ---------------------------------------------------------------------------
# Test 5: Balance projection is deterministic (pure function, no randomness)
# ---------------------------------------------------------------------------

def test_balance_projection_deterministic():
    """_project_balance returns identical results on repeated calls."""
    balance = 1346700.0
    years = 17
    rate = 0.06

    result_1 = _project_balance(balance, years, rate)
    result_2 = _project_balance(balance, years, rate)
    result_3 = _project_balance(balance, years, rate)

    assert result_1 == result_2 == result_3, (
        "Expected deterministic output — results differ across calls"
    )

    # Sanity: compounded balance must be substantially larger than input
    assert result_1 > balance * 1.5, (
        f"17 years at 6% should more than double the balance; got {result_1} from {balance}"
    )

    # Verify the formula: balance * (1.06 ** years)
    expected = round(balance * (1.06 ** years), 2)
    assert result_1 == expected, f"Expected {expected}, got {result_1}"


# ---------------------------------------------------------------------------
# Test 6: RMD calculation accuracy (balance / 26.5)
# ---------------------------------------------------------------------------

def test_rmd_calculation_correct():
    """do_nothing_first_rmd == do_nothing_pretax_at_rmd / 26.5 ± $1."""
    result = _make_result()
    snapshot = _make_snapshot()
    trajectory = _make_trajectory()
    conversions = _make_conversions()

    result = _post_process(result, snapshot, trajectory, conversions)
    result = _calculate_with_plan_comparison(result, snapshot, trajectory, conversions)
    dnc = result.do_nothing_comparison

    expected_rmd = dnc.do_nothing_pretax_at_rmd / _RMD_FACTOR_73

    assert abs(dnc.do_nothing_first_rmd - expected_rmd) < 1.0, (
        f"do_nothing_first_rmd ({dnc.do_nothing_first_rmd:.2f}) should equal "
        f"pretax_at_rmd / 26.5 = {expected_rmd:.2f}"
    )

    # with_plan RMD should similarly satisfy balance / 26.5
    expected_wp_rmd = dnc.with_plan_pretax_at_rmd / _RMD_FACTOR_73
    assert abs(dnc.with_plan_first_rmd - expected_wp_rmd) < 1.0, (
        f"with_plan_first_rmd ({dnc.with_plan_first_rmd:.2f}) should equal "
        f"with_plan_pretax / 26.5 = {expected_wp_rmd:.2f}"
    )

    # RMD factor sanity: 26.5 is the IRS Uniform Lifetime Table factor at age 73
    assert _RMD_FACTOR_73 == 26.5
