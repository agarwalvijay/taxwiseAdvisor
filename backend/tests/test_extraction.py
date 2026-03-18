"""
Tests for all document extractors.

All tests mock the Anthropic API to avoid network calls in CI.
Tests validate:
1. Parser correctly maps Claude's JSON response into typed ExtractionResult
2. Key fields are present with correct values and types
3. Confidence scores are returned per field
4. Null fields are preserved (no hallucination)
5. Institution and tax_year are derived correctly
"""

import copy
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from backend.models.document import ExtractionResult, FieldConfidence
from backend.extraction.extractors.form_1040 import Form1040Extractor, _parse_extraction_response
from backend.extraction.extractors.brokerage import BrokerageExtractor, _parse_extraction_response as _parse_brokerage
from backend.extraction.extractors.retirement_account import (
    TraditionalIRAExtractor,
    Retirement401kExtractor,
    _parse_extraction_response as _parse_retirement,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures (file text)
# ---------------------------------------------------------------------------

@pytest.fixture
def form_1040_text():
    return (FIXTURES / "form_1040_2024.txt").read_text()

@pytest.fixture
def brokerage_fidelity_text():
    return (FIXTURES / "brokerage_fidelity_2024.txt").read_text()

@pytest.fixture
def brokerage_schwab_text():
    return (FIXTURES / "brokerage_schwab_2024.txt").read_text()

@pytest.fixture
def ira_text():
    return (FIXTURES / "ira_vanguard_2024.txt").read_text()

@pytest.fixture
def retirement_401k_text():
    return (FIXTURES / "retirement_401k_fidelity_2024.txt").read_text()


# ---------------------------------------------------------------------------
# Simulated Claude responses (matching fixture data)
# ---------------------------------------------------------------------------

MOCK_1040_RESPONSE = {
    "tax_year": {"value": 2024, "confidence": 0.99, "inferred": False, "note": None},
    "filing_status": {"value": "married_filing_jointly", "confidence": 0.99, "inferred": False, "note": None},
    "state_of_residence": {"value": "IL", "confidence": 0.97, "inferred": False, "note": "From mailing address"},
    "wages_salaries_tips": {"value": 185000.00, "confidence": 0.99, "inferred": False, "note": "Line 1a"},
    "taxable_interest": {"value": 1240.00, "confidence": 0.99, "inferred": False, "note": "Line 2b"},
    "tax_exempt_interest": {"value": 0.00, "confidence": 0.99, "inferred": False, "note": "Line 2a"},
    "qualified_dividends": {"value": 8400.00, "confidence": 0.99, "inferred": False, "note": "Line 3a"},
    "ordinary_dividends": {"value": 9100.00, "confidence": 0.99, "inferred": False, "note": "Line 3b"},
    "ira_distributions_total": {"value": None, "confidence": 0.99, "inferred": False, "note": "Not reported"},
    "ira_distributions_taxable": {"value": None, "confidence": 0.99, "inferred": False, "note": "Not reported"},
    "pensions_annuities_total": {"value": None, "confidence": 0.99, "inferred": False, "note": "Not reported"},
    "pensions_annuities_taxable": {"value": None, "confidence": 0.99, "inferred": False, "note": "Not reported"},
    "social_security_total": {"value": None, "confidence": 0.99, "inferred": False, "note": "Not reported"},
    "social_security_taxable": {"value": None, "confidence": 0.99, "inferred": False, "note": "Not reported"},
    "capital_gains_or_loss": {"value": 12500.00, "confidence": 0.99, "inferred": False, "note": "Line 7"},
    "schedule_1_additional_income": {"value": None, "confidence": 0.98, "inferred": False, "note": "None reported"},
    "total_income": {"value": 207840.00, "confidence": 0.99, "inferred": False, "note": "Line 9"},
    "adjustments_to_income": {"value": None, "confidence": 0.98, "inferred": False, "note": "Line 10"},
    "agi": {"value": 207840.00, "confidence": 0.99, "inferred": False, "note": "Line 11"},
    "standard_deduction_amount": {"value": 29200.00, "confidence": 0.99, "inferred": False, "note": "Line 12a"},
    "itemized_deductions": {"value": None, "confidence": 0.99, "inferred": False, "note": "Standard deduction taken"},
    "qualified_business_income_deduction": {"value": None, "confidence": 0.99, "inferred": False, "note": "Line 13"},
    "taxable_income": {"value": 178640.00, "confidence": 0.99, "inferred": False, "note": "Line 15"},
    "total_tax": {"value": 26467.00, "confidence": 0.99, "inferred": False, "note": "Line 24"},
    "federal_income_tax_withheld": {"value": 28000.00, "confidence": 0.99, "inferred": False, "note": "Line 25a"},
    "estimated_tax_payments": {"value": None, "confidence": 0.99, "inferred": False, "note": "Line 25b"},
    "refund_or_amount_owed": {"value": 1533.00, "confidence": 0.99, "inferred": False, "note": "Line 32 — refund"},
    "extraction_notes": ["Complete 2024 Form 1040, all lines legible."],
    "overall_confidence": 0.99,
}

MOCK_BROKERAGE_RESPONSE = {
    "statement_date": {"value": "2024-12-31", "confidence": 0.99, "inferred": False, "note": None},
    "institution": {"value": "fidelity", "confidence": 0.99, "inferred": False, "note": None},
    "account_type": {"value": "taxable_brokerage", "confidence": 0.99, "inferred": False, "note": None},
    "account_number_last4": {"value": "7842", "confidence": 0.98, "inferred": False, "note": None},
    "total_account_value": {"value": 487250.00, "confidence": 0.99, "inferred": False, "note": None},
    "cash_and_money_market": {"value": 12400.00, "confidence": 0.99, "inferred": False, "note": None},
    "total_securities_value": {"value": 474850.00, "confidence": 0.97, "inferred": True, "note": "total minus cash"},
    "total_cost_basis": {"value": 390950.00, "confidence": 0.91, "inferred": False, "note": "All positions have known basis"},
    "total_unrealized_gain_loss": {"value": 34290.20, "confidence": 0.90, "inferred": True, "note": None},
    "ytd_dividends": {"value": 8400.00, "confidence": 0.98, "inferred": False, "note": None},
    "ytd_interest": {"value": 1240.00, "confidence": 0.98, "inferred": False, "note": None},
    "ytd_realized_gains": {"value": 12500.00, "confidence": 0.96, "inferred": False, "note": None},
    "holdings": {
        "value": [
            {
                "symbol": "VTI", "description": "Vanguard Total Stock Market ETF",
                "shares": 142.5, "price_per_share": 245.10, "market_value": 34927.00,
                "cost_basis": 28400.00, "unrealized_gain_loss": 6527.00,
                "holding_period": "long_term", "confidence": 0.99
            },
            {
                "symbol": "IVV", "description": "iShares Core S&P 500 ETF",
                "shares": 210.0, "price_per_share": 487.25, "market_value": 102322.50,
                "cost_basis": 89600.00, "unrealized_gain_loss": 12722.50,
                "holding_period": "long_term", "confidence": 0.98
            },
            {
                "symbol": "AMZN", "description": "Amazon.com Inc",
                "shares": 100.0, "price_per_share": 218.45, "market_value": 21845.00,
                "cost_basis": 19500.00, "unrealized_gain_loss": 2345.00,
                "holding_period": "short_term", "confidence": 0.97
            },
        ],
        "confidence": 0.97, "inferred": False, "note": "9 positions extracted"
    },
    "extraction_notes": ["Fidelity Q4 2024 statement with 9 positions."],
    "overall_confidence": 0.96,
}

MOCK_IRA_RESPONSE = {
    "account_type": {"value": "traditional_ira", "confidence": 0.99, "inferred": False, "note": None},
    "institution": {"value": "vanguard", "confidence": 0.99, "inferred": False, "note": None},
    "employer_name": {"value": None, "confidence": 0.99, "inferred": False, "note": "IRA — no employer"},
    "plan_name": {"value": None, "confidence": 0.99, "inferred": False, "note": "IRA — no plan name"},
    "statement_date": {"value": "2024-12-31", "confidence": 0.99, "inferred": False, "note": None},
    "account_value": {"value": 215000.00, "confidence": 0.99, "inferred": False, "note": "Ending balance"},
    "vested_balance": {"value": None, "confidence": 0.99, "inferred": False, "note": "IRA — no vesting"},
    "roth_sub_account_balance": {"value": None, "confidence": 0.99, "inferred": False, "note": None},
    "ytd_employee_contributions": {"value": 7000.00, "confidence": 0.98, "inferred": False, "note": None},
    "ytd_employer_contributions": {"value": None, "confidence": 0.99, "inferred": False, "note": "IRA — no employer"},
    "non_deductible_basis": {"value": None, "confidence": 0.90, "inferred": False, "note": "Not tracked by Vanguard"},
    "ytd_distributions": {"value": None, "confidence": 0.99, "inferred": False, "note": "No distributions"},
    "holdings_summary": {
        "value": [
            {"fund_name": "Vanguard Total Stock Market Index", "ticker": "VTSAX",
             "allocation_pct": 100.0, "market_value": 215000.00, "confidence": 0.99}
        ],
        "confidence": 0.99, "inferred": False, "note": "Single fund"
    },
    "extraction_notes": ["Vanguard Traditional IRA, Q4 2024."],
    "overall_confidence": 0.98,
}

MOCK_401K_RESPONSE = {
    "account_type": {"value": "traditional_401k", "confidence": 0.99, "inferred": False, "note": None},
    "institution": {"value": "fidelity", "confidence": 0.99, "inferred": False, "note": None},
    "employer_name": {"value": "Acme Corporation", "confidence": 0.97, "inferred": False, "note": None},
    "plan_name": {"value": "Acme Corp 401(k) Retirement Savings Plan", "confidence": 0.96, "inferred": False, "note": None},
    "statement_date": {"value": "2024-12-31", "confidence": 0.99, "inferred": False, "note": None},
    "account_value": {"value": 842000.00, "confidence": 0.99, "inferred": False, "note": "Ending balance"},
    "vested_balance": {"value": 798000.00, "confidence": 0.97, "inferred": False, "note": "94.78% vested"},
    "roth_sub_account_balance": {"value": None, "confidence": 0.99, "inferred": False, "note": "No Roth sub-account"},
    "ytd_employee_contributions": {"value": 23000.00, "confidence": 0.98, "inferred": False, "note": None},
    "ytd_employer_contributions": {"value": 9200.00, "confidence": 0.97, "inferred": False, "note": "50% match up to 8%"},
    "non_deductible_basis": {"value": None, "confidence": 0.99, "inferred": False, "note": "401k — not applicable"},
    "ytd_distributions": {"value": None, "confidence": 0.99, "inferred": False, "note": "No distributions"},
    "holdings_summary": {
        "value": [
            {"fund_name": "Fidelity 500 Index Fund", "ticker": "FXAIX",
             "allocation_pct": 60.0, "market_value": 505200.00, "confidence": 0.98},
            {"fund_name": "Fidelity U.S. Bond Index Fund", "ticker": "FXNAX",
             "allocation_pct": 25.0, "market_value": 210500.00, "confidence": 0.97},
        ],
        "confidence": 0.96, "inferred": False, "note": "4 funds total"
    },
    "extraction_notes": ["Fidelity NetBenefits 401k, full year 2024."],
    "overall_confidence": 0.97,
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _mock_anthropic(response_dict: dict):
    mock_content = MagicMock()
    mock_content.text = json.dumps(response_dict)
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    return mock_client


# ============================================================================
# FORM 1040 TESTS
# ============================================================================

def test_1040_parse_returns_extraction_result():
    result = _parse_extraction_response(copy.deepcopy(MOCK_1040_RESPONSE))
    assert isinstance(result, ExtractionResult)
    assert result.document_type == "form_1040"


def test_1040_tax_year():
    result = _parse_extraction_response(copy.deepcopy(MOCK_1040_RESPONSE))
    assert result.tax_year == 2024


def test_1040_agi():
    result = _parse_extraction_response(copy.deepcopy(MOCK_1040_RESPONSE))
    assert result.fields["agi"].value == 207840.00
    assert result.fields["agi"].confidence >= 0.99


def test_1040_filing_status():
    result = _parse_extraction_response(copy.deepcopy(MOCK_1040_RESPONSE))
    assert result.fields["filing_status"].value == "married_filing_jointly"


def test_1040_wages():
    result = _parse_extraction_response(copy.deepcopy(MOCK_1040_RESPONSE))
    assert result.fields["wages_salaries_tips"].value == 185000.00


def test_1040_capital_gains():
    result = _parse_extraction_response(copy.deepcopy(MOCK_1040_RESPONSE))
    assert result.fields["capital_gains_or_loss"].value == 12500.00


def test_1040_null_ira_distributions():
    result = _parse_extraction_response(copy.deepcopy(MOCK_1040_RESPONSE))
    assert result.fields["ira_distributions_total"].value is None


def test_1040_all_fields_have_confidence():
    result = _parse_extraction_response(copy.deepcopy(MOCK_1040_RESPONSE))
    for name, fc in result.fields.items():
        assert isinstance(fc.confidence, float), f"{name} missing confidence"
        assert 0.0 <= fc.confidence <= 1.0


@pytest.mark.asyncio
async def test_1040_extractor_integration(form_1040_text):
    extractor = Form1040Extractor()
    with patch("backend.extraction.extractors.form_1040.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_anthropic(copy.deepcopy(MOCK_1040_RESPONSE))
        result = await extractor.extract(form_1040_text)
    assert result.document_type == "form_1040"
    assert result.tax_year == 2024
    assert result.fields["agi"].value == 207840.00


# ============================================================================
# BROKERAGE EXTRACTOR TESTS
# ============================================================================

def test_brokerage_parse_returns_extraction_result():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    assert isinstance(result, ExtractionResult)
    assert result.document_type == "brokerage_statement"


def test_brokerage_institution():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    assert result.institution == "fidelity"


def test_brokerage_tax_year_from_statement_date():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    assert result.tax_year == 2024


def test_brokerage_total_account_value():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    assert result.fields["total_account_value"].value == 487250.00


def test_brokerage_cash_balance():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    assert result.fields["cash_and_money_market"].value == 12400.00


def test_brokerage_ytd_dividends():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    assert result.fields["ytd_dividends"].value == 8400.00


def test_brokerage_ytd_interest():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    assert result.fields["ytd_interest"].value == 1240.00


def test_brokerage_total_cost_basis():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    assert result.fields["total_cost_basis"].value == 390950.00


def test_brokerage_holdings_present():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    holdings = result.fields["holdings"].value
    assert isinstance(holdings, list)
    assert len(holdings) == 3


def test_brokerage_holdings_has_symbol_and_value():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    holdings = result.fields["holdings"].value
    symbols = [h["symbol"] for h in holdings]
    assert "VTI" in symbols
    assert "IVV" in symbols


def test_brokerage_short_term_holding_period():
    result = _parse_brokerage(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
    amzn = next(h for h in result.fields["holdings"].value if h["symbol"] == "AMZN")
    assert amzn["holding_period"] == "short_term"


def test_brokerage_null_cost_basis_preserved():
    data = copy.deepcopy(MOCK_BROKERAGE_RESPONSE)
    data["total_cost_basis"]["value"] = None
    result = _parse_brokerage(data)
    assert result.fields["total_cost_basis"].value is None


@pytest.mark.asyncio
async def test_brokerage_extractor_fidelity_integration(brokerage_fidelity_text):
    extractor = BrokerageExtractor()
    with patch("backend.extraction.extractors.brokerage.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_anthropic(copy.deepcopy(MOCK_BROKERAGE_RESPONSE))
        result = await extractor.extract(brokerage_fidelity_text)
    assert result.document_type == "brokerage_statement"
    assert result.institution == "fidelity"
    assert result.fields["total_account_value"].value == 487250.00


@pytest.mark.asyncio
async def test_brokerage_extractor_schwab_integration(brokerage_schwab_text):
    schwab_response = copy.deepcopy(MOCK_BROKERAGE_RESPONSE)
    schwab_response["institution"]["value"] = "schwab"
    schwab_response["total_account_value"]["value"] = 124380.00
    schwab_response["account_number_last4"]["value"] = "1923"

    extractor = BrokerageExtractor()
    with patch("backend.extraction.extractors.brokerage.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_anthropic(schwab_response)
        result = await extractor.extract(brokerage_schwab_text)
    assert result.institution == "schwab"
    assert result.fields["total_account_value"].value == 124380.00


# ============================================================================
# TRADITIONAL IRA EXTRACTOR TESTS
# ============================================================================

def test_ira_parse_returns_extraction_result():
    result = _parse_retirement(copy.deepcopy(MOCK_IRA_RESPONSE), document_type="retirement_ira")
    assert isinstance(result, ExtractionResult)
    assert result.document_type == "retirement_ira"


def test_ira_institution():
    result = _parse_retirement(copy.deepcopy(MOCK_IRA_RESPONSE), document_type="retirement_ira")
    assert result.institution == "vanguard"


def test_ira_account_value():
    result = _parse_retirement(copy.deepcopy(MOCK_IRA_RESPONSE), document_type="retirement_ira")
    assert result.fields["account_value"].value == 215000.00


def test_ira_account_type_field():
    result = _parse_retirement(copy.deepcopy(MOCK_IRA_RESPONSE), document_type="retirement_ira")
    assert result.fields["account_type"].value == "traditional_ira"


def test_ira_ytd_contributions():
    result = _parse_retirement(copy.deepcopy(MOCK_IRA_RESPONSE), document_type="retirement_ira")
    assert result.fields["ytd_employee_contributions"].value == 7000.00


def test_ira_non_deductible_basis_null():
    result = _parse_retirement(copy.deepcopy(MOCK_IRA_RESPONSE), document_type="retirement_ira")
    assert result.fields["non_deductible_basis"].value is None


def test_ira_no_distributions():
    result = _parse_retirement(copy.deepcopy(MOCK_IRA_RESPONSE), document_type="retirement_ira")
    assert result.fields["ytd_distributions"].value is None


@pytest.mark.asyncio
async def test_ira_extractor_integration(ira_text):
    extractor = TraditionalIRAExtractor()
    with patch("backend.extraction.extractors.retirement_account.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_anthropic(copy.deepcopy(MOCK_IRA_RESPONSE))
        result = await extractor.extract(ira_text)
    # document_type should be refined from retirement_ira → traditional_ira
    assert result.document_type in ("retirement_ira", "traditional_ira")
    assert result.fields["account_value"].value == 215000.00
    assert result.institution == "vanguard"


# ============================================================================
# 401k EXTRACTOR TESTS
# ============================================================================

def test_401k_parse_returns_extraction_result():
    result = _parse_retirement(copy.deepcopy(MOCK_401K_RESPONSE), document_type="retirement_401k")
    assert isinstance(result, ExtractionResult)
    assert result.document_type == "retirement_401k"


def test_401k_employer_name():
    result = _parse_retirement(copy.deepcopy(MOCK_401K_RESPONSE), document_type="retirement_401k")
    assert result.fields["employer_name"].value == "Acme Corporation"


def test_401k_account_value():
    result = _parse_retirement(copy.deepcopy(MOCK_401K_RESPONSE), document_type="retirement_401k")
    assert result.fields["account_value"].value == 842000.00


def test_401k_vested_balance():
    result = _parse_retirement(copy.deepcopy(MOCK_401K_RESPONSE), document_type="retirement_401k")
    assert result.fields["vested_balance"].value == 798000.00


def test_401k_employee_contributions():
    result = _parse_retirement(copy.deepcopy(MOCK_401K_RESPONSE), document_type="retirement_401k")
    assert result.fields["ytd_employee_contributions"].value == 23000.00


def test_401k_employer_contributions():
    result = _parse_retirement(copy.deepcopy(MOCK_401K_RESPONSE), document_type="retirement_401k")
    assert result.fields["ytd_employer_contributions"].value == 9200.00


def test_401k_no_roth_sub_account():
    result = _parse_retirement(copy.deepcopy(MOCK_401K_RESPONSE), document_type="retirement_401k")
    assert result.fields["roth_sub_account_balance"].value is None


def test_401k_holdings_summary():
    result = _parse_retirement(copy.deepcopy(MOCK_401K_RESPONSE), document_type="retirement_401k")
    holdings = result.fields["holdings_summary"].value
    assert isinstance(holdings, list)
    assert len(holdings) == 2
    assert holdings[0]["ticker"] == "FXAIX"


@pytest.mark.asyncio
async def test_401k_extractor_integration(retirement_401k_text):
    extractor = Retirement401kExtractor()
    with patch("backend.extraction.extractors.retirement_account.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_anthropic(copy.deepcopy(MOCK_401K_RESPONSE))
        result = await extractor.extract(retirement_401k_text)
    assert result.document_type in ("retirement_401k", "traditional_401k")
    assert result.fields["account_value"].value == 842000.00
    assert result.fields["employer_name"].value == "Acme Corporation"
