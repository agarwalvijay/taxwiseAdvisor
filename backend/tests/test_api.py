"""
Tests for Session 3 API-level logic (no DB required — Pydantic model validation only,
plus gate logic using mocks).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import ValidationError

from backend.models.document import ExtractionResult, FieldConfidence
from backend.models.snapshot import (
    ClientGateStatus,
    IncomeProjectionInput,
    IncomeProjectionsRequest,
)
from backend.extraction.validator import (
    check_agi_vs_income_sources,
    check_ira_contribution_plausibility,
    validate_documents,
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


def make_1040_with_income(
    agi=207840.0,
    wages=185000.0,
    adjustments=0.0,
) -> ExtractionResult:
    fields = {
        "agi": (agi, 0.99),
        "wages_salaries_tips": (wages, 0.99),
    }
    if adjustments:
        fields["adjustments_to_income"] = (adjustments, 0.99)
    return make_extraction("form_1040", tax_year=2024, fields=fields)


# ---------------------------------------------------------------------------
# IncomeProjectionsRequest validation tests
# ---------------------------------------------------------------------------


def test_income_projection_requires_3_years():
    """Less than 3 years → ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        IncomeProjectionsRequest(
            projections=[
                IncomeProjectionInput(year=2025, estimated_income=100000),
                IncomeProjectionInput(year=2026, estimated_income=105000),
            ]
        )
    assert "3 years" in str(exc_info.value).lower() or "3" in str(exc_info.value)


def test_income_projection_non_sequential_years():
    """Non-sequential years (gap) → ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        IncomeProjectionsRequest(
            projections=[
                IncomeProjectionInput(year=2024, estimated_income=100000),
                IncomeProjectionInput(year=2026, estimated_income=105000),  # Gap: missing 2025
                IncomeProjectionInput(year=2027, estimated_income=110000),
            ]
        )
    assert "gap" in str(exc_info.value).lower() or "sequential" in str(exc_info.value).lower()


def test_income_projection_negative_income():
    """Negative estimated_income → ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        IncomeProjectionsRequest(
            projections=[
                IncomeProjectionInput(year=2025, estimated_income=-1000),
                IncomeProjectionInput(year=2026, estimated_income=105000),
                IncomeProjectionInput(year=2027, estimated_income=110000),
            ]
        )
    assert "0" in str(exc_info.value) or ">= 0" in str(exc_info.value) or "negative" in str(exc_info.value).lower()


def test_income_projection_valid():
    """3 sequential years, all positive → passes validation."""
    req = IncomeProjectionsRequest(
        projections=[
            IncomeProjectionInput(year=2025, estimated_income=100000),
            IncomeProjectionInput(year=2026, estimated_income=105000),
            IncomeProjectionInput(year=2027, estimated_income=110000),
        ]
    )
    assert len(req.projections) == 3


def test_income_projection_ss_age_out_of_range():
    """social_security_start_age=71 → ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        IncomeProjectionsRequest(
            projections=[
                IncomeProjectionInput(year=2025, estimated_income=100000),
                IncomeProjectionInput(year=2026, estimated_income=105000),
                IncomeProjectionInput(year=2027, estimated_income=110000),
            ],
            social_security_start_age=71,
        )
    assert "62" in str(exc_info.value) or "70" in str(exc_info.value)


def test_income_projection_ss_age_valid():
    """social_security_start_age=70 → passes."""
    req = IncomeProjectionsRequest(
        projections=[
            IncomeProjectionInput(year=2025, estimated_income=100000),
            IncomeProjectionInput(year=2026, estimated_income=105000),
            IncomeProjectionInput(year=2027, estimated_income=110000),
        ],
        social_security_start_age=70,
    )
    assert req.social_security_start_age == 70


# ---------------------------------------------------------------------------
# ClientGateStatus model tests
# ---------------------------------------------------------------------------


def test_gate_status_defaults_action_required():
    """Default ClientGateStatus should have overall_status='action_required'."""
    gs = ClientGateStatus()
    assert gs.overall_status == "action_required"
    assert gs.classification_gate == "not_started"
    assert gs.extraction_gate == "not_started"
    assert gs.validation_gate == "not_started"
    assert gs.snapshot_gate == "not_started"
    assert gs.income_table_gate == "not_started"


def test_gate_status_all_passed_is_ready():
    """When all gates are 'passed', overall_status can be set to 'ready_for_plan'."""
    gs = ClientGateStatus(
        classification_gate="passed",
        extraction_gate="passed",
        validation_gate="passed",
        snapshot_gate="passed",
        income_table_gate="passed",
        overall_status="ready_for_plan",
    )
    assert gs.overall_status == "ready_for_plan"


# ---------------------------------------------------------------------------
# New validator check tests
# ---------------------------------------------------------------------------


def test_check_agi_vs_income_sources_passes_when_close():
    """Computed income within $1000 of AGI → no warning."""
    # AGI = wages + taxable_interest = 185000 + 1240 = 186240; "agi" = 186300 (diff = 60)
    form_1040 = make_extraction(
        "form_1040",
        tax_year=2024,
        fields={
            "agi": (186300.0, 0.99),
            "wages_salaries_tips": (185000.0, 0.99),
            "taxable_interest": (1240.0, 0.99),
        },
    )
    issues = check_agi_vs_income_sources([form_1040])
    assert len(issues) == 0


def test_check_agi_vs_income_sources_warns_when_divergent():
    """$5000+ gap between AGI and computed income → warning."""
    # AGI = 207840, wages = 185000 (only component) → computed = 185000, diff = 22840
    form_1040 = make_extraction(
        "form_1040",
        tax_year=2024,
        fields={
            "agi": (207840.0, 0.99),
            "wages_salaries_tips": (185000.0, 0.99),
        },
    )
    issues = check_agi_vs_income_sources([form_1040])
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].field_a == "agi"
    assert issues[0].source_a == "form_1040"
    assert issues[0].value_a == 207840.0


def test_check_agi_skipped_without_1040():
    """Without form_1040, the check should produce no issues."""
    brokerage = make_extraction(
        "brokerage_statement",
        fields={"ytd_dividends": (9100.0, 0.99)},
    )
    issues = check_agi_vs_income_sources([brokerage])
    assert len(issues) == 0


def test_check_ira_contribution_plausibility_warns_when_deduction_missing():
    """IRA contrib present + no adjustment on 1040 → warning."""
    ira = make_extraction(
        "retirement_ira",
        fields={"ytd_employee_contributions": (6500.0, 0.99)},
    )
    form_1040 = make_extraction(
        "form_1040",
        fields={
            "agi": (207840.0, 0.99),
            "wages_salaries_tips": (207840.0, 0.99),
        },
    )
    issues = check_ira_contribution_plausibility([form_1040, ira])
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].field_a == "ytd_employee_contributions"
    assert issues[0].source_a == "retirement_ira"
    assert issues[0].value_a == 6500.0
    assert issues[0].field_b == "adjustments_to_income"
    assert issues[0].source_b == "form_1040"


def test_check_ira_contribution_plausibility_passes_when_adjustment_present():
    """IRA contrib present + matching adjustment on 1040 → no warning."""
    ira = make_extraction(
        "retirement_ira",
        fields={"ytd_employee_contributions": (6500.0, 0.99)},
    )
    form_1040 = make_extraction(
        "form_1040",
        fields={
            "agi": (201340.0, 0.99),
            "wages_salaries_tips": (207840.0, 0.99),
            "adjustments_to_income": (6500.0, 0.99),  # IRA deduction taken
        },
    )
    issues = check_ira_contribution_plausibility([form_1040, ira])
    assert len(issues) == 0


# ---------------------------------------------------------------------------
# can_generate_plan logic tests (mock DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_can_generate_plan_false_no_documents():
    """No documents → allowed=False."""
    from backend.gates.confidence_gate import can_generate_plan

    # Mock db to return empty scalars for documents
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await can_generate_plan("00000000-0000-0000-0000-000000000001", mock_db)
    assert result["allowed"] is False
    assert result["reason"] is not None
    assert "no documents" in result["reason"].lower()


@pytest.mark.asyncio
async def test_can_generate_plan_false_no_snapshot():
    """Docs exist but no snapshot → allowed=False."""
    from backend.gates.confidence_gate import can_generate_plan
    from backend.models.document import Document

    # Create a minimal mock document
    mock_doc = MagicMock()
    mock_doc.classification_status = "classified"
    mock_doc.raw_extraction = {"document_type": "form_1040", "fields": {}, "extraction_notes": [], "overall_confidence": 0.9}

    # First call returns documents, second returns no snapshot
    docs_result = MagicMock()
    docs_result.scalars.return_value.all.return_value = [mock_doc]

    snap_result = MagicMock()
    snap_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[docs_result, snap_result])

    result = await can_generate_plan("00000000-0000-0000-0000-000000000001", mock_db)
    assert result["allowed"] is False
    assert "snapshot" in result["reason"].lower()
