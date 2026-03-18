"""
Tests for Session 4 reasoning engine.
All Anthropic API calls are mocked — no real API calls are made.
"""
import json
import pytest
import copy
from unittest.mock import AsyncMock, MagicMock, patch

from backend.models.snapshot import ClientFinancialSnapshotSchema
from backend.reasoning.tax_trajectory import TaxTrajectoryAnalyzer, ReasoningStepError
from backend.reasoning.conversion_optimizer import ConversionOptimizer
from backend.reasoning.tlh_advisor import TLHAdvisor
from backend.reasoning.plan_synthesizer import PlanSynthesizer
from backend.models.plan import (
    TaxTrajectoryOutput,
    ConversionOptimizerOutput,
    TLHAdvisorOutput,
    PlanSynthesizerOutput,
)

# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------

SYNTHETIC_SNAPSHOT = {
    "client_id": "00000000-0000-0000-0000-000000000001",
    "snapshot_date": "2026-03-17",
    "personal": {
        "age": 54,
        "spouse_age": 51,
        "filing_status": "married_filing_jointly",
        "state": "IL",
        "retirement_target_age": 62,
        "spouse_retirement_target_age": 60,
    },
    "income": {
        "current_year_agi": 271000.0,
        "projections": [
            {"year": 2026, "estimated_income": 295000.0, "notes": "peak earning year", "source": "advisor_input"},
            {"year": 2027, "estimated_income": 271000.0, "notes": "normal year", "source": "advisor_input"},
            {"year": 2028, "estimated_income": 0.0, "notes": "early retirement - no income", "source": "advisor_input"},
            {"year": 2029, "estimated_income": 0.0, "notes": "early retirement - no income", "source": "advisor_input"},
            {"year": 2030, "estimated_income": 0.0, "notes": "early retirement - no income", "source": "advisor_input"},
            {"year": 2031, "estimated_income": 0.0, "notes": "early retirement - no income", "source": "advisor_input"},
            {"year": 2032, "estimated_income": 15000.0, "notes": "part-time consulting", "source": "advisor_input"},
        ],
        "social_security": {"start_age": 70, "monthly_benefit_estimate": 3520.0},
    },
    "accounts": {
        "taxable_brokerage": [
            {
                "institution": "Fidelity",
                "total_value": 487250.0,
                "cash_balance": 12400.0,
                "holdings": [
                    {
                        "symbol": "VTI",
                        "description": "Vanguard Total Stock Mkt ETF",
                        "shares": 142.5,
                        "price_per_share": 245.10,
                        "market_value": 34926.75,
                        "cost_basis": 28400.0,
                        "unrealized_gain_loss": 6526.75,
                        "holding_period": "long_term",
                    },
                    {
                        "symbol": "AMZN",
                        "description": "Amazon.com Inc",
                        "shares": 45.0,
                        "price_per_share": 180.50,
                        "market_value": 8122.50,
                        "cost_basis": 11250.0,
                        "unrealized_gain_loss": -3127.50,
                        "holding_period": "long_term",
                    },
                    {
                        "symbol": "INTC",
                        "description": "Intel Corp",
                        "shares": 200.0,
                        "price_per_share": 22.40,
                        "market_value": 4480.0,
                        "cost_basis": 9800.0,
                        "unrealized_gain_loss": -5320.0,
                        "holding_period": "long_term",
                    },
                    {
                        "symbol": "BND",
                        "description": "Vanguard Total Bond Mkt ETF",
                        "shares": 350.0,
                        "price_per_share": 72.80,
                        "market_value": 25480.0,
                        "cost_basis": 27000.0,
                        "unrealized_gain_loss": -1520.0,
                        "holding_period": "long_term",
                    },
                    {
                        "symbol": "VUG",
                        "description": "Vanguard Growth ETF",
                        "shares": 180.0,
                        "price_per_share": 340.20,
                        "market_value": 61236.0,
                        "cost_basis": 42000.0,
                        "unrealized_gain_loss": 19236.0,
                        "holding_period": "long_term",
                    },
                ],
            }
        ],
        "traditional_401k": [{"institution": "Fidelity", "balance": 842000.0, "employer": "Acme Corp"}],
        "roth_401k": [],
        "traditional_ira": [{"institution": "Vanguard", "balance": 215000.0, "basis": 0.0}],
        "roth_ira": [{"institution": "Vanguard", "balance": 88000.0}],
        "hsa": [{"institution": "HealthEquity", "balance": 24000.0}],
        "cash_savings": 65000.0,
    },
    "tax_profile": {
        "current_marginal_bracket": 0.32,
        "current_agi": 271000.0,
        "ltcg_rate": 0.15,
        "state_income_tax_rate": 0.0495,
        "irmaa_exposure": False,
        "irmaa_tier1_threshold_mfj": 212000.0,
        "irmaa_buffer": 59000.0,
        "niit_exposure": True,
        "niit_threshold_mfj": 250000.0,
        "aca_relevant": False,
    },
    "rmd_profile": {
        "rmd_start_age": 73,
        "years_until_rmd": 19,
        "projected_pretax_balance_at_rmd": 2031000.0,
        "projected_first_rmd": 76642.0,
    },
    "data_provenance": {
        "source_documents": ["1040_2024.pdf", "fidelity_dec2025.pdf", "ira_vanguard_2024.pdf"],
        "advisor_overrides": [],
        "low_confidence_fields": [],
        "missing_soft_required": [],
        "snapshot_version": 1,
        "created_at": "2026-03-17T14:25:00Z",
    },
}

# ---------------------------------------------------------------------------
# Mock response builders
# ---------------------------------------------------------------------------

TRAJECTORY_RESPONSE = {
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
    "conversion_window_rationale": "Client retires at 62 in 2034. Years 2028–2032 show near-zero income, ideal for conversions.",
    "years_until_rmd": 19,
    "projected_first_rmd": 76642.0,
    "projected_pretax_at_rmd": 2031000.0,
    "urgency": "high",
    "ss_taxation_risk": True,
    "narrative": "Client is in the 32% bracket now but faces a significant tax cliff at RMD start age 73...",
    "confidence": 0.88,
    "data_gaps": [],
}

CONVERSION_RESPONSE = {
    "conversion_plan": [
        {
            "year": 2028,
            "convert_amount": 182000.0,
            "estimated_federal_tax": 29868.0,
            "estimated_state_tax": 9009.0,
            "bracket_used": "fills 22% bracket, stays below IRMAA Tier 1 ($212k)",
            "post_conversion_agi": 182000.0,
            "irmaa_safe": True,
            "irmaa_note": None,
            "aca_safe": True,
            "aca_note": None,
            "ss_taxation_impact": None,
            "net_benefit_note": "Converting at ~16.4% blended vs. future RMDs at 24%+.",
        },
        {
            "year": 2029,
            "convert_amount": 182000.0,
            "estimated_federal_tax": 29868.0,
            "estimated_state_tax": 9009.0,
            "bracket_used": "fills 22% bracket, stays below IRMAA Tier 1",
            "post_conversion_agi": 182000.0,
            "irmaa_safe": True,
            "irmaa_note": None,
            "aca_safe": True,
            "aca_note": None,
            "ss_taxation_impact": None,
            "net_benefit_note": "Continued conversion at favorable rates.",
        },
        {
            "year": 2030,
            "convert_amount": 182000.0,
            "estimated_federal_tax": 29868.0,
            "estimated_state_tax": 9009.0,
            "bracket_used": "fills 22% bracket, stays below IRMAA Tier 1",
            "post_conversion_agi": 182000.0,
            "irmaa_safe": True,
            "irmaa_note": None,
            "aca_safe": True,
            "aca_note": None,
            "ss_taxation_impact": None,
            "net_benefit_note": "Third year of systematic conversion program.",
        },
    ],
    "total_converted": 546000.0,
    "estimated_total_tax_on_conversions": 119622.0,
    "liquidity_check_passed": True,
    "liquidity_note": None,
    "aca_cliff_risk_years": [],
    "irmaa_cliff_risk_years": [],
    "state_tax_note": "Illinois taxes Roth conversions as ordinary income at 4.95%, but future Roth withdrawals are Illinois state-tax-free.",
    "narrative": "A 3-year conversion program converts $546,000 from pre-tax to Roth at favorable rates.",
    "confidence": 0.85,
    "data_gaps": [],
}

TLH_RESPONSE = {
    "tlh_section_complete": True,
    "tlh_unavailable_reason": None,
    "tlh_opportunities": [
        {
            "symbol": "AMZN",
            "description": "Amazon.com Inc",
            "unrealized_loss": -3127.50,
            "holding_period": "long_term",
            "action": "Sell 45 shares of AMZN to harvest $3,127.50 long-term loss.",
            "suggested_replacement": "XLY (Consumer Discretionary Select Sector SPDR ETF)",
            "wash_sale_risk": "none",
            "wash_sale_note": "No recent AMZN purchases identified. Wait 31 days before repurchasing.",
            "estimated_tax_benefit": 587.97,
            "niit_benefit": 118.84,
        },
        {
            "symbol": "INTC",
            "description": "Intel Corp",
            "unrealized_loss": -5320.0,
            "holding_period": "long_term",
            "action": "Sell 200 shares of INTC to harvest $5,320.00 long-term loss.",
            "suggested_replacement": "SOXX (iShares Semiconductor ETF)",
            "wash_sale_risk": "none",
            "wash_sale_note": "No recent INTC purchases identified. Wait 31 days before repurchasing.",
            "estimated_tax_benefit": 1000.16,
            "niit_benefit": 202.16,
        },
    ],
    "total_harvestable_losses": 8447.50,
    "estimated_total_tax_benefit": 1588.13,
    "asset_location_moves": [
        {
            "asset_description": "BND - Vanguard Total Bond Market ETF",
            "current_location": "Taxable brokerage (Fidelity)",
            "recommended_location": "Traditional IRA or 401(k)",
            "rationale": "Bond interest is taxed as ordinary income. Holding bonds in tax-deferred accounts defers this tax drag.",
            "priority": "medium",
        }
    ],
    "narrative": "Two TLH opportunities identified: AMZN and INTC with combined losses of $8,447.50.",
    "confidence": 0.92,
    "data_gaps": [],
}

SYNTHESIS_RESPONSE = {
    "executive_summary": (
        "This client has significant Roth conversion opportunity over the next 5 years. "
        "The combination of a multi-year conversion program, immediate tax-loss harvesting, "
        "and asset location optimization is projected to save substantial lifetime taxes."
    ),
    "priority_actions": [
        {
            "priority": 1,
            "category": "roth_conversion",
            "action": "Convert $182,000 from Vanguard Traditional IRA to Roth IRA in 2028, paying approximately $38,877 in combined federal and Illinois state taxes from taxable brokerage funds.",
            "rationale": "With zero income in 2028, the client can convert at a blended 16.4% rate instead of the 24%+ rate that will apply at RMD start. This saves roughly $14,000 in taxes on this single conversion.",
            "estimated_benefit": "$140,000+ in lifetime tax savings across the full conversion program",
            "urgency": "immediate",
            "confidence": "high",
        },
        {
            "priority": 2,
            "category": "tlh",
            "action": "Sell 45 shares of AMZN and 200 shares of INTC in Fidelity taxable brokerage to harvest $8,447.50 in long-term losses. Purchase XLY and SOXX as replacements within 2 days.",
            "rationale": "These positions are already at a loss. Harvesting now captures the tax benefit before conditions change, and replacement ETFs maintain similar market exposure.",
            "estimated_benefit": "$1,588 in immediate tax savings (15% LTCG + 3.8% NIIT on losses)",
            "urgency": "this_year",
            "confidence": "high",
        },
        {
            "priority": 3,
            "category": "asset_location",
            "action": "Gradually shift BND (Vanguard Total Bond ETF) from the taxable Fidelity account to the Traditional IRA as the taxable portfolio is rebalanced.",
            "rationale": "Bond interest is taxed as ordinary income. Moving bonds to tax-deferred accounts reduces annual tax drag.",
            "estimated_benefit": "Estimated $500–$800/year in reduced tax drag on bond income",
            "urgency": "multi_year",
            "confidence": "medium",
        },
    ],
    "key_assumptions": [
        "Tax rates remain at current law levels through the planning horizon",
        "6% nominal annual growth applied to pre-tax retirement accounts",
        "Social Security benefit estimates as provided by client ($3,520/month at age 70)",
        "Illinois state income tax rate of 4.95% applied to Roth conversions",
        "2026 MFJ standard deduction of $30,000 used for all conversion year calculations",
    ],
    "data_gaps_affecting_plan": [],
    "plan_confidence": 0.88,
    "disclaimer": (
        "This analysis was prepared by [Advisor Name] using TaxWise Advisor software as a planning tool. "
        "It is intended to support informed discussion between you and your financial advisor and does not "
        "constitute independent financial, tax, or legal advice. Tax laws change frequently and individual "
        "circumstances vary. The projections and recommendations in this report are based on information "
        "provided as of [Report Date] and involve assumptions about future income, tax rates, and investment "
        "returns that may not materialize. Before implementing any strategy described in this report, please "
        "consult with a qualified tax professional. Your advisor is responsible for the recommendations made to you."
    ),
    "narrative": "This plan focuses on the multi-year Roth conversion opportunity as the highest-impact strategy.",
}


def make_mock_response(json_data: dict):
    """Build a mock Anthropic response object."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(json_data))]
    mock_response.usage = MagicMock(input_tokens=500, output_tokens=300)
    return mock_response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot():
    return ClientFinancialSnapshotSchema(**SYNTHETIC_SNAPSHOT)


@pytest.fixture
def trajectory_output():
    return TaxTrajectoryOutput(**TRAJECTORY_RESPONSE)


@pytest.fixture
def conversion_output():
    return ConversionOptimizerOutput(**CONVERSION_RESPONSE)


@pytest.fixture
def tlh_output():
    return TLHAdvisorOutput(**TLH_RESPONSE)


# ---------------------------------------------------------------------------
# Test 1: Tax Trajectory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tax_trajectory(snapshot):
    mock_response = make_mock_response(TRAJECTORY_RESPONSE)

    with patch("backend.reasoning.tax_trajectory.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        analyzer = TaxTrajectoryAnalyzer()
        result = await analyzer.run(snapshot)

    assert result.current_bracket == 0.32
    assert 2028 in result.conversion_window_years
    assert result.irmaa_risk.flagged is True
    assert result.urgency == "high"
    assert result.confidence > 0.75


# ---------------------------------------------------------------------------
# Test 2: Conversion Optimizer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversion_optimizer(snapshot, trajectory_output):
    mock_response = make_mock_response(CONVERSION_RESPONSE)

    with patch("backend.reasoning.conversion_optimizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        optimizer = ConversionOptimizer()
        result = await optimizer.run(snapshot, trajectory_output)

    # Non-empty plan
    assert len(result.conversion_plan) > 0

    # No conversion amount exceeds total pretax balance (842k + 215k = 1,057,000)
    total_pretax = 842000.0 + 215000.0
    assert result.total_converted <= total_pretax

    # Years 2028, 2029, 2030 present
    years = [entry.year for entry in result.conversion_plan]
    assert 2028 in years
    assert 2029 in years
    assert 2030 in years

    # Liquidity passed
    assert result.liquidity_check_passed is True

    # Illinois mentioned in state tax note
    assert "illinois" in result.state_tax_note.lower() or "Illinois" in result.state_tax_note


# ---------------------------------------------------------------------------
# Test 3: TLH Advisor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tlh_advisor(snapshot):
    mock_response = make_mock_response(TLH_RESPONSE)

    with patch("backend.reasoning.tlh_advisor.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        advisor = TLHAdvisor()
        result = await advisor.run(snapshot)

    assert result.tlh_section_complete is True
    assert len(result.tlh_opportunities) > 0
    for opp in result.tlh_opportunities:
        assert opp.wash_sale_risk in ("none", "low", "high")
    assert len(result.asset_location_moves) > 0


# ---------------------------------------------------------------------------
# Test 4: Plan Synthesizer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_synthesizer(snapshot, trajectory_output, conversion_output, tlh_output):
    mock_response = make_mock_response(SYNTHESIS_RESPONSE)

    with patch("backend.reasoning.plan_synthesizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        synthesizer = PlanSynthesizer()
        result = await synthesizer.run(snapshot, trajectory_output, conversion_output, tlh_output)

    assert len(result.priority_actions) > 0
    # Sorted by priority (1, 2, 3, ...)
    priorities = [a.priority for a in result.priority_actions]
    assert priorities == sorted(priorities)
    assert result.executive_summary != ""
    assert result.disclaimer != ""
    assert result.plan_confidence > 0.70


# ---------------------------------------------------------------------------
# Test 5: TLH No Cost Basis — short-circuit without calling Claude
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tlh_no_cost_basis():
    """TLHAdvisor should return tlh_section_complete=False without calling Claude."""
    # Build snapshot with no cost_basis on any holding
    no_basis_snapshot = copy.deepcopy(SYNTHETIC_SNAPSHOT)
    for brokerage in no_basis_snapshot["accounts"]["taxable_brokerage"]:
        for holding in brokerage["holdings"]:
            holding["cost_basis"] = None

    snap = ClientFinancialSnapshotSchema(**no_basis_snapshot)

    with patch("backend.reasoning.tlh_advisor.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client

        advisor = TLHAdvisor()
        result = await advisor.run(snap)

        # Claude should NOT have been called
        mock_client.messages.create.assert_not_called()

    assert result.tlh_section_complete is False
    assert result.tlh_unavailable_reason is not None


# ---------------------------------------------------------------------------
# Test 6: Retry on bad JSON — succeeds on second attempt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_step_retry_on_bad_json(snapshot):
    """TaxTrajectoryAnalyzer should succeed on the second attempt after bad JSON."""
    good_response = make_mock_response(TRAJECTORY_RESPONSE)

    bad_response = MagicMock()
    bad_response.content = [MagicMock(text="this is not valid json at all !!!")]
    bad_response.usage = MagicMock(input_tokens=100, output_tokens=10)

    with patch("backend.reasoning.tax_trajectory.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        # First call returns garbage, second call returns valid JSON
        mock_client.messages.create = AsyncMock(side_effect=[bad_response, good_response])

        analyzer = TaxTrajectoryAnalyzer()
        result = await analyzer.run(snapshot)

    assert result.current_bracket == 0.32
    assert mock_client.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# Test 7: Fails after two bad responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_step_fails_after_two_bad_responses(snapshot):
    """TaxTrajectoryAnalyzer should raise ReasoningStepError after two bad responses."""
    bad_response = MagicMock()
    bad_response.content = [MagicMock(text="not json at all")]
    bad_response.usage = MagicMock(input_tokens=100, output_tokens=10)

    with patch("backend.reasoning.tax_trajectory.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=bad_response)

        analyzer = TaxTrajectoryAnalyzer()
        with pytest.raises(ReasoningStepError) as exc_info:
            await analyzer.run(snapshot)

    assert "tax_trajectory" in exc_info.value.step_name


# ---------------------------------------------------------------------------
# Test 8: Plan generation blocked — API returns 422
# ---------------------------------------------------------------------------


def test_plan_generation_blocked():
    """POST /api/plans/{client_id}/generate returns 422 when gates are blocked."""
    from fastapi.testclient import TestClient
    from backend.main import app

    client_id = "00000000-0000-0000-0000-000000000001"

    with patch(
        "backend.api.routes.plans.can_generate_plan",
        new=AsyncMock(
            return_value={
                "allowed": False,
                "reason": "Income projections are required (minimum 3 years). Please complete the income projection table.",
            }
        ),
    ):
        with TestClient(app) as test_client:
            response = test_client.post(f"/api/plans/{client_id}/generate")

    assert response.status_code == 422
    data = response.json()
    assert "blocking_reason" in data["detail"]
