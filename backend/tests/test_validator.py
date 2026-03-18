"""
Tests for cross-document consistency validator.

Validates that:
1. Consistent document sets pass validation
2. Tax year mismatches > 1 year are contradictions
3. Tax year mismatches of 1 year are warnings (not blocking)
4. Duplicate documents (same type+institution+year) are contradictions
5. W-2 wages matching 1040 within tolerance passes
6. W-2 wages deviating > 2% from 1040 is a contradiction
7. Brokerage income exceeding 1040 income is a warning
8. Negative retirement balance is a contradiction
9. Zero retirement balance is a warning
10. Single document skips cross-checks
"""

import pytest

from backend.models.document import ExtractionResult, FieldConfidence
from backend.extraction.validator import (
    validate_documents,
    check_tax_year_consistency,
    check_duplicate_documents,
    check_w2_wages_match_1040,
    check_brokerage_income_consistent_with_1040,
    check_retirement_balance_plausibility,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_extraction(
    document_type: str,
    institution: str | None = None,
    tax_year: int | None = 2024,
    fields: dict | None = None,
) -> ExtractionResult:
    typed_fields = {}
    if fields:
        for name, (val, conf) in fields.items():
            typed_fields[name] = FieldConfidence(value=val, confidence=conf)
    return ExtractionResult(
        document_type=document_type,
        institution=institution,
        tax_year=tax_year,
        fields=typed_fields,
        extraction_notes=[],
        overall_confidence=0.95,
    )


def make_1040(agi=207840.0, wages=185000.0, dividends=9100.0, interest=1240.0, tax_year=2024):
    return make_extraction(
        "form_1040",
        tax_year=tax_year,
        fields={
            "agi": (agi, 0.99),
            "wages_salaries_tips": (wages, 0.99),
            "ordinary_dividends": (dividends, 0.99),
            "taxable_interest": (interest, 0.99),
        },
    )


def make_brokerage(dividends=8400.0, interest=1240.0, institution="fidelity", tax_year=2024):
    return make_extraction(
        "brokerage_statement",
        institution=institution,
        tax_year=tax_year,
        fields={
            "ytd_dividends": (dividends, 0.98),
            "ytd_interest": (interest, 0.98),
            "total_account_value": (487250.0, 0.99),
        },
    )


def make_w2(wages=185000.0, tax_year=2024):
    return make_extraction(
        "w2",
        tax_year=tax_year,
        fields={"box1_wages": (wages, 0.99)},
    )


def make_ira(balance=215000.0, tax_year=2024, institution="vanguard"):
    return make_extraction(
        "retirement_ira",
        institution=institution,
        tax_year=tax_year,
        fields={"account_value": (balance, 0.99)},
    )


def make_401k(balance=842000.0, tax_year=2024, institution="fidelity"):
    return make_extraction(
        "retirement_401k",
        institution=institution,
        tax_year=tax_year,
        fields={"account_value": (balance, 0.99)},
    )


# ============================================================================
# OVERALL VALIDATE_DOCUMENTS FUNCTION
# ============================================================================

def test_single_document_skips_cross_checks():
    result = validate_documents([make_1040()])
    assert result.passed is True
    assert "skipped_single_document" in result.checks_run


def test_consistent_documents_pass():
    docs = [make_1040(), make_brokerage(), make_ira(), make_401k()]
    result = validate_documents(docs)
    assert result.passed is True
    assert len(result.contradictions) == 0


def test_validation_result_has_checks_run():
    docs = [make_1040(), make_brokerage()]
    result = validate_documents(docs)
    assert len(result.checks_run) > 0


def test_validation_result_structure():
    docs = [make_1040(), make_brokerage()]
    result = validate_documents(docs)
    assert hasattr(result, "passed")
    assert hasattr(result, "issues")
    assert hasattr(result, "checks_run")
    assert isinstance(result.issues, list)


# ============================================================================
# TAX YEAR CONSISTENCY
# ============================================================================

def test_tax_year_consistency_same_year_passes():
    docs = [make_1040(tax_year=2024), make_brokerage(tax_year=2024)]
    issues = check_tax_year_consistency(docs)
    assert len(issues) == 0


def test_tax_year_consistency_consecutive_years_is_warning():
    docs = [make_1040(tax_year=2023), make_brokerage(tax_year=2024)]
    issues = check_tax_year_consistency(docs)
    assert len(issues) == 1
    assert issues[0].severity == "warning"


def test_tax_year_consistency_two_year_gap_is_contradiction():
    docs = [make_1040(tax_year=2022), make_brokerage(tax_year=2024)]
    issues = check_tax_year_consistency(docs)
    assert len(issues) == 1
    assert issues[0].severity == "contradiction"


def test_tax_year_consistency_three_year_gap_is_contradiction():
    docs = [make_1040(tax_year=2020), make_brokerage(tax_year=2024)]
    issues = check_tax_year_consistency(docs)
    contradictions = [i for i in issues if i.severity == "contradiction"]
    assert len(contradictions) >= 1


def test_tax_year_consistency_null_years_skipped():
    """Documents with no tax_year should not cause false contradiction."""
    doc1 = make_extraction("form_1040", tax_year=None)
    doc2 = make_extraction("brokerage_statement", tax_year=None)
    issues = check_tax_year_consistency([doc1, doc2])
    assert len(issues) == 0


def test_tax_year_contradiction_includes_resolution():
    docs = [make_1040(tax_year=2020), make_brokerage(tax_year=2024)]
    issues = check_tax_year_consistency(docs)
    contradiction = next(i for i in issues if i.severity == "contradiction")
    assert len(contradiction.suggested_resolution) > 10


# ============================================================================
# DUPLICATE DOCUMENT DETECTION
# ============================================================================

def test_duplicate_detection_no_duplicates_passes():
    docs = [make_1040(), make_brokerage(institution="fidelity"), make_ira()]
    issues = check_duplicate_documents(docs)
    assert len(issues) == 0


def test_duplicate_detection_same_type_same_institution_is_contradiction():
    docs = [
        make_brokerage(institution="fidelity", tax_year=2024),
        make_brokerage(institution="fidelity", tax_year=2024),
    ]
    issues = check_duplicate_documents(docs)
    assert len(issues) == 1
    assert issues[0].severity == "contradiction"


def test_duplicate_detection_same_type_different_institution_passes():
    docs = [
        make_brokerage(institution="fidelity", tax_year=2024),
        make_brokerage(institution="schwab", tax_year=2024),
    ]
    issues = check_duplicate_documents(docs)
    assert len(issues) == 0


def test_duplicate_detection_same_type_different_year_passes():
    docs = [
        make_brokerage(institution="fidelity", tax_year=2024),
        make_brokerage(institution="fidelity", tax_year=2023),
    ]
    issues = check_duplicate_documents(docs)
    assert len(issues) == 0


def test_duplicate_1040_is_contradiction():
    docs = [make_1040(tax_year=2024), make_1040(tax_year=2024)]
    issues = check_duplicate_documents(docs)
    assert len(issues) == 1
    assert issues[0].severity == "contradiction"


# ============================================================================
# W-2 WAGES vs 1040 CHECK
# ============================================================================

def test_w2_wages_matching_1040_passes():
    docs = [make_1040(wages=185000.0), make_w2(wages=185000.0)]
    issues = check_w2_wages_match_1040(docs)
    assert len(issues) == 0


def test_w2_wages_within_tolerance_passes():
    # 2% tolerance, so 185000 * 0.02 = 3700 buffer
    docs = [make_1040(wages=185000.0), make_w2(wages=182000.0)]
    issues = check_w2_wages_match_1040(docs)
    assert len(issues) == 0


def test_w2_wages_exceed_tolerance_is_contradiction():
    # $25,000 difference — way over 2% tolerance
    docs = [make_1040(wages=185000.0), make_w2(wages=160000.0)]
    issues = check_w2_wages_match_1040(docs)
    assert len(issues) == 1
    assert issues[0].severity == "contradiction"


def test_w2_wages_contradiction_includes_amounts():
    docs = [make_1040(wages=185000.0), make_w2(wages=140000.0)]
    issues = check_w2_wages_match_1040(docs)
    assert len(issues) == 1
    assert "185,000" in issues[0].description or "185000" in issues[0].description


def test_w2_check_skipped_without_w2():
    docs = [make_1040(), make_brokerage()]
    issues = check_w2_wages_match_1040(docs)
    assert len(issues) == 0


def test_w2_check_skipped_without_1040():
    docs = [make_w2(), make_brokerage()]
    issues = check_w2_wages_match_1040(docs)
    assert len(issues) == 0


def test_multiple_w2_wages_summed():
    """Two W-2s should be summed before comparing to 1040."""
    w2a = make_extraction("w2", tax_year=2024, fields={"box1_wages": (100000.0, 0.99)})
    w2b = make_extraction("w2", tax_year=2024, fields={"box1_wages": (85000.0, 0.99)})
    f1040 = make_1040(wages=185000.0)  # sum of both W-2s
    issues = check_w2_wages_match_1040([f1040, w2a, w2b])
    assert len(issues) == 0


# ============================================================================
# BROKERAGE INCOME vs 1040 CHECK
# ============================================================================

def test_brokerage_income_within_1040_passes():
    # Brokerage dividends (8400) < 1040 dividends (9100) — multiple accounts expected
    docs = [make_1040(dividends=9100.0, interest=1240.0), make_brokerage(dividends=8400.0, interest=1240.0)]
    issues = check_brokerage_income_consistent_with_1040(docs)
    assert len(issues) == 0


def test_brokerage_dividends_exceeding_1040_is_warning():
    # Brokerage shows MORE dividends than the 1040 — suspicious
    docs = [make_1040(dividends=5000.0), make_brokerage(dividends=9100.0)]
    issues = check_brokerage_income_consistent_with_1040(docs)
    dividend_issues = [i for i in issues if "dividend" in i.description.lower()]
    assert len(dividend_issues) == 1
    assert dividend_issues[0].severity == "warning"


def test_brokerage_interest_exceeding_1040_is_warning():
    docs = [make_1040(interest=500.0), make_brokerage(interest=1500.0)]
    issues = check_brokerage_income_consistent_with_1040(docs)
    interest_issues = [i for i in issues if "interest" in i.description.lower()]
    assert len(interest_issues) == 1
    assert interest_issues[0].severity == "warning"


def test_brokerage_income_check_skipped_without_both_docs():
    docs = [make_brokerage()]  # No 1040
    issues = check_brokerage_income_consistent_with_1040(docs)
    assert len(issues) == 0


def test_brokerage_income_warning_is_not_blocking():
    """Brokerage income warnings should not block the overall validation."""
    docs = [make_1040(dividends=5000.0), make_brokerage(dividends=9100.0)]
    result = validate_documents(docs)
    # All issues are warnings, no contradictions
    assert len(result.contradictions) == 0
    # passed=True because no contradictions (only warnings are non-blocking)
    assert result.passed is True


# ============================================================================
# RETIREMENT BALANCE PLAUSIBILITY
# ============================================================================

def test_positive_retirement_balance_passes():
    docs = [make_ira(balance=215000.0), make_401k(balance=842000.0)]
    issues = check_retirement_balance_plausibility(docs)
    assert len(issues) == 0


def test_negative_retirement_balance_is_contradiction():
    docs = [make_ira(balance=-5000.0)]
    issues = check_retirement_balance_plausibility(docs)
    assert len(issues) == 1
    assert issues[0].severity == "contradiction"


def test_zero_retirement_balance_is_warning():
    docs = [make_401k(balance=0.0)]
    issues = check_retirement_balance_plausibility(docs)
    assert len(issues) == 1
    assert issues[0].severity == "warning"


def test_very_large_balance_is_warning():
    docs = [make_ira(balance=100_000_000.0)]
    issues = check_retirement_balance_plausibility(docs)
    assert len(issues) == 1
    assert issues[0].severity == "warning"


def test_normal_large_balance_does_not_warn():
    docs = [make_ira(balance=2_500_000.0)]  # $2.5M is normal for this platform
    issues = check_retirement_balance_plausibility(docs)
    assert len(issues) == 0


def test_negative_balance_blocks_validation():
    """A negative retirement balance is a contradiction → passed=False."""
    docs = [make_1040(), make_ira(balance=-100.0)]
    result = validate_documents(docs)
    assert result.passed is False
    assert len(result.contradictions) >= 1


# ============================================================================
# COMBINED SCENARIO TESTS
# ============================================================================

def test_full_consistent_set_passes():
    """Full set of realistic consistent documents should pass validation."""
    docs = [
        make_1040(agi=207840.0, wages=185000.0, dividends=9100.0, interest=1240.0),
        make_brokerage(dividends=8400.0, interest=1240.0),
        make_ira(balance=215000.0),
        make_401k(balance=842000.0),
        make_w2(wages=185000.0),
    ]
    result = validate_documents(docs)
    assert result.passed is True


def test_multiple_contradictions_all_surfaced():
    """Multiple contradictions should all appear in the result."""
    docs = [
        make_1040(tax_year=2020, wages=185000.0),  # year far off
        make_ira(balance=-500.0, tax_year=2024),   # negative balance
        make_1040(tax_year=2024, wages=185000.0),  # duplicate 1040
    ]
    result = validate_documents(docs)
    assert result.passed is False
    # Should have at least: tax year contradiction + negative balance + duplicate 1040
    assert len(result.contradictions) >= 2
