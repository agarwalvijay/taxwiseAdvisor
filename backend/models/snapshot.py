"""
SQLAlchemy ORM model for ClientFinancialSnapshot and its Pydantic schema.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, DateTime, Integer, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pydantic import BaseModel, Field, model_validator

from backend.database import Base


# ---------------------------------------------------------------------------
# ORM Model
# ---------------------------------------------------------------------------


class ClientFinancialSnapshotORM(Base):
    __tablename__ = "client_financial_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    snapshot_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    gate_status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    data_provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    client: Mapped["Client"] = relationship(  # noqa: F821
        "Client", back_populates="snapshots"
    )
    plans: Mapped[list["PlanORM"]] = relationship(  # noqa: F821
        "PlanORM", back_populates="snapshot"
    )


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class PersonalInfo(BaseModel):
    age: Optional[int] = None
    spouse_age: Optional[int] = None
    filing_status: Optional[str] = None  # married_filing_jointly | single | head_of_household | married_filing_separately
    state: Optional[str] = None
    retirement_target_age: Optional[int] = None
    spouse_retirement_target_age: Optional[int] = None


class IncomeProjection(BaseModel):
    year: int
    estimated_income: float
    notes: Optional[str] = None
    source: str = "advisor_input"  # advisor_input | extracted


class SocialSecurityInfo(BaseModel):
    start_age: Optional[int] = None
    monthly_benefit_estimate: Optional[float] = None


class IncomeInfo(BaseModel):
    current_year_agi: Optional[float] = None
    projections: list[IncomeProjection] = Field(default_factory=list)
    social_security: Optional[SocialSecurityInfo] = None


class Holding(BaseModel):
    symbol: Optional[str] = None
    description: Optional[str] = None
    shares: Optional[float] = None
    price_per_share: Optional[float] = None
    market_value: Optional[float] = None
    cost_basis: Optional[float] = None
    unrealized_gain_loss: Optional[float] = None
    holding_period: Optional[str] = None  # short_term | long_term | unknown


class TaxableBrokerageAccount(BaseModel):
    institution: Optional[str] = None
    total_value: Optional[float] = None
    cash_balance: Optional[float] = None
    holdings: list[Holding] = Field(default_factory=list)


class RetirementAccount(BaseModel):
    institution: Optional[str] = None
    balance: Optional[float] = None
    employer: Optional[str] = None
    basis: Optional[float] = None  # For traditional IRA non-deductible basis


class HsaAccount(BaseModel):
    institution: Optional[str] = None
    balance: Optional[float] = None


class AccountsInfo(BaseModel):
    taxable_brokerage: list[TaxableBrokerageAccount] = Field(default_factory=list)
    traditional_401k: list[RetirementAccount] = Field(default_factory=list)
    roth_401k: list[RetirementAccount] = Field(default_factory=list)
    traditional_ira: list[RetirementAccount] = Field(default_factory=list)
    roth_ira: list[RetirementAccount] = Field(default_factory=list)
    hsa: list[HsaAccount] = Field(default_factory=list)
    cash_savings: Optional[float] = None


class TaxProfile(BaseModel):
    current_marginal_bracket: Optional[float] = None
    current_agi: Optional[float] = None
    ltcg_rate: Optional[float] = None
    state_income_tax_rate: Optional[float] = None
    irmaa_exposure: Optional[bool] = None
    irmaa_tier1_threshold_mfj: Optional[float] = None
    irmaa_buffer: Optional[float] = None
    niit_exposure: Optional[bool] = None
    niit_threshold_mfj: Optional[float] = None
    aca_relevant: Optional[bool] = None


class RmdProfile(BaseModel):
    rmd_start_age: Optional[int] = None
    years_until_rmd: Optional[int] = None
    projected_pretax_balance_at_rmd: Optional[float] = None
    projected_first_rmd: Optional[float] = None


class AdvisorOverride(BaseModel):
    field: str
    original_extracted: Any
    advisor_confirmed: Any
    timestamp: str


class DataProvenance(BaseModel):
    source_documents: list[str] = Field(default_factory=list)
    advisor_overrides: list[AdvisorOverride] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)
    missing_soft_required: list[str] = Field(default_factory=list)
    snapshot_version: int = 1
    created_at: Optional[str] = None


class ClientFinancialSnapshotSchema(BaseModel):
    client_id: str
    snapshot_date: Optional[str] = None
    personal: PersonalInfo = Field(default_factory=PersonalInfo)
    income: IncomeInfo = Field(default_factory=IncomeInfo)
    accounts: AccountsInfo = Field(default_factory=AccountsInfo)
    tax_profile: TaxProfile = Field(default_factory=TaxProfile)
    rmd_profile: RmdProfile = Field(default_factory=RmdProfile)
    data_provenance: DataProvenance = Field(default_factory=DataProvenance)


# ---------------------------------------------------------------------------
# Gate Status Schemas
# ---------------------------------------------------------------------------


class FlaggedField(BaseModel):
    field_name: str
    extracted_value: Any
    confidence: Optional[float] = None
    reason: str
    field_classification: str  # hard_required | soft_required | optional


class GateStatus(BaseModel):
    passed: bool
    flagged_fields: list[FlaggedField] = Field(default_factory=list)
    hard_required_failed: list[str] = Field(default_factory=list)
    soft_required_missing: list[str] = Field(default_factory=list)
    optional_missing: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Session 3 Models
# ---------------------------------------------------------------------------


class ContradictionRecord(BaseModel):
    contradiction_id: str
    check_name: str
    severity: str
    description: str
    field_a: Optional[str] = None
    source_a: Optional[str] = None
    value_a: Any = None
    field_b: Optional[str] = None
    source_b: Optional[str] = None
    value_b: Any = None
    suggested_resolution: str
    resolved: bool = False
    resolved_value: Any = None
    resolution_note: Optional[str] = None
    resolved_at: Optional[str] = None


class AdvisorConfirmation(BaseModel):
    field_path: str
    confirmed_value: Any
    original_extracted: Any
    confirmed_at: str


class ClientGateStatus(BaseModel):
    # Individual gate states
    classification_gate: str = "not_started"   # passed | failed | not_started
    extraction_gate: str = "not_started"        # passed | review_required | not_started
    validation_gate: str = "not_started"        # passed | contradictions_pending | not_started
    snapshot_gate: str = "not_started"          # passed | missing_required_fields | not_started
    income_table_gate: str = "not_started"      # passed | not_started
    # Summary
    overall_status: str = "action_required"     # ready_for_plan | action_required
    flagged_fields: list[FlaggedField] = Field(default_factory=list)
    contradictions: list[ContradictionRecord] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    blocking_reason: Optional[str] = None
    # Mutable advisor state (persisted in gate_status JSONB)
    advisor_confirmations: dict[str, AdvisorConfirmation] = Field(default_factory=dict)


class ConfirmFieldRequest(BaseModel):
    field_path: str
    confirmed_value: Any
    original_extracted: Any = None


class ResolveContradictionRequest(BaseModel):
    contradiction_id: str
    resolution: str
    resolved_value: Any = None


class IncomeProjectionInput(BaseModel):
    year: int
    estimated_income: float
    notes: Optional[str] = None


class IncomeProjectionsRequest(BaseModel):
    projections: list[IncomeProjectionInput]
    social_security_start_age: Optional[int] = None
    social_security_monthly_benefit: Optional[float] = None

    @model_validator(mode="after")
    def validate_projections(self):
        if len(self.projections) < 3:
            raise ValueError("At least 3 years of income projections are required.")
        years = [p.year for p in self.projections]
        if len(set(years)) != len(years):
            raise ValueError("Each projection year must be unique.")
        for p in self.projections:
            if p.estimated_income < 0:
                raise ValueError(f"Estimated income for {p.year} must be >= 0.")
        if self.social_security_start_age is not None:
            if not (62 <= self.social_security_start_age <= 70):
                raise ValueError("social_security_start_age must be between 62 and 70.")
        return self
