"""
Snapshot API routes.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from backend.database import get_db
from backend.extraction.snapshot_assembler import SnapshotAssemblyError, assemble_snapshot
from backend.extraction.validator import validate_documents
from backend.gates.confidence_gate import evaluate_extraction, HARD_REQUIRED_FIELDS
from backend.models.document import Document, ExtractionResult
from backend.models.snapshot import (
    ClientFinancialSnapshotORM,
    ClientGateStatus,
    ConfirmFieldRequest,
    ContradictionRecord,
    FlaggedField,
    IncomeInfo,
    IncomeProjection,
    IncomeProjectionsRequest,
    ResolveContradictionRequest,
    SocialSecurityInfo,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: Compute gate status from documents + snapshot ORM
# ---------------------------------------------------------------------------


def _compute_gate_status(
    documents: list,  # Document ORM objects
    snapshot_orm: Optional[ClientFinancialSnapshotORM],
) -> dict:
    """
    Compute the full ClientGateStatus dict from documents and snapshot.
    """
    gate_status_db = (snapshot_orm.gate_status or {}) if snapshot_orm else {}
    advisor_confirmations = gate_status_db.get("advisor_confirmations", {})

    # ----- Classification Gate -----
    if not documents:
        classification_gate = "not_started"
    elif any(d.classification_status == "rejected" for d in documents):
        classification_gate = "failed"
    else:
        classification_gate = "passed"

    # ----- Extraction Gate -----
    # Evaluate each extraction, collect flagged fields — deduplicate by field_name
    _seen_fields: set[str] = set()
    all_flagged_fields: list[FlaggedField] = []
    for doc in documents:
        if doc.raw_extraction:
            try:
                extraction = ExtractionResult(**doc.raw_extraction)
                gate = evaluate_extraction(extraction)
                for f in gate.flagged_fields:
                    if f.field_name not in _seen_fields:
                        _seen_fields.add(f.field_name)
                        all_flagged_fields.append(f)
            except Exception:
                pass

    # Filter to hard required unresolved flags
    unresolved_hard_flags = [
        f for f in all_flagged_fields
        if f.field_classification == "hard_required"
        and f.field_name not in advisor_confirmations
    ]

    if not documents:
        extraction_gate = "not_started"
    elif unresolved_hard_flags:
        extraction_gate = "review_required"
    else:
        extraction_gate = "passed"

    # ----- Validation Gate -----
    # Load validation issues from gate_status JSONB or recompute
    stored_contradictions = gate_status_db.get("contradictions", [])
    # If we have them stored, use them; otherwise try to compute
    if stored_contradictions:
        contradictions = stored_contradictions
    else:
        # Try to compute from extractions
        extractions: list[ExtractionResult] = []
        for doc in documents:
            if doc.raw_extraction:
                try:
                    extractions.append(ExtractionResult(**doc.raw_extraction))
                except Exception:
                    pass
        if len(extractions) >= 2:
            val_result = validate_documents(extractions)
            contradictions = [
                {
                    "contradiction_id": issue.contradiction_id or "",
                    "check_name": issue.check_name,
                    "severity": issue.severity,
                    "description": issue.description,
                    "field_a": issue.field_a,
                    "source_a": issue.source_a,
                    "value_a": issue.value_a,
                    "field_b": issue.field_b,
                    "source_b": issue.source_b,
                    "value_b": issue.value_b,
                    "suggested_resolution": issue.suggested_resolution,
                    "resolved": False,
                }
                for issue in val_result.issues
                if issue.severity == "contradiction"
            ]
        else:
            contradictions = []

    unresolved_count = sum(
        1 for c in contradictions
        if isinstance(c, dict) and not c.get("resolved", False)
        and c.get("severity") == "contradiction"
    )

    if not documents or len([d for d in documents if d.raw_extraction]) < 2:
        validation_gate = "not_started"
    elif unresolved_count > 0:
        validation_gate = "contradictions_pending"
    else:
        validation_gate = "passed"

    # ----- Snapshot Gate -----
    if snapshot_orm is None or not snapshot_orm.snapshot_data:
        snapshot_gate = "not_started"
    else:
        # Check required fields in snapshot_data
        sd = snapshot_orm.snapshot_data
        personal = sd.get("personal", {})
        income = sd.get("income", {})
        missing_snap_fields = []
        if not personal.get("filing_status"):
            missing_snap_fields.append("personal.filing_status")
        if personal.get("age") is None:
            missing_snap_fields.append("personal.age")
        if not personal.get("state"):
            missing_snap_fields.append("personal.state")
        if income.get("current_year_agi") is None:
            missing_snap_fields.append("income.current_year_agi")
        if personal.get("retirement_target_age") is None:
            missing_snap_fields.append("personal.retirement_target_age")

        if missing_snap_fields:
            snapshot_gate = "missing_required_fields"
        else:
            snapshot_gate = gate_status_db.get("snapshot_gate", "passed")

    missing_fields: list[str] = []
    if snapshot_orm and snapshot_orm.snapshot_data:
        sd = snapshot_orm.snapshot_data
        personal = sd.get("personal", {})
        income = sd.get("income", {})
        if not personal.get("filing_status"):
            missing_fields.append("personal.filing_status")
        if personal.get("age") is None:
            missing_fields.append("personal.age")
        if not personal.get("state"):
            missing_fields.append("personal.state")
        if income.get("current_year_agi") is None:
            missing_fields.append("income.current_year_agi")
        if personal.get("retirement_target_age") is None:
            missing_fields.append("personal.retirement_target_age")

    # ----- Income Table Gate -----
    if snapshot_orm and snapshot_orm.snapshot_data:
        income_data = snapshot_orm.snapshot_data.get("income", {})
        projections = income_data.get("projections", [])
        income_table_gate = "passed" if len(projections) >= 3 else "not_started"
    else:
        income_table_gate = gate_status_db.get("income_table_gate", "not_started")

    # ----- Blocking Reason -----
    blocking_reason = None
    if classification_gate == "failed":
        blocking_reason = "One or more documents failed classification. Review the document list."
    elif extraction_gate == "review_required":
        blocking_reason = "Some fields require advisor review before proceeding."
    elif validation_gate == "contradictions_pending":
        blocking_reason = "Cross-document contradictions must be resolved before assembly."
    elif snapshot_gate in ("not_started", "missing_required_fields"):
        if snapshot_gate == "not_started":
            blocking_reason = "Snapshot has not been assembled yet."
        else:
            blocking_reason = "Snapshot is missing required fields."
    elif income_table_gate != "passed":
        blocking_reason = "Income projections are required (minimum 3 years)."

    # ----- Overall Status -----
    all_gates_passed = all([
        classification_gate == "passed",
        extraction_gate == "passed",
        validation_gate == "passed",
        snapshot_gate == "passed",
        income_table_gate == "passed",
    ])
    overall_status = "ready_for_plan" if all_gates_passed else "action_required"

    # Build ContradictionRecord list
    contradiction_records: list[dict] = []
    for c in contradictions:
        if isinstance(c, dict):
            contradiction_records.append(c)

    return {
        "classification_gate": classification_gate,
        "extraction_gate": extraction_gate,
        "validation_gate": validation_gate,
        "snapshot_gate": snapshot_gate,
        "income_table_gate": income_table_gate,
        "overall_status": overall_status,
        "flagged_fields": [
            f.model_dump() for f in all_flagged_fields
            if f.field_name not in advisor_confirmations
        ],
        "contradictions": contradiction_records,
        "missing_fields": missing_fields,
        "blocking_reason": blocking_reason,
        "advisor_confirmations": advisor_confirmations,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{client_id}/gate-status")
async def get_gate_status(
    client_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the full gate status for a client."""
    try:
        client_uuid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client_id format.")

    # Load documents
    doc_result = await db.execute(
        select(Document).where(Document.client_id == client_uuid)
    )
    documents = doc_result.scalars().all()

    # Load snapshot
    snap_result = await db.execute(
        select(ClientFinancialSnapshotORM)
        .where(ClientFinancialSnapshotORM.client_id == client_uuid)
        .order_by(ClientFinancialSnapshotORM.version.desc())
    )
    snapshot_orm = snap_result.scalar_one_or_none()

    return _compute_gate_status(documents, snapshot_orm)


@router.get("/{client_id}")
async def get_snapshot(
    client_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent snapshot for a client."""
    try:
        client_uuid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client_id format.")

    snap_result = await db.execute(
        select(ClientFinancialSnapshotORM)
        .where(ClientFinancialSnapshotORM.client_id == client_uuid)
        .order_by(ClientFinancialSnapshotORM.version.desc())
    )
    snapshot_orm = snap_result.scalar_one_or_none()

    if snapshot_orm is None:
        raise HTTPException(status_code=404, detail="Snapshot not found for this client.")

    return {
        "snapshot_id": str(snapshot_orm.id),
        "client_id": str(snapshot_orm.client_id),
        "version": snapshot_orm.version,
        "snapshot_data": snapshot_orm.snapshot_data,
        "gate_status": snapshot_orm.gate_status,
        "data_provenance": snapshot_orm.data_provenance,
        "created_at": snapshot_orm.created_at.isoformat() if snapshot_orm.created_at else None,
    }


@router.post("/{client_id}/assemble")
async def assemble_client_snapshot(
    client_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Assemble a financial snapshot from extracted documents."""
    try:
        uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client_id format.")

    try:
        snapshot_orm = await assemble_snapshot(client_id, db)
    except SnapshotAssemblyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "assembly_failed",
                "missing_fields": exc.missing_fields,
                "message": str(exc),
            },
        )

    # Update snapshot gate
    gate_status = dict(snapshot_orm.gate_status or {})
    gate_status["snapshot_gate"] = "passed"
    snapshot_orm.gate_status = gate_status
    flag_modified(snapshot_orm, "gate_status")
    await db.commit()
    await db.refresh(snapshot_orm)

    return {
        "snapshot_id": str(snapshot_orm.id),
        "client_id": str(snapshot_orm.client_id),
        "version": snapshot_orm.version,
        "snapshot_data": snapshot_orm.snapshot_data,
        "gate_status": snapshot_orm.gate_status,
        "data_provenance": snapshot_orm.data_provenance,
    }


@router.post("/{client_id}/confirm-field")
async def confirm_field(
    client_id: str,
    body: ConfirmFieldRequest,
    db: AsyncSession = Depends(get_db),
):
    """Confirm (or override) a field value for the client snapshot."""
    try:
        client_uuid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client_id format.")

    # Get or create snapshot record
    snap_result = await db.execute(
        select(ClientFinancialSnapshotORM)
        .where(ClientFinancialSnapshotORM.client_id == client_uuid)
        .order_by(ClientFinancialSnapshotORM.version.desc())
    )
    snapshot_orm = snap_result.scalar_one_or_none()

    if snapshot_orm is None:
        # Create empty snapshot record to store gate_status
        snapshot_orm = ClientFinancialSnapshotORM(
            client_id=client_uuid,
            snapshot_data={},
            gate_status={},
            data_provenance={},
            version=1,
        )
        db.add(snapshot_orm)
        await db.flush()

    gate_status = dict(snapshot_orm.gate_status or {})
    advisor_confirmations = dict(gate_status.get("advisor_confirmations", {}))

    # Add confirmation
    advisor_confirmations[body.field_path] = {
        "field_path": body.field_path,
        "confirmed_value": body.confirmed_value,
        "original_extracted": body.original_extracted,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
    gate_status["advisor_confirmations"] = advisor_confirmations

    # Recompute extraction_gate
    # Load documents for this client
    doc_result = await db.execute(
        select(Document).where(Document.client_id == client_uuid)
    )
    documents = doc_result.scalars().all()

    # Get all hard-required flagged fields from all extractions
    unresolved_hard_flags = []
    for doc in documents:
        if doc.raw_extraction:
            try:
                extraction = ExtractionResult(**doc.raw_extraction)
                gate = evaluate_extraction(extraction)
                for f in gate.flagged_fields:
                    if f.field_classification == "hard_required" and f.field_name not in advisor_confirmations:
                        unresolved_hard_flags.append(f.field_name)
            except Exception:
                pass

    gate_status["extraction_gate"] = "review_required" if unresolved_hard_flags else "passed"
    snapshot_orm.gate_status = gate_status
    flag_modified(snapshot_orm, "gate_status")
    await db.commit()
    await db.refresh(snapshot_orm)

    return _compute_gate_status(documents, snapshot_orm)


@router.post("/{client_id}/resolve-contradiction")
async def resolve_contradiction(
    client_id: str,
    body: ResolveContradictionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resolve a cross-document contradiction."""
    try:
        client_uuid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client_id format.")

    snap_result = await db.execute(
        select(ClientFinancialSnapshotORM)
        .where(ClientFinancialSnapshotORM.client_id == client_uuid)
        .order_by(ClientFinancialSnapshotORM.version.desc())
    )
    snapshot_orm = snap_result.scalar_one_or_none()

    if snapshot_orm is None:
        raise HTTPException(status_code=404, detail="Snapshot not found for this client.")

    gate_status = dict(snapshot_orm.gate_status or {})
    contradictions = list(gate_status.get("contradictions", []))

    # Find and mark the contradiction as resolved
    found = False
    for contradiction in contradictions:
        if isinstance(contradiction, dict) and contradiction.get("contradiction_id") == body.contradiction_id:
            contradiction["resolved"] = True
            contradiction["resolved_value"] = body.resolved_value
            contradiction["resolution_note"] = body.resolution
            contradiction["resolved_at"] = datetime.now(timezone.utc).isoformat()
            found = True
            break

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Contradiction '{body.contradiction_id}' not found.",
        )

    gate_status["contradictions"] = contradictions

    # Recompute validation_gate
    unresolved = [
        c for c in contradictions
        if isinstance(c, dict) and not c.get("resolved", False) and c.get("severity") == "contradiction"
    ]
    gate_status["validation_gate"] = "passed" if not unresolved else "contradictions_pending"
    snapshot_orm.gate_status = gate_status
    await db.commit()
    await db.refresh(snapshot_orm)

    # Load documents for gate status
    doc_result = await db.execute(
        select(Document).where(Document.client_id == client_uuid)
    )
    documents = doc_result.scalars().all()
    return _compute_gate_status(documents, snapshot_orm)


@router.post("/{client_id}/income-projections")
async def save_income_projections(
    client_id: str,
    body: IncomeProjectionsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save income projections for a client."""
    try:
        client_uuid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client_id format.")

    snap_result = await db.execute(
        select(ClientFinancialSnapshotORM)
        .where(ClientFinancialSnapshotORM.client_id == client_uuid)
        .order_by(ClientFinancialSnapshotORM.version.desc())
    )
    snapshot_orm = snap_result.scalar_one_or_none()

    if snapshot_orm is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Snapshot must be assembled before saving income projections.",
        )

    # Update snapshot_data with projections
    snapshot_data = dict(snapshot_orm.snapshot_data or {})
    if "income" not in snapshot_data:
        snapshot_data["income"] = {}

    snapshot_data["income"]["projections"] = [
        {
            "year": p.year,
            "estimated_income": p.estimated_income,
            "notes": p.notes,
            "source": "advisor_input",
        }
        for p in body.projections
    ]

    # Update social security if provided
    if body.social_security_start_age is not None or body.social_security_monthly_benefit is not None:
        snapshot_data["income"]["social_security"] = {
            "start_age": body.social_security_start_age,
            "monthly_benefit_estimate": body.social_security_monthly_benefit,
        }

    snapshot_orm.snapshot_data = snapshot_data
    flag_modified(snapshot_orm, "snapshot_data")

    # Update income_table_gate
    gate_status = dict(snapshot_orm.gate_status or {})
    gate_status["income_table_gate"] = "passed"
    snapshot_orm.gate_status = gate_status
    flag_modified(snapshot_orm, "gate_status")

    await db.commit()
    await db.refresh(snapshot_orm)

    return {
        "snapshot_id": str(snapshot_orm.id),
        "client_id": str(snapshot_orm.client_id),
        "version": snapshot_orm.version,
        "snapshot_data": snapshot_orm.snapshot_data,
        "gate_status": snapshot_orm.gate_status,
    }


@router.get("/{client_id}/income-projections")
async def get_income_projections(
    client_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get income projections for a client."""
    try:
        client_uuid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client_id format.")

    snap_result = await db.execute(
        select(ClientFinancialSnapshotORM)
        .where(ClientFinancialSnapshotORM.client_id == client_uuid)
        .order_by(ClientFinancialSnapshotORM.version.desc())
    )
    snapshot_orm = snap_result.scalar_one_or_none()

    if snapshot_orm is None or not snapshot_orm.snapshot_data:
        return {"projections": None, "social_security": None}

    income_data = snapshot_orm.snapshot_data.get("income", {})
    return {
        "projections": income_data.get("projections"),
        "social_security": income_data.get("social_security"),
    }
