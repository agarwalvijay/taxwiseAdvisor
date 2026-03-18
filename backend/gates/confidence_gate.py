"""
Confidence gate logic.

Evaluates an ExtractionResult against configured thresholds and returns a GateStatus
describing which fields passed, which require advisor review, and which are missing.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.document import Document, ExtractionResult
from backend.models.snapshot import ClientFinancialSnapshotORM, ClientGateStatus, GateStatus, FlaggedField

# Fields extracted directly from documents, checked per document type.
# NOTE: client_age and retirement_target_age are advisor-provided (never in any financial doc).
#       total_*_balance fields are assembled post-extraction and checked by the snapshot gate.
#       Only these three are truly extractable from a form_1040.
HARD_REQUIRED_FIELDS = {
    "filing_status",
    "agi",
    "state_of_residence",
}

# Which hard-required extraction fields apply per document type.
# Documents that don't contain a field should never be flagged for its absence.
HARD_REQUIRED_BY_DOC_TYPE: dict[str, set[str]] = {
    "form_1040": {"filing_status", "agi", "state_of_residence"},
    "brokerage_statement": set(),   # balances assembled at snapshot level
    "retirement_401k": set(),       # balances assembled at snapshot level
    "retirement_ira": set(),        # balances assembled at snapshot level
    "w2": set(),
    "ssa_estimate": set(),
    "unknown": set(),
}

# Fields that are entered by the advisor (not extracted from any document).
# Collected via Client Profile form and stored in advisor_confirmations.
ADVISOR_INPUT_FIELDS = {"client_age", "retirement_target_age"}

# Fields where plan generates but affected section is flagged incomplete
SOFT_REQUIRED_FIELDS = {
    "cost_basis_total",
    "ssa_benefit_estimate",
    "income_projections_beyond_current_year",
    "hsa_balance",
}

# Fields that enrich the plan; absence is noted in provenance only
OPTIONAL_FIELDS = {
    "lot_level_cost_basis",
    "pension_defined_benefit",
    "prior_year_tax_return",
    "rmd_worksheet",
}

# Human-readable labels for display on the advisor review screen
FIELD_LABELS = {
    "filing_status": "Filing Status",
    "client_age": "Client Age",
    "agi": "Adjusted Gross Income (AGI)",
    "retirement_target_age": "Retirement Target Age",
    "total_pretax_retirement_balance": "Total Pre-Tax Retirement Balance",
    "total_roth_balance": "Total Roth Balance",
    "total_taxable_brokerage_balance": "Total Taxable Brokerage Balance",
    "state_of_residence": "State of Residence",
    "cost_basis_total": "Total Cost Basis (Taxable Holdings)",
    "ssa_benefit_estimate": "Social Security Benefit Estimate",
    "income_projections_beyond_current_year": "Income Projections (Future Years)",
    "hsa_balance": "HSA Balance",
    "lot_level_cost_basis": "Lot-Level Cost Basis",
    "pension_defined_benefit": "Pension / Defined Benefit Details",
    "prior_year_tax_return": "Prior Year Tax Return",
    "rmd_worksheet": "RMD Worksheet",
}


def evaluate_extraction(extraction_result: ExtractionResult) -> GateStatus:
    """
    Evaluate an ExtractionResult against the confidence gate thresholds.

    Returns GateStatus with:
    - passed: True only if ALL hard-required fields are >= HARD threshold
    - flagged_fields: detailed list with reason per flagged field
    - hard_required_failed: field names that block the plan
    - soft_required_missing: field names that proceed with warning
    - optional_missing: field names silently noted
    """
    hard_threshold = settings.confidence_threshold_hard_required
    soft_threshold = settings.confidence_threshold_soft_required
    optional_threshold = settings.confidence_threshold_optional

    flagged: list[FlaggedField] = []
    hard_required_failed: list[str] = []
    soft_required_missing: list[str] = []
    optional_missing: list[str] = []

    doc_type = extraction_result.document_type
    fields_to_check = HARD_REQUIRED_BY_DOC_TYPE.get(doc_type, set())

    for field_name in fields_to_check:
        field_data = extraction_result.fields.get(field_name)

        if field_data is None or field_data.value is None:
            reason = (
                f"{FIELD_LABELS.get(field_name, field_name)} was not found in the document. "
                "This field is required to generate the tax plan. "
                "Please enter the correct value or confirm it is not available."
            )
            flagged.append(
                FlaggedField(
                    field_name=field_name,
                    extracted_value=None,
                    confidence=None,
                    reason=reason,
                    field_classification="hard_required",
                )
            )
            hard_required_failed.append(field_name)

        elif field_data.confidence < hard_threshold:
            reason = _build_low_confidence_reason(
                field_name, field_data.confidence, hard_threshold, field_data.note
            )
            flagged.append(
                FlaggedField(
                    field_name=field_name,
                    extracted_value=field_data.value,
                    confidence=field_data.confidence,
                    reason=reason,
                    field_classification="hard_required",
                )
            )
            hard_required_failed.append(field_name)

    for field_name in SOFT_REQUIRED_FIELDS:
        field_data = extraction_result.fields.get(field_name)

        if field_data is None or field_data.value is None:
            # Simply absent — record for provenance but don't add to flagged list.
            # Advisor has nothing to review; the plan section will just be marked incomplete.
            soft_required_missing.append(field_name)

        elif field_data.confidence < soft_threshold:
            reason = _build_low_confidence_reason(
                field_name, field_data.confidence, soft_threshold, field_data.note
            )
            flagged.append(
                FlaggedField(
                    field_name=field_name,
                    extracted_value=field_data.value,
                    confidence=field_data.confidence,
                    reason=reason,
                    field_classification="soft_required",
                )
            )
            soft_required_missing.append(field_name)

    for field_name in OPTIONAL_FIELDS:
        field_data = extraction_result.fields.get(field_name)

        if field_data is not None and field_data.value is not None:
            if field_data.confidence < optional_threshold:
                optional_missing.append(field_name)
        else:
            optional_missing.append(field_name)

    passed = len(hard_required_failed) == 0

    return GateStatus(
        passed=passed,
        flagged_fields=flagged,
        hard_required_failed=hard_required_failed,
        soft_required_missing=soft_required_missing,
        optional_missing=optional_missing,
    )


def _build_low_confidence_reason(
    field_name: str,
    confidence: float,
    threshold: float,
    extraction_note: str | None,
) -> str:
    label = FIELD_LABELS.get(field_name, field_name)
    base = (
        f"{label} was extracted with {confidence:.0%} confidence, "
        f"below the required {threshold:.0%} threshold. "
    )
    if extraction_note:
        base += f"Extraction note: {extraction_note}. "
    base += "Please confirm the extracted value is correct or enter the correct value."
    return base


async def can_generate_plan(client_id: str, db: AsyncSession) -> dict:
    """
    Check all 5 gates in sequence.
    Returns {"allowed": bool, "reason": str | None}
    """
    import uuid as _uuid

    try:
        client_uuid = _uuid.UUID(client_id)
    except ValueError:
        return {"allowed": False, "reason": "Invalid client_id format."}

    # Gate 1: Load all documents for client
    result = await db.execute(
        select(Document).where(Document.client_id == client_uuid)
    )
    documents = result.scalars().all()

    if not documents:
        return {"allowed": False, "reason": "No documents have been uploaded for this client."}

    # Check for rejected or unextracted documents
    for doc in documents:
        if doc.classification_status == "rejected":
            return {
                "allowed": False,
                "reason": "One or more documents failed classification or have not been extracted. Review the document list.",
            }
        if doc.classification_status != "rejected" and doc.raw_extraction is None:
            # Check if classified (extraction expected) but not yet extracted
            if doc.classification_status == "classified":
                return {
                    "allowed": False,
                    "reason": "One or more documents failed classification or have not been extracted. Review the document list.",
                }

    # Gate 2: Load snapshot record
    snapshot_result = await db.execute(
        select(ClientFinancialSnapshotORM)
        .where(ClientFinancialSnapshotORM.client_id == client_uuid)
        .order_by(ClientFinancialSnapshotORM.version.desc())
    )
    snapshot = snapshot_result.scalar_one_or_none()

    if snapshot is None:
        return {
            "allowed": False,
            "reason": "Snapshot has not been assembled yet. Complete document review and run snapshot assembly.",
        }

    # Load gate_status
    gate_status_raw = snapshot.gate_status or {}

    # Gate 3: Extraction gate
    extraction_gate = gate_status_raw.get("extraction_gate", "not_started")
    if extraction_gate != "passed":
        return {
            "allowed": False,
            "reason": gate_status_raw.get(
                "blocking_reason",
                "Extraction review is incomplete. Confirm all flagged fields before generating a plan.",
            ),
        }

    # Gate 4: Validation gate
    # "not_started" is acceptable when there's only 1 document (no cross-doc check possible)
    validation_gate = gate_status_raw.get("validation_gate", "not_started")
    classified_docs = [d for d in documents if d.classification_status != "rejected"]
    if validation_gate == "contradictions_pending":
        return {
            "allowed": False,
            "reason": "Cross-document contradictions must be resolved before generating a plan.",
        }
    if validation_gate == "not_started" and len(classified_docs) >= 2:
        return {
            "allowed": False,
            "reason": "Cross-document contradictions must be resolved before generating a plan.",
        }

    # Gate 5: Snapshot gate
    snapshot_gate = gate_status_raw.get("snapshot_gate", "not_started")
    if snapshot_gate != "passed":
        return {
            "allowed": False,
            "reason": "Snapshot assembly failed or is missing required fields. Check gate status for details.",
        }

    # Gate 6: Income table gate
    snapshot_data = snapshot.snapshot_data or {}
    income_data = snapshot_data.get("income", {})
    projections = income_data.get("projections", [])
    if len(projections) < 3:
        return {
            "allowed": False,
            "reason": "Income projections are required (minimum 3 years). Please complete the income projection table.",
        }

    return {"allowed": True, "reason": None}
