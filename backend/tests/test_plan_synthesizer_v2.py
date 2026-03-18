"""
Tests for Plan Synthesizer v2 — new schema with ClientSnapshotSummary,
DoNothingComparison, ConversionTableSummary, TLHSummary, DataGap.

All Anthropic API calls are mocked — no real API calls are made.
"""
import json
import copy
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.models.snapshot import ClientFinancialSnapshotSchema
from backend.reasoning.plan_synthesizer import PlanSynthesizer, _post_process
from backend.reasoning.tax_trajectory import ReasoningStepError
from backend.models.plan import (
    TaxTrajectoryOutput,
    ConversionOptimizerOutput,
    TLHAdvisorOutput,
    PlanSynthesizerOutput,
    ConversionTableSummary,
    YearlyConversionRow,
    ClientSnapshotSummary,
    DoNothingComparison,
    TLHSummary,
)

# Re-use the synthetic snapshot + step responses from test_reasoning.py
from backend.tests.test_reasoning import (
    SYNTHETIC_SNAPSHOT,
    TRAJECTORY_RESPONSE,
    CONVERSION_RESPONSE,
    TLH_RESPONSE,
    make_mock_response,
)

# ---------------------------------------------------------------------------
# New synthesis response matching v2 schema
# ---------------------------------------------------------------------------

SYNTHESIS_V2_RESPONSE = {
    "executive_summary": (
        "This client has a significant Roth conversion opportunity over the next 5 years. "
        "A 3-year conversion program starting in 2028 converts $546,000 at a blended 16.4% "
        "effective rate — well below the 24%+ bracket at RMD start. Combined with immediate "
        "TLH and asset location moves, this plan is estimated to save $140,000+ in lifetime taxes."
    ),
    "client_snapshot_summary": {
        "age": 54,
        "spouse_age": 51,
        "filing_status": "married_filing_jointly",
        "state": "IL",
        "retirement_target_age": 62,
        "years_to_retirement": 8,
        "current_agi": 271000.0,
        "total_pretax_balance": 1057000.0,
        "total_roth_balance": 88000.0,
        "total_taxable_balance": 487250.0,
        "total_hsa_balance": 24000.0,
        "cash_savings": 65000.0,
        "years_until_rmd": 19,
        "projected_first_rmd": 76642.0,
    },
    "do_nothing_comparison": {
        "projected_rmd_at_73": 76642.0,
        "rmd_bracket": 0.24,
        "irmaa_triggered": True,
        "estimated_lifetime_tax_savings": 140000.0,
        "narrative": (
            "Without Roth conversions, the client's pre-tax balance grows to ~$2,031,000 by age 73, "
            "generating RMDs of $76,642/year. Combined with Social Security, these RMDs trigger the "
            "24% bracket and IRMAA surcharges — an estimated $140,000 in avoidable lifetime taxes."
        ),
    },
    "priority_actions": [
        {
            "priority": 1,
            "category": "roth_conversion",
            "action": "Convert $182,000 from Vanguard Traditional IRA to Roth IRA in 2028, paying $38,877 in combined federal and Illinois state taxes from taxable brokerage funds.",
            "rationale": "With zero income in 2028, client converts at 16.4% blended vs. 24%+ at RMD start. IL taxes the conversion at 4.95% now, but future Roth withdrawals are IL state-tax-free.",
            "estimated_benefit": "$140,000+ in lifetime tax savings across the 3-year conversion program",
            "urgency": "immediate",
            "confidence": "high",
        },
        {
            "priority": 2,
            "category": "tlh",
            "action": "Sell 45 shares of AMZN and 200 shares of INTC to harvest $8,447.50 in long-term losses. Buy XLY and SOXX as replacements within 2 days. Wait 31 days before repurchasing.",
            "rationale": "These positions are at a loss. Harvesting captures LTCG + NIIT savings while replacement ETFs maintain market exposure.",
            "estimated_benefit": "$1,588 in immediate tax savings plus $321 in NIIT reduction",
            "urgency": "this_year",
            "confidence": "high",
        },
        {
            "priority": 3,
            "category": "asset_location",
            "action": "Gradually shift BND (Vanguard Total Bond ETF) from taxable Fidelity to Traditional IRA/401(k) during rebalancing.",
            "rationale": "Bond interest is taxed as ordinary income in a taxable account. Moving bonds to tax-deferred reduces annual tax drag.",
            "estimated_benefit": "Estimated $500–$800/year in reduced tax drag on bond income",
            "urgency": "multi_year",
            "confidence": "medium",
        },
    ],
    "conversion_table": {
        "rows": [
            {
                "year": 2028,
                "pre_conversion_income": 0.0,
                "convert_amount": 182000.0,
                "post_conversion_agi": 182000.0,
                "federal_tax": 29868.0,
                "state_tax": 9009.0,
                "total_tax": 38877.0,
                "effective_rate_pct": 16.4,
                "cumulative_converted": 182000.0,
                "irmaa_safe": True,
                "note": "Fills 22% bracket, stays below IRMAA Tier 1 ($212k)",
            },
            {
                "year": 2029,
                "pre_conversion_income": 0.0,
                "convert_amount": 182000.0,
                "post_conversion_agi": 182000.0,
                "federal_tax": 29868.0,
                "state_tax": 9009.0,
                "total_tax": 38877.0,
                "effective_rate_pct": 16.4,
                "cumulative_converted": 364000.0,
                "irmaa_safe": True,
                "note": "Second year of conversion program",
            },
            {
                "year": 2030,
                "pre_conversion_income": 0.0,
                "convert_amount": 182000.0,
                "post_conversion_agi": 182000.0,
                "federal_tax": 29868.0,
                "state_tax": 9009.0,
                "total_tax": 38877.0,
                "effective_rate_pct": 16.4,
                "cumulative_converted": 546000.0,
                "irmaa_safe": True,
                "note": "Third and final year of conversion program",
            },
        ],
        "total_converted": 546000.0,
        "total_tax_paid": 116631.0,
        "blended_effective_rate_pct": 16.4,
        "il_state_tax_note": "Illinois taxes Roth conversions as ordinary income at 4.95%, but future Roth IRA withdrawals are Illinois state-tax-free.",
    },
    "tlh_summary": {
        "available": True,
        "total_harvestable_losses": 8447.50,
        "estimated_total_tax_benefit": 1588.13,
        "niit_benefit": 320.99,
        "action_items": [
            "Sell 45 shares AMZN → buy XLY. Est. tax benefit: $587.97 + $118.84 NIIT. Wait 31 days before repurchasing AMZN.",
            "Sell 200 shares INTC → buy SOXX. Est. tax benefit: $1,000.16 + $202.16 NIIT. Wait 31 days before repurchasing INTC.",
        ],
        "unavailable_reason": None,
    },
    "key_assumptions": [
        "Tax rates remain at current law levels through the planning horizon",
        "6% nominal annual growth applied to pre-tax retirement accounts",
        "Social Security benefit estimates as provided by client ($3,520/month at age 70)",
        "Illinois state income tax rate of 4.95% applied to Roth conversions (future Roth withdrawals are IL state-tax-free)",
        "2026 MFJ standard deduction of $30,000 used for all conversion year calculations",
    ],
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
    "narrative": "The most impactful action is a 3-year systematic Roth conversion program beginning in 2028.",
}


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
# Test 1: Happy path — v2 schema validates correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_v2_happy_path(snapshot, trajectory_output, conversion_output, tlh_output):
    """Plan synthesizer returns a valid PlanSynthesizerOutput with new schema."""
    mock_response = make_mock_response(SYNTHESIS_V2_RESPONSE)

    with patch("backend.reasoning.plan_synthesizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        synthesizer = PlanSynthesizer()
        result = await synthesizer.run(snapshot, trajectory_output, conversion_output, tlh_output)

    assert isinstance(result, PlanSynthesizerOutput)
    assert len(result.priority_actions) >= 1
    assert result.executive_summary != ""
    assert result.disclaimer != ""
    assert result.plan_confidence > 0.70
    assert result.urgency in ("high", "medium", "low")

    # New fields
    assert isinstance(result.client_snapshot_summary, ClientSnapshotSummary)
    assert isinstance(result.do_nothing_comparison, DoNothingComparison)
    assert isinstance(result.conversion_table, ConversionTableSummary)
    assert isinstance(result.tlh_summary, TLHSummary)


# ---------------------------------------------------------------------------
# Test 2: cumulative_converted is deterministically post-processed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_cumulative_converted_postprocessed(snapshot, trajectory_output, conversion_output, tlh_output):
    """Post-processing must fill cumulative_converted as a running total."""
    # Give Claude's response wrong cumulative values — post-processing must fix them
    bad_response = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    for row in bad_response["conversion_table"]["rows"]:
        row["cumulative_converted"] = 0.0  # wrong — should be running total

    mock_response = make_mock_response(bad_response)

    with patch("backend.reasoning.plan_synthesizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        synthesizer = PlanSynthesizer()
        result = await synthesizer.run(snapshot, trajectory_output, conversion_output, tlh_output)

    rows = result.conversion_table.rows
    assert len(rows) == 3
    assert rows[0].cumulative_converted == pytest.approx(182000.0, abs=1.0)
    assert rows[1].cumulative_converted == pytest.approx(364000.0, abs=1.0)
    assert rows[2].cumulative_converted == pytest.approx(546000.0, abs=1.0)


# ---------------------------------------------------------------------------
# Test 3: IL state tax corrected by post-processing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_il_state_tax_corrected(snapshot, trajectory_output, conversion_output, tlh_output):
    """For IL clients, state_tax must equal convert_amount × 0.0495 after post-processing."""
    bad_response = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    # Give wrong state_tax values
    for row in bad_response["conversion_table"]["rows"]:
        row["state_tax"] = 0.0  # wrong for IL

    mock_response = make_mock_response(bad_response)

    with patch("backend.reasoning.plan_synthesizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        synthesizer = PlanSynthesizer()
        result = await synthesizer.run(snapshot, trajectory_output, conversion_output, tlh_output)

    for row in result.conversion_table.rows:
        expected_state_tax = round(row.convert_amount * 0.0495, 2)
        assert row.state_tax == pytest.approx(expected_state_tax, abs=1.0), (
            f"Year {row.year}: expected state_tax {expected_state_tax}, got {row.state_tax}"
        )


# ---------------------------------------------------------------------------
# Test 4: total_tax and effective_rate_pct are recalculated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_total_tax_recalculated(snapshot, trajectory_output, conversion_output, tlh_output):
    """Post-processing must recalculate total_tax = federal_tax + state_tax and effective_rate_pct."""
    bad_response = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    for row in bad_response["conversion_table"]["rows"]:
        row["total_tax"] = 99999.0  # wrong
        row["effective_rate_pct"] = 99.9  # wrong

    mock_response = make_mock_response(bad_response)

    with patch("backend.reasoning.plan_synthesizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        synthesizer = PlanSynthesizer()
        result = await synthesizer.run(snapshot, trajectory_output, conversion_output, tlh_output)

    for row in result.conversion_table.rows:
        expected_total = round(row.federal_tax + row.state_tax, 2)
        assert row.total_tax == pytest.approx(expected_total, abs=0.1)
        expected_rate = round(expected_total / row.convert_amount * 100, 1)
        assert row.effective_rate_pct == pytest.approx(expected_rate, abs=0.5)


# ---------------------------------------------------------------------------
# Test 5: Conversion table summary totals are recalculated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_table_totals_recalculated(snapshot, trajectory_output, conversion_output, tlh_output):
    """Post-processing must recalculate total_converted, total_tax_paid, blended_effective_rate_pct."""
    bad_response = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    bad_response["conversion_table"]["total_converted"] = 0.0  # wrong
    bad_response["conversion_table"]["total_tax_paid"] = 0.0  # wrong
    bad_response["conversion_table"]["blended_effective_rate_pct"] = 0.0  # wrong

    mock_response = make_mock_response(bad_response)

    with patch("backend.reasoning.plan_synthesizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        synthesizer = PlanSynthesizer()
        result = await synthesizer.run(snapshot, trajectory_output, conversion_output, tlh_output)

    ct = result.conversion_table
    assert ct.total_converted == pytest.approx(546000.0, abs=1.0)
    assert ct.total_tax_paid > 0
    assert ct.blended_effective_rate_pct > 0


# ---------------------------------------------------------------------------
# Test 6: Empty priority_actions raises ReasoningStepError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_empty_priority_actions_raises(snapshot, trajectory_output, conversion_output, tlh_output):
    """PlanSynthesizer must raise ReasoningStepError if priority_actions is empty."""
    bad_response = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    bad_response["priority_actions"] = []

    mock_response = make_mock_response(bad_response)

    with patch("backend.reasoning.plan_synthesizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        synthesizer = PlanSynthesizer()
        with pytest.raises(ReasoningStepError) as exc_info:
            await synthesizer.run(snapshot, trajectory_output, conversion_output, tlh_output)

    assert "priority_actions" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 7: TLH summary unavailable when no cost basis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_tlh_unavailable_reflected(snapshot, trajectory_output, conversion_output):
    """When TLH is unavailable (tlh_section_complete=False), tlh_summary.available should be False."""
    no_tlh_response = copy.deepcopy(SYNTHESIS_V2_RESPONSE)
    no_tlh_response["tlh_summary"]["available"] = False
    no_tlh_response["tlh_summary"]["total_harvestable_losses"] = 0.0
    no_tlh_response["tlh_summary"]["estimated_total_tax_benefit"] = 0.0
    no_tlh_response["tlh_summary"]["action_items"] = []
    no_tlh_response["tlh_summary"]["unavailable_reason"] = "Cost basis not available for taxable holdings."

    tlh_unavailable = TLHAdvisorOutput(
        tlh_section_complete=False,
        tlh_unavailable_reason="Cost basis not available for taxable holdings.",
        tlh_opportunities=[],
        total_harvestable_losses=0.0,
        estimated_total_tax_benefit=0.0,
        asset_location_moves=[],
        narrative="TLH not available.",
        confidence=0.5,
        data_gaps=[],
    )

    mock_response = make_mock_response(no_tlh_response)

    with patch("backend.reasoning.plan_synthesizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        synthesizer = PlanSynthesizer()
        result = await synthesizer.run(snapshot, trajectory_output, conversion_output, tlh_unavailable)

    assert result.tlh_summary.available is False
    assert result.tlh_summary.unavailable_reason is not None


# ---------------------------------------------------------------------------
# Test 8: Retry on bad JSON succeeds on second attempt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesizer_v2_retry_on_bad_json(snapshot, trajectory_output, conversion_output, tlh_output):
    """PlanSynthesizer should succeed on the second attempt after bad JSON."""
    good_response = make_mock_response(SYNTHESIS_V2_RESPONSE)

    bad_response = MagicMock()
    bad_response.content = [MagicMock(text="this is not valid json at all !!!")]
    bad_response.usage = MagicMock(input_tokens=100, output_tokens=10)

    with patch("backend.reasoning.plan_synthesizer.anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(side_effect=[bad_response, good_response])

        synthesizer = PlanSynthesizer()
        result = await synthesizer.run(snapshot, trajectory_output, conversion_output, tlh_output)

    assert isinstance(result, PlanSynthesizerOutput)
    assert mock_client.messages.create.call_count == 2
