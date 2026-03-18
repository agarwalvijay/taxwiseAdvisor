"""
Tests for snapshot assembler (pure function — no DB, no HTTP).

Uses synthetic ExtractionResult objects to test assemble_from_extractions.
"""

import pytest

from backend.models.document import ExtractionResult, FieldConfidence
from backend.extraction.snapshot_assembler import (
    assemble_from_extractions,
    SnapshotAssemblyError,
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


def make_1040(
    agi=207840.0,
    wages=185000.0,
    filing_status="married_filing_jointly",
    state="IL",
    taxable_income=170000.0,
    tax_year=2024,
) -> ExtractionResult:
    return make_extraction(
        "form_1040",
        tax_year=tax_year,
        fields={
            "agi": (agi, 0.99),
            "wages_salaries_tips": (wages, 0.99),
            "filing_status": (filing_status, 0.99),
            "state_of_residence": (state, 0.97),
            "taxable_income": (taxable_income, 0.99),
        },
    )


def make_brokerage(
    total_value=487250.0,
    institution="fidelity",
    tax_year=2024,
) -> ExtractionResult:
    return make_extraction(
        "brokerage_statement",
        institution=institution,
        tax_year=tax_year,
        fields={
            "total_account_value": (total_value, 0.99),
        },
    )


def make_ira(
    balance=215000.0,
    institution="vanguard",
    tax_year=2024,
) -> ExtractionResult:
    return make_extraction(
        "retirement_ira",
        institution=institution,
        tax_year=tax_year,
        fields={
            "account_value": (balance, 0.99),
        },
    )


def make_401k(
    balance=842000.0,
    institution="fidelity",
    tax_year=2024,
) -> ExtractionResult:
    return make_extraction(
        "retirement_401k",
        institution=institution,
        tax_year=tax_year,
        fields={
            "account_value": (balance, 0.99),
        },
    )


# Standard advisor confirmations providing age and retirement_target_age
STANDARD_CONFIRMATIONS = {
    "personal.age": {"confirmed_value": 54, "original_extracted": None, "confirmed_at": "2026-01-01T00:00:00"},
    "personal.retirement_target_age": {"confirmed_value": 62, "original_extracted": None, "confirmed_at": "2026-01-01T00:00:00"},
}


def standard_extractions():
    return [make_1040(), make_brokerage(), make_ira(), make_401k()]


def assemble_standard(**kwargs):
    """Assemble with standard extractions and confirmations."""
    extractions = kwargs.pop("extractions", standard_extractions())
    confirmations = kwargs.pop("confirmations", STANDARD_CONFIRMATIONS)
    return assemble_from_extractions(
        client_id="client-123",
        extractions=extractions,
        advisor_confirmations=confirmations,
        snapshot_date="2026-01-01T00:00:00",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_assemble_populates_filing_status():
    snapshot = assemble_standard()
    assert snapshot.personal.filing_status == "married_filing_jointly"


def test_assemble_populates_agi():
    snapshot = assemble_standard()
    assert snapshot.income.current_year_agi == 207840.0


def test_assemble_populates_state():
    snapshot = assemble_standard()
    assert snapshot.personal.state == "IL"


def test_assemble_brokerage_account():
    snapshot = assemble_standard()
    assert len(snapshot.accounts.taxable_brokerage) == 1
    brokerage = snapshot.accounts.taxable_brokerage[0]
    assert brokerage.institution == "fidelity"
    assert brokerage.total_value == 487250.0


def test_assemble_ira_account():
    snapshot = assemble_standard()
    assert len(snapshot.accounts.traditional_ira) == 1
    ira = snapshot.accounts.traditional_ira[0]
    assert ira.institution == "vanguard"
    assert ira.balance == 215000.0


def test_assemble_401k_account():
    snapshot = assemble_standard()
    assert len(snapshot.accounts.traditional_401k) == 1
    k401 = snapshot.accounts.traditional_401k[0]
    assert k401.institution == "fidelity"
    assert k401.balance == 842000.0


def test_assemble_data_provenance_source_documents():
    snapshot = assemble_standard()
    provenance = snapshot.data_provenance
    assert "form_1040" in provenance.source_documents
    assert "brokerage_statement" in provenance.source_documents
    assert "retirement_ira" in provenance.source_documents
    assert "retirement_401k" in provenance.source_documents


def test_advisor_override_takes_precedence():
    """Confirmed AGI should override the extracted value."""
    confirmations = {
        **STANDARD_CONFIRMATIONS,
        "income.current_year_agi": {
            "confirmed_value": 250000.0,
            "original_extracted": 207840.0,
            "confirmed_at": "2026-01-01T00:00:00",
        },
    }
    snapshot = assemble_standard(confirmations=confirmations)
    assert snapshot.income.current_year_agi == 250000.0


def test_assemble_raises_when_hard_required_missing():
    """Omitting age and retirement_target_age → SnapshotAssemblyError."""
    with pytest.raises(SnapshotAssemblyError) as exc_info:
        assemble_from_extractions(
            client_id="client-123",
            extractions=standard_extractions(),
            advisor_confirmations={},  # No age or retirement_target_age
            snapshot_date="2026-01-01T00:00:00",
        )
    error = exc_info.value
    assert "personal.age" in error.missing_fields
    assert "personal.retirement_target_age" in error.missing_fields


def test_assemble_computes_tax_profile():
    """MFJ with taxable_income=170000 → 22% marginal bracket."""
    snapshot = assemble_standard()
    # 170000 is in the 22% MFJ bracket (96950–206700)
    assert snapshot.tax_profile.current_marginal_bracket == 0.22


def test_assemble_computes_niit_exposure():
    """AGI 260000 MFJ exceeds NIIT threshold of 250000 → niit_exposure=True."""
    confirmations = {
        **STANDARD_CONFIRMATIONS,
        "income.current_year_agi": {
            "confirmed_value": 260000.0,
            "original_extracted": 260000.0,
            "confirmed_at": "2026-01-01T00:00:00",
        },
    }
    extractions = [make_1040(agi=260000.0), make_brokerage(), make_ira()]
    snapshot = assemble_standard(extractions=extractions, confirmations=confirmations)
    assert snapshot.tax_profile.niit_exposure is True


def test_assemble_rmd_profile_years_until_rmd():
    """age=54 → years_until_rmd = 73 - 54 = 19."""
    snapshot = assemble_standard()
    assert snapshot.rmd_profile.years_until_rmd == 19
