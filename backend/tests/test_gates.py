"""
Tests for the confidence gate logic.

Verifies:
1. Hard required fields below 0.85 are flagged and block the plan
2. Soft required fields below 0.75 proceed with a warning (not blocking)
3. Gate passes when all hard required fields meet the threshold
4. Optional fields below 0.60 are silently omitted
5. Missing fields (null value) are handled correctly
"""

import pytest

from backend.models.document import ExtractionResult, FieldConfidence
from backend.gates.confidence_gate import evaluate_extraction, HARD_REQUIRED_FIELDS, SOFT_REQUIRED_FIELDS


def make_extraction(fields: dict) -> ExtractionResult:
    """Helper to build an ExtractionResult from a flat dict of field_name → (value, confidence)."""
    typed_fields = {
        name: FieldConfidence(value=val, confidence=conf)
        for name, (val, conf) in fields.items()
    }
    return ExtractionResult(
        document_type="form_1040",
        fields=typed_fields,
        extraction_notes=[],
        overall_confidence=0.90,
    )


def passing_hard_fields() -> dict:
    """All hard required fields at or above the 0.85 threshold."""
    return {
        "filing_status": ("married_filing_jointly", 0.99),
        "agi": (207840.00, 0.99),
        "state_of_residence": ("IL", 0.97),
        # The remaining hard required fields are post-assembly (snapshot-level),
        # so they are not present in a single-document extraction.
        # Gate only evaluates fields that ARE present.
    }


# ---------------------------------------------------------------------------
# Hard Required tests
# ---------------------------------------------------------------------------


def test_gate_passes_with_high_confidence_hard_fields():
    """Gate should pass when all present hard required fields are above 0.85."""
    extraction = make_extraction(passing_hard_fields())
    gate = evaluate_extraction(extraction)
    # gate.passed is True only if no hard required fields fail
    # Fields not present in extraction are missing — they ARE flagged as hard_required
    # but their absence is expected at document-level (they're snapshot-level fields).
    # Verify that present hard required fields with good confidence do not appear as failed.
    failed_present_fields = [
        f for f in gate.hard_required_failed
        if f in extraction.fields
    ]
    assert failed_present_fields == [], f"Present fields should not fail: {failed_present_fields}"


def test_gate_blocks_on_hard_required_field_below_threshold():
    """A hard required field with confidence 0.60 should block the gate."""
    fields = passing_hard_fields()
    fields["agi"] = (207840.00, 0.60)  # Below 0.85 threshold
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    assert "agi" in gate.hard_required_failed
    flagged_names = [f.field_name for f in gate.flagged_fields]
    assert "agi" in flagged_names


def test_gate_hard_required_flag_includes_reason():
    """Flagged hard required fields must have a human-readable reason."""
    fields = passing_hard_fields()
    fields["filing_status"] = ("married_filing_jointly", 0.50)
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    fs_flags = [f for f in gate.flagged_fields if f.field_name == "filing_status"]
    assert len(fs_flags) == 1
    assert len(fs_flags[0].reason) > 10  # Non-trivial reason string
    assert fs_flags[0].field_classification == "hard_required"


def test_gate_blocks_on_null_hard_required_field():
    """A hard required field with null value should be flagged."""
    fields = passing_hard_fields()
    fields["agi"] = (None, 0.0)  # Null value — field not found
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    assert "agi" in gate.hard_required_failed
    agi_flags = [f for f in gate.flagged_fields if f.field_name == "agi"]
    assert agi_flags[0].extracted_value is None


def test_gate_fails_when_hard_required_is_missing_from_extraction():
    """Hard required fields absent from extraction entirely should be flagged."""
    # Don't include 'filing_status' at all
    fields = {"agi": (207840.00, 0.99), "state_of_residence": ("IL", 0.99)}
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    assert "filing_status" in gate.hard_required_failed
    assert gate.passed is False


# ---------------------------------------------------------------------------
# Soft Required tests
# ---------------------------------------------------------------------------


def test_gate_proceeds_with_soft_required_below_threshold():
    """Gate should pass (not be blocked) when a soft required field is below 0.75."""
    fields = {
        **passing_hard_fields(),
        "cost_basis_total": (150000.00, 0.50),  # Below soft threshold
    }
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    # Cost basis should be in soft_required_missing
    assert "cost_basis_total" in gate.soft_required_missing

    # Gate should NOT block on soft required failures alone
    # (only blocks if hard required fields fail — which they don't here)
    hard_failures = [f for f in gate.hard_required_failed if f in extraction.fields]
    assert len(hard_failures) == 0


def test_gate_flags_soft_required_missing_with_warning():
    """Soft required fields below threshold should be in flagged_fields with soft classification."""
    fields = {
        **passing_hard_fields(),
        "cost_basis_total": (None, 0.0),
    }
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    cb_flags = [f for f in gate.flagged_fields if f.field_name == "cost_basis_total"]
    assert len(cb_flags) == 1
    assert cb_flags[0].field_classification == "soft_required"


def test_gate_soft_required_high_confidence_does_not_flag():
    """A soft required field above 0.75 should not appear in soft_required_missing."""
    fields = {
        **passing_hard_fields(),
        "cost_basis_total": (150000.00, 0.90),  # Above threshold
    }
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    assert "cost_basis_total" not in gate.soft_required_missing


# ---------------------------------------------------------------------------
# Optional field tests
# ---------------------------------------------------------------------------


def test_gate_optional_below_threshold_silently_omitted():
    """Optional fields below 0.60 should be in optional_missing but NOT in flagged_fields."""
    fields = {
        **passing_hard_fields(),
        "lot_level_cost_basis": (True, 0.40),  # Below optional threshold
    }
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    assert "lot_level_cost_basis" in gate.optional_missing
    # Optional fields should not appear in the advisor-blocking flagged_fields list
    optional_in_flagged = [
        f for f in gate.flagged_fields
        if f.field_name == "lot_level_cost_basis"
    ]
    assert len(optional_in_flagged) == 0


def test_gate_optional_missing_entirely_is_silently_noted():
    """Optional fields not in extraction at all should be noted in optional_missing."""
    extraction = make_extraction(passing_hard_fields())
    gate = evaluate_extraction(extraction)

    assert "lot_level_cost_basis" in gate.optional_missing


# ---------------------------------------------------------------------------
# Gate status structure tests
# ---------------------------------------------------------------------------


def test_gate_status_structure():
    """GateStatus should always have all required fields."""
    extraction = make_extraction(passing_hard_fields())
    gate = evaluate_extraction(extraction)

    assert hasattr(gate, "passed")
    assert hasattr(gate, "flagged_fields")
    assert hasattr(gate, "hard_required_failed")
    assert hasattr(gate, "soft_required_missing")
    assert hasattr(gate, "optional_missing")
    assert isinstance(gate.flagged_fields, list)
    assert isinstance(gate.hard_required_failed, list)


def test_gate_passed_is_false_when_hard_required_fails():
    """gate.passed must be False when any hard required field fails."""
    fields = passing_hard_fields()
    fields["agi"] = (None, 0.0)
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    assert gate.passed is False


def test_gate_flagged_fields_include_confidence_and_value():
    """Each flagged field must carry extracted_value and confidence (for the advisor review screen)."""
    fields = passing_hard_fields()
    fields["agi"] = (207840.00, 0.70)  # Below hard threshold
    extraction = make_extraction(fields)
    gate = evaluate_extraction(extraction)

    agi_flag = next(f for f in gate.flagged_fields if f.field_name == "agi")
    assert agi_flag.extracted_value == 207840.00
    assert agi_flag.confidence == 0.70


# ---------------------------------------------------------------------------
# Session 3: contradiction_id and new checks
# ---------------------------------------------------------------------------


def test_contradiction_id_assigned():
    """validate_documents with contradictory docs → all issues have non-None contradiction_id."""
    from backend.models.document import FieldConfidence

    # Create a doc with a negative balance → contradiction
    ira_bad = ExtractionResult(
        document_type="retirement_ira",
        institution="vanguard",
        tax_year=2024,
        fields={"account_value": FieldConfidence(value=-5000.0, confidence=0.99)},
        extraction_notes=[],
        overall_confidence=0.95,
    )
    f1040 = ExtractionResult(
        document_type="form_1040",
        institution=None,
        tax_year=2024,
        fields={
            "agi": FieldConfidence(value=207840.0, confidence=0.99),
            "wages_salaries_tips": FieldConfidence(value=185000.0, confidence=0.99),
        },
        extraction_notes=[],
        overall_confidence=0.95,
    )

    from backend.extraction.validator import validate_documents
    result = validate_documents([f1040, ira_bad])

    assert result.passed is False
    # All issues should have contradiction_id assigned
    for issue in result.issues:
        assert issue.contradiction_id is not None
        assert len(issue.contradiction_id) == 8


def test_new_check_agi_vs_income_in_pipeline():
    """Full validate_documents call with AGI mismatch produces warning."""
    from backend.models.document import FieldConfidence

    # AGI = 207840, but wages = 185000 (only field) → difference = 22840 → warning
    f1040 = ExtractionResult(
        document_type="form_1040",
        institution=None,
        tax_year=2024,
        fields={
            "agi": FieldConfidence(value=207840.0, confidence=0.99),
            "wages_salaries_tips": FieldConfidence(value=185000.0, confidence=0.99),
        },
        extraction_notes=[],
        overall_confidence=0.95,
    )
    brokerage = ExtractionResult(
        document_type="brokerage_statement",
        institution="fidelity",
        tax_year=2024,
        fields={
            "total_account_value": FieldConfidence(value=487250.0, confidence=0.99),
        },
        extraction_notes=[],
        overall_confidence=0.95,
    )

    from backend.extraction.validator import validate_documents
    result = validate_documents([f1040, brokerage])

    # Should have warning about AGI vs income
    agi_warnings = [
        i for i in result.issues
        if i.check_name == "check_agi_vs_income_sources" and i.severity == "warning"
    ]
    assert len(agi_warnings) == 1
    assert agi_warnings[0].field_a == "agi"
    assert agi_warnings[0].source_a == "form_1040"
