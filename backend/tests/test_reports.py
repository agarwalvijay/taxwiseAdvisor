"""
Tests for the PDF report generator.

Tests:
1. test_report_generates: generate from synthetic plan fixture → PDF bytes start with b"%PDF"
2. test_report_blocked: ReportGenerationError raised if plan_status != "complete"
3. test_all_sections_present: render HTML template, assert all 9 section ids present
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import uuid

# The synthetic plan fixture — complete step_outputs matching all 4 schemas
SYNTHETIC_STEP_OUTPUTS = {
    "step_1": {
        "current_bracket": 0.32,
        "current_agi": 271000.0,
        "retirement_bracket_estimate": 0.22,
        "rmd_bracket_estimate": 0.24,
        "irmaa_risk": {
            "flagged": True,
            "reason": "Projected RMDs of $76,642 combined with SS income will push MAGI above $212,000 IRMAA Tier 1.",
            "tier_at_risk": 1,
        },
        "conversion_window_years": [2028, 2029, 2030, 2031, 2032],
        "conversion_window_rationale": "Client retires at 62; years 2028-2032 show near-zero income.",
        "years_until_rmd": 19,
        "projected_first_rmd": 76642.0,
        "projected_pretax_at_rmd": 2031000.0,
        "urgency": "high",
        "ss_taxation_risk": True,
        "narrative": "Client is in the 32% bracket now but faces a significant tax cliff at RMD start age 73.",
        "confidence": 0.88,
        "data_gaps": [],
    },
    "step_2": {
        "conversion_plan": [
            {
                "year": 2028,
                "convert_amount": 182000.0,
                "estimated_federal_tax": 40040.0,
                "estimated_state_tax": 9009.0,
                "bracket_used": "fills 22% bracket, stays below IRMAA tier 1",
                "post_conversion_agi": 182000.0,
                "irmaa_safe": True,
                "irmaa_note": None,
                "aca_safe": True,
                "aca_note": None,
                "ss_taxation_impact": None,
                "net_benefit_note": "Converting $182k saves an estimated $28k in lifetime taxes vs. taking RMDs at higher rates.",
            },
            {
                "year": 2029,
                "convert_amount": 182000.0,
                "estimated_federal_tax": 40040.0,
                "estimated_state_tax": 9009.0,
                "bracket_used": "fills 22% bracket, stays below IRMAA tier 1",
                "post_conversion_agi": 182000.0,
                "irmaa_safe": True,
                "irmaa_note": None,
                "aca_safe": True,
                "aca_note": None,
                "ss_taxation_impact": None,
                "net_benefit_note": "Second consecutive year of conversions during the early retirement income gap.",
            },
        ],
        "total_converted": 364000.0,
        "estimated_total_tax_on_conversions": 98098.0,
        "liquidity_check_passed": True,
        "liquidity_note": None,
        "aca_cliff_risk_years": [],
        "irmaa_cliff_risk_years": [],
        "state_tax_note": "Illinois taxes Roth conversions as ordinary income at 4.95%, but future Roth withdrawals are Illinois state-tax-free — significantly improving the net benefit of converting now.",
        "narrative": "The two-year early retirement window provides an exceptional opportunity to convert $182,000 per year.",
        "confidence": 0.85,
        "data_gaps": [],
    },
    "step_3": {
        "tlh_section_complete": True,
        "tlh_unavailable_reason": None,
        "tlh_opportunities": [
            {
                "symbol": "INTC",
                "description": "Intel Corp",
                "unrealized_loss": -5320.0,
                "holding_period": "long_term",
                "action": "Sell all 200 shares",
                "suggested_replacement": "AMD (Advanced Micro Devices)",
                "wash_sale_risk": "none",
                "wash_sale_note": "AMD and INTC are not substantially identical securities.",
                "estimated_tax_benefit": 1265.16,
                "niit_benefit": 201.76,
            },
        ],
        "total_harvestable_losses": 9967.5,
        "estimated_total_tax_benefit": 2372.46,
        "asset_location_moves": [
            {
                "asset_description": "BND (Vanguard Total Bond Market ETF)",
                "current_location": "Taxable Brokerage (Fidelity)",
                "recommended_location": "Traditional IRA or 401(k)",
                "rationale": "Bond funds generate ordinary income. Holding in a tax-deferred account shields this income from current taxation.",
                "priority": "high",
            },
        ],
        "narrative": "Two tax-loss harvesting opportunities identified in the taxable brokerage account.",
        "confidence": 0.87,
        "data_gaps": [],
    },
    "step_4": {
        "executive_summary": "Based on our analysis, your client faces a significant tax cliff at age 73 when Required Minimum Distributions begin. The 8-year early retirement window (ages 62-70) provides an exceptional opportunity to convert pre-tax retirement assets to Roth at historically low marginal rates, potentially saving $150,000+ in lifetime taxes.",
        "priority_actions": [
            {
                "priority": 1,
                "category": "roth_conversion",
                "action": "Convert $182,000 from Vanguard Traditional IRA and Fidelity 401(k) to Roth IRA annually in 2028 and 2029",
                "rationale": "These two years of near-zero income allow large conversions at the 22% rate, well below the projected 24-32% RMD bracket.",
                "estimated_benefit": "Estimated $84,000+ in lifetime federal and state tax savings",
                "urgency": "immediate",
                "confidence": "high",
            },
            {
                "priority": 2,
                "category": "tlh",
                "action": "Harvest $5,320 loss in Intel (INTC) — sell 200 shares and immediately buy AMD as replacement",
                "rationale": "Locking in this paper loss offsets capital gains and reduces NIIT exposure. AMD tracks similarly to Intel without triggering wash-sale rules.",
                "estimated_benefit": "Estimated $1,467 in immediate tax savings (federal + NIIT)",
                "urgency": "this_year",
                "confidence": "high",
            },
        ],
        "key_assumptions": [
            "Federal tax brackets remain at 2026 levels (no legislative changes)",
            "Investment returns average 6% nominal annually",
            "Social Security benefit of $3,520/month starting at age 70 as projected",
            "Illinois state income tax rate remains at 4.95%",
        ],
        "data_gaps_affecting_plan": [],
        "plan_confidence": 0.86,
        "disclaimer": "This analysis was prepared by [Advisor Name] using TaxWise Advisor software as a planning tool. It is intended to support informed discussion between you and your financial advisor and does not constitute independent financial, tax, or legal advice. Tax laws change frequently and individual circumstances vary. The projections and recommendations in this report are based on information provided as of [Report Date] and involve assumptions about future income, tax rates, and investment returns that may not materialize. Before implementing any strategy described in this report, please consult with a qualified tax professional. Your advisor is responsible for the recommendations made to you.",
        "narrative": "This plan addresses the most significant tax optimization opportunity: converting pre-tax retirement assets to Roth during the low-income retirement window.",
    },
}

SYNTHETIC_SNAPSHOT_DATA = {
    "personal": {"age": 54, "filing_status": "married_filing_jointly", "state": "IL", "retirement_target_age": 62},
    "income": {"current_year_agi": 271000.0},
    "accounts": {
        "traditional_401k": [{"institution": "Fidelity", "balance": 842000.0}],
        "traditional_ira": [{"institution": "Vanguard", "balance": 215000.0}],
        "roth_ira": [{"institution": "Vanguard", "balance": 88000.0}],
        "taxable_brokerage": [{"institution": "Fidelity", "total_value": 487250.0}],
    },
    "rmd_profile": {"years_until_rmd": 19},
    "tax_profile": {"current_marginal_bracket": 0.32},
}


@pytest.mark.asyncio
async def test_report_generates():
    from backend.reports.generator import ReportGenerator
    import uuid as _uuid

    plan_id = _uuid.uuid4()
    snapshot_id = _uuid.uuid4()

    mock_plan = MagicMock()
    mock_plan.id = plan_id
    mock_plan.plan_status = "complete"
    mock_plan.step_outputs = SYNTHETIC_STEP_OUTPUTS
    mock_plan.snapshot_id = snapshot_id

    mock_snapshot = MagicMock()
    mock_snapshot.snapshot_data = SYNTHETIC_SNAPSHOT_DATA

    # Mock two sequential db.execute() calls
    mock_result_plan = MagicMock()
    mock_result_plan.scalar_one_or_none.return_value = mock_plan
    mock_result_snapshot = MagicMock()
    mock_result_snapshot.scalar_one_or_none.return_value = mock_snapshot

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[mock_result_plan, mock_result_snapshot])

    gen = ReportGenerator()
    pdf_bytes = await gen.generate(
        plan_id=plan_id,
        advisor_name="Jane Advisor",
        client_name="John Client",
        db=mock_db,
    )
    assert pdf_bytes[:4] == b"%PDF", "Result must be a valid PDF"
    assert len(pdf_bytes) > 1000, "PDF should be non-trivial"


@pytest.mark.asyncio
async def test_report_blocked():
    from backend.reports.generator import ReportGenerator, ReportGenerationError
    import uuid as _uuid

    plan_id = _uuid.uuid4()

    mock_plan = MagicMock()
    mock_plan.id = plan_id
    mock_plan.plan_status = "generating"
    mock_plan.step_outputs = {}
    mock_plan.snapshot_id = _uuid.uuid4()

    mock_result_plan = MagicMock()
    mock_result_plan.scalar_one_or_none.return_value = mock_plan

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result_plan)

    gen = ReportGenerator()
    with pytest.raises(ReportGenerationError) as exc_info:
        await gen.generate(
            plan_id=plan_id,
            advisor_name="Jane Advisor",
            client_name="John Client",
            db=mock_db,
        )
    assert "not complete" in str(exc_info.value).lower()


def test_all_sections_present():
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path
    templates_dir = Path("backend/reports/templates")
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
    template = env.get_template("report.html")
    html = template.render(
        advisor_name="Jane Advisor",
        client_name="John Client",
        analysis_date="March 17, 2026",
        snapshot=SYNTHETIC_SNAPSHOT_DATA,
        step1=SYNTHETIC_STEP_OUTPUTS["step_1"],
        step2=SYNTHETIC_STEP_OUTPUTS["step_2"],
        step3=SYNTHETIC_STEP_OUTPUTS["step_3"],
        step4=SYNTHETIC_STEP_OUTPUTS["step_4"],
    )
    for section_id in ["cover", "executive-summary", "financial-snapshot", "tax-trajectory",
                       "roth-conversion", "tax-loss-harvesting", "action-plan",
                       "key-assumptions", "disclaimer"]:
        assert f'id="{section_id}"' in html, f"Section '{section_id}' not found in template"
