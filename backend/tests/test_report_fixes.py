"""
Tests for the 5 report fixes:
1. test_disclaimer_substitution: [Advisor Name] and [Report Date] replaced in rendered HTML
2. test_bracket_percentage_format: Marginal bracket shows "24.0%" not "0%"
3. test_comparison_table_renders: Do-Nothing vs This Plan section renders with data
4. test_illustrative_conversion_table: Illustrative amber warning renders when ct.illustrative=True
5. test_consequence_in_actions: "If you don't act" consequence callout renders per action
"""
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path("backend/reports/templates")


def _render(step4: dict, snapshot: dict | None = None, step1: dict | None = None) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)

    def _filter_currency(value) -> str:
        try:
            return f"${float(value):,.0f}"
        except (TypeError, ValueError):
            return "—"

    def _filter_percentage(value, decimals: int = 1) -> str:
        try:
            return f"{float(value) * 100:.{decimals}f}%"
        except (TypeError, ValueError):
            return "—"

    def _filter_irmaa_safe(value: bool) -> str:
        return "✓ Safe" if value else "✗ Risk"

    def _filter_urgency_class(value: str) -> str:
        mapping = {
            "immediate": "urgency-immediate",
            "this_year": "urgency-this_year",
            "multi_year": "urgency-multi_year",
            "high": "urgency-immediate",
            "medium": "urgency-this_year",
            "low": "urgency-multi_year",
        }
        return mapping.get(str(value).lower(), "urgency-multi_year")

    env.filters["currency"] = _filter_currency
    env.filters["percentage"] = _filter_percentage
    env.filters["irmaa_safe"] = _filter_irmaa_safe
    env.filters["urgency_class"] = _filter_urgency_class

    template = env.get_template("report.html")
    return template.render(
        advisor_name="Jane Advisor",
        client_name="John Client",
        analysis_date="March 18, 2026",
        snapshot=snapshot or _minimal_snapshot(),
        step1=step1 or {},
        step2={},
        step3={"tlh_section_complete": False, "tlh_unavailable_reason": "No taxable brokerage data."},
        step4=step4,
    )


def _minimal_snapshot() -> dict:
    return {
        "personal": {"age": 54, "filing_status": "married_filing_jointly", "state": "IL", "retirement_target_age": 62},
        "income": {"current_year_agi": 271000.0},
        "accounts": {
            "traditional_ira": [{"institution": "Vanguard", "balance": 1057000.0}],
            "roth_ira": [{"institution": "Vanguard", "balance": 88000.0}],
            "taxable_brokerage": [{"institution": "Fidelity", "total_value": 487250.0}],
        },
        "tax_profile": {"current_marginal_bracket": 0.24},
        "rmd_profile": {"years_until_rmd": 19},
    }


def _base_step4(**overrides) -> dict:
    base = {
        "executive_summary": "Test summary.",
        "priority_actions": [
            {
                "priority": 1,
                "category": "roth_conversion",
                "action": "Convert $182,000 this year.",
                "rationale": "Low income year.",
                "consequence": "If you don't act: pay $140,000 more in lifetime taxes.",
                "estimated_benefit": "$140,000+ savings",
                "urgency": "immediate",
                "confidence": "high",
            }
        ],
        "conversion_table": {
            "rows": [],
            "total_converted": 0.0,
            "total_tax_paid": 0.0,
            "blended_effective_rate_pct": 0.0,
            "il_state_tax_note": "",
            "illustrative": False,
        },
        "tlh_summary": {"available": False, "unavailable_reason": "No data."},
        "do_nothing_comparison": {
            "projected_rmd_at_73": 76642.0,
            "rmd_bracket": 0.24,
            "irmaa_triggered": True,
            "estimated_lifetime_tax_savings": 140000.0,
            "narrative": "Without action, client faces $140k in extra taxes.",
            "pretax_at_73_without_plan": 2031000.0,
            "pretax_at_73_with_plan": 1485000.0,
            "first_rmd_without_plan": 76642.0,
            "first_rmd_with_plan": 56038.0,
            "annual_rmd_tax_without_plan": 18394.0,
            "annual_rmd_tax_with_plan": 13449.0,
            "roth_at_73_without_plan": 88000.0,
            "roth_at_73_with_plan": 634000.0,
        },
        "key_assumptions": ["6% nominal growth applied."],
        "data_gaps": [],
        "plan_confidence": 0.88,
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
        "narrative": "Act now.",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1: Disclaimer substitution
# ---------------------------------------------------------------------------

def test_disclaimer_substitution():
    html = _render(_base_step4())
    assert "[Advisor Name]" not in html, "Placeholder [Advisor Name] must be replaced"
    assert "[Report Date]" not in html, "Placeholder [Report Date] must be replaced"
    assert "Jane Advisor" in html, "advisor_name must appear in disclaimer"
    assert "March 18, 2026" in html, "analysis_date must appear in disclaimer"


# ---------------------------------------------------------------------------
# Test 2: Marginal bracket percentage format
# ---------------------------------------------------------------------------

def test_bracket_percentage_format():
    snapshot = _minimal_snapshot()
    snapshot["tax_profile"]["current_marginal_bracket"] = 0.24
    html = _render(_base_step4(), snapshot=snapshot)
    # Should render "24.0%" not "0%" or "24%"
    assert "24.0%" in html, "Marginal bracket should render as 24.0% via percentage filter"
    # Make sure we don't see the zero-bug
    # (The old code `round(0) | int` on a None returns 0%)
    # Verify with a non-zero value that it's the right field
    snapshot2 = _minimal_snapshot()
    snapshot2["tax_profile"]["current_marginal_bracket"] = 0.32
    html2 = _render(_base_step4(), snapshot=snapshot2)
    assert "32.0%" in html2, "Marginal bracket should render as 32.0%"


# ---------------------------------------------------------------------------
# Test 3: Do-Nothing vs This Plan comparison table renders
# ---------------------------------------------------------------------------

def test_comparison_table_renders():
    html = _render(_base_step4())
    assert 'id="do-nothing-comparison"' in html, "do-nothing-comparison section must be present"
    assert "Do-Nothing vs. This Plan" in html, "Section heading must be present"
    assert "2,031,000" in html, "pretax_at_73_without_plan must render"
    assert "1,485,000" in html, "pretax_at_73_with_plan must render"
    assert "76,642" in html, "first_rmd_without_plan must render"
    assert "Estimated Lifetime Tax Savings" in html, "lifetime savings row must be present"
    assert "140,000" in html, "estimated_lifetime_tax_savings must render"


# ---------------------------------------------------------------------------
# Test 4: Illustrative conversion table amber warning
# ---------------------------------------------------------------------------

def test_illustrative_conversion_table():
    step4 = _base_step4()
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
                "note": "Illustrative — assumes $0 other income.",
            }
        ],
        "total_converted": 212000.0,
        "total_tax_paid": 40640.0,
        "blended_effective_rate_pct": 19.2,
        "il_state_tax_note": "Illinois taxes Roth conversions at 4.95%.",
        "illustrative": True,
    }
    html = _render(step4)
    assert "ILLUSTRATIVE PROJECTION" in html, "Illustrative warning banner must render"
    assert "Add retirement year projections in Step 3" in html, "CTA to add projections must render"
    # Row should be amber-tinted (FFFBEB)
    assert "FFFBEB" in html, "Illustrative rows must use amber background"


# ---------------------------------------------------------------------------
# Test 5: Consequence field renders in action plan
# ---------------------------------------------------------------------------

def test_consequence_in_actions():
    step4 = _base_step4()
    step4["priority_actions"] = [
        {
            "priority": 1,
            "category": "roth_conversion",
            "action": "Convert $182,000 this year.",
            "rationale": "Low income year — best window.",
            "consequence": "If you don't act: pay an estimated $140,000 more in lifetime taxes.",
            "estimated_benefit": "$140,000+ savings",
            "urgency": "immediate",
            "confidence": "high",
        },
        {
            "priority": 2,
            "category": "tlh",
            "action": "Harvest INTC losses.",
            "rationale": "Position is at a loss.",
            "consequence": "If you don't act: $1,909 in combined LTCG and NIIT savings will be forfeited.",
            "estimated_benefit": "$1,588 + $321 NIIT",
            "urgency": "this_year",
            "confidence": "high",
        },
    ]
    html = _render(step4)
    # Jinja2 autoescape encodes apostrophes — check for escaped or unescaped form
    assert ("If you don&#39;t act: pay an estimated $140,000" in html
            or "If you don't act: pay an estimated $140,000" in html), \
        "Consequence for action 1 must render"
    assert ("If you don&#39;t act: $1,909" in html
            or "If you don't act: $1,909" in html), \
        "Consequence for action 2 must render"
    # Consequence should be in the amber callout style
    assert "FFF3CD" in html, "Consequence must use amber callout background"
