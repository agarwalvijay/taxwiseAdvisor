"""
SQLAlchemy ORM models for Advisor, Client, and Document.
Also contains Pydantic schemas for extraction and classification results.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    String,
    ForeignKey,
    DateTime,
    Float,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pydantic import BaseModel, Field

from backend.database import Base


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class Advisor(Base):
    __tablename__ = "advisors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    clients: Mapped[list["Client"]] = relationship("Client", back_populates="advisor")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    advisor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("advisors.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    advisor: Mapped["Advisor"] = relationship("Advisor", back_populates="clients")
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="client"
    )
    snapshots: Mapped[list["ClientFinancialSnapshotORM"]] = relationship(
        "ClientFinancialSnapshotORM", back_populates="client"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    s3_key: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    document_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    institution: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    tax_year: Mapped[Optional[int]] = mapped_column(nullable=True)
    classification_confidence: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    classification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    raw_extraction: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    extraction_confidence_map: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    client: Mapped["Client"] = relationship("Client", back_populates="documents")


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class ClassificationResult(BaseModel):
    document_type: str  # form_1040 | brokerage_statement | retirement_401k | retirement_ira | w2 | ssa_estimate | unknown
    institution: Optional[str] = None  # fidelity | schwab | vanguard | ... | null
    tax_year: Optional[int] = None
    confidence: float
    rejection_reason: Optional[str] = None


class FieldConfidence(BaseModel):
    value: Any
    confidence: float
    inferred: bool = False  # True if value was inferred rather than explicitly stated
    note: Optional[str] = None


class ExtractionResult(BaseModel):
    document_type: str
    tax_year: Optional[int] = None
    institution: Optional[str] = None
    fields: dict[str, FieldConfidence]
    extraction_notes: list[str] = Field(default_factory=list)
    overall_confidence: float


class DocumentUploadResponse(BaseModel):
    document_id: str
    client_id: str
    filename: str
    classification: ClassificationResult
    extraction: Optional[ExtractionResult] = None
    gate_status: Optional[dict] = None
    status: str  # classified | extracted | gate_passed | gate_failed | classification_failed
    message: Optional[str] = None
