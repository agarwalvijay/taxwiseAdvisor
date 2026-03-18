"""
Document API routes.

POST /api/documents/upload      — upload PDF, classify, extract, gate
GET  /api/documents/{client_id} — list documents for client
GET  /api/documents/{document_id}/extraction — get extraction result + gate status
"""

import uuid
from typing import Optional

import fitz  # PyMuPDF
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.extraction.classifier import classify_document
from backend.extraction.extractors.form_1040 import Form1040Extractor
from backend.extraction.extractors.brokerage import BrokerageExtractor
from backend.extraction.extractors.retirement_account import (
    TraditionalIRAExtractor,
    Retirement401kExtractor,
)
from backend.extraction.validator import validate_documents, ValidationResult
from backend.gates.confidence_gate import evaluate_extraction
from backend.models.document import Document, DocumentUploadResponse, ExtractionResult
from backend.models.snapshot import ClientFinancialSnapshotORM, GateStatus

router = APIRouter()

# Registry of extractors keyed by document_type returned from classifier
_EXTRACTORS = {
    "form_1040": Form1040Extractor(),
    "brokerage_statement": BrokerageExtractor(),
    "retirement_ira": TraditionalIRAExtractor(),
    "retirement_401k": Retirement401kExtractor(),
}


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_200_OK)
async def upload_document(
    file: UploadFile = File(...),
    client_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a PDF document for a client.

    Pipeline:
    1. Extract text with PyMuPDF
    2. Classify document type
    3. If classification fails → 422
    4. Run type-specific extractor
    5. Run confidence gate
    6. Persist document record
    7. Return document + gate status
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only PDF files are accepted.",
        )

    raw_bytes = await file.read()

    # --- Step 1: Extract text via PyMuPDF ---
    try:
        pdf_doc = fitz.open(stream=raw_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in pdf_doc)
        pdf_doc.close()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not read PDF: {exc}",
        )

    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="PDF contains no extractable text. Scanned PDFs are not yet supported.",
        )

    # --- Step 2: Classify ---
    classification = await classify_document(text)

    doc_id = uuid.uuid4()
    client_uuid = uuid.UUID(client_id)

    # Persist with classification result (even on rejection, we record the attempt)
    if (
        classification.confidence < 0.90
        or classification.document_type == "unknown"
    ):
        doc = Document(
            id=doc_id,
            client_id=client_uuid,
            filename=file.filename,
            document_type=classification.document_type,
            classification_confidence=classification.confidence,
            classification_status="rejected",
        )
        db.add(doc)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "classification_failed",
                "message": classification.rejection_reason,
                "document_id": str(doc_id),
                "confidence": classification.confidence,
            },
        )

    # --- Step 3: Extract ---
    extractor = _EXTRACTORS.get(classification.document_type)
    extraction: Optional[ExtractionResult] = None

    if extractor is not None:
        extraction = await extractor.extract(text)

    # --- Step 4: Gate ---
    gate_status: Optional[GateStatus] = None
    if extraction is not None:
        gate_status = evaluate_extraction(extraction)

    # --- Step 5: Persist ---
    doc = Document(
        id=doc_id,
        client_id=client_uuid,
        filename=file.filename,
        document_type=classification.document_type,
        institution=classification.institution,
        tax_year=classification.tax_year,
        classification_confidence=classification.confidence,
        classification_status="classified",
        raw_extraction=extraction.model_dump() if extraction else None,
        extraction_confidence_map=(
            {k: v.model_dump() for k, v in extraction.fields.items()}
            if extraction
            else None
        ),
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # --- Step 4b: Cross-document validation (if client has 2+ documents) ---
    all_docs_result = await db.execute(
        select(Document).where(
            Document.client_id == client_uuid,
            Document.classification_status == "classified",
            Document.raw_extraction.isnot(None),
        )
    )
    all_docs = all_docs_result.scalars().all()

    if len(all_docs) >= 2:
        all_extractions = [ExtractionResult(**d.raw_extraction) for d in all_docs if d.raw_extraction]
        val_result = validate_documents(all_extractions)
        await _upsert_snapshot_gate_status(client_uuid, val_result, db)

    overall_status = "classified"
    if extraction is not None:
        overall_status = "gate_passed" if (gate_status and gate_status.passed) else "gate_failed"

    return DocumentUploadResponse(
        document_id=str(doc.id),
        client_id=client_id,
        filename=file.filename,
        classification=classification,
        extraction=extraction,
        gate_status=gate_status.model_dump() if gate_status else None,
        status=overall_status,
    )


async def _upsert_snapshot_gate_status(
    client_id: uuid.UUID,
    val_result: ValidationResult,
    db: AsyncSession,
) -> None:
    """
    Create or update the ClientFinancialSnapshotORM record with validation issues
    stored in gate_status["contradictions"] and the appropriate validation_gate value.
    """
    # Serialize validation issues
    contradiction_dicts = [
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
            "resolved_value": None,
            "resolution_note": None,
            "resolved_at": None,
        }
        for issue in val_result.issues
        if issue.severity == "contradiction"
    ]

    validation_gate = "passed" if val_result.passed else "contradictions_pending"

    # Load existing snapshot
    result = await db.execute(
        select(ClientFinancialSnapshotORM)
        .where(ClientFinancialSnapshotORM.client_id == client_id)
        .order_by(ClientFinancialSnapshotORM.version.desc())
    )
    snapshot = result.scalar_one_or_none()

    if snapshot is None:
        # Create a new snapshot record with just gate_status populated
        new_gate_status = {
            "contradictions": contradiction_dicts,
            "validation_gate": validation_gate,
            "advisor_confirmations": {},
        }
        snapshot = ClientFinancialSnapshotORM(
            client_id=client_id,
            snapshot_data={},
            gate_status=new_gate_status,
            data_provenance={},
            version=1,
        )
        db.add(snapshot)
    else:
        # Update existing gate_status
        gate_status = dict(snapshot.gate_status or {})
        gate_status["contradictions"] = contradiction_dicts
        gate_status["validation_gate"] = validation_gate
        snapshot.gate_status = gate_status

    await db.commit()


@router.get("/{client_id}")
async def list_documents(
    client_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all documents for a client."""
    try:
        client_uuid = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client_id format.")

    result = await db.execute(
        select(Document).where(Document.client_id == client_uuid).order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()

    return [
        {
            "document_id": str(d.id),
            "filename": d.filename,
            "document_type": d.document_type,
            "institution": d.institution,
            "tax_year": d.tax_year,
            "classification_confidence": d.classification_confidence,
            "classification_status": d.classification_status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in docs
    ]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a document by ID."""
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document_id format.")

    result = await db.execute(select(Document).where(Document.id == doc_uuid))
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    await db.delete(doc)
    await db.commit()


@router.get("/{document_id}/extraction")
async def get_extraction(
    document_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get extraction result and gate status for a document."""
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document_id format.")

    result = await db.execute(select(Document).where(Document.id == doc_uuid))
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    gate_status = None
    if doc.raw_extraction:
        try:
            extraction = ExtractionResult(**doc.raw_extraction)
            gate = evaluate_extraction(extraction)
            gate_status = gate.model_dump()
        except Exception:
            gate_status = None

    return {
        "document_id": str(doc.id),
        "filename": doc.filename,
        "document_type": doc.document_type,
        "tax_year": doc.tax_year,
        "raw_extraction": doc.raw_extraction,
        "extraction_confidence_map": doc.extraction_confidence_map,
        "gate_status": gate_status,
    }
