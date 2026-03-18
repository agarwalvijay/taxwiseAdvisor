"""
SQLAlchemy ORM model for Plan and its Pydantic schemas.
"""

import uuid
from datetime import datetime
from typing import Literal, Optional

from sqlalchemy import String, ForeignKey, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pydantic import BaseModel, Field

from backend.database import Base


class PlanORM(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("client_financial_snapshots.id"),
        nullable=False,
    )
    step_outputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    plan_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    snapshot: Mapped["ClientFinancialSnapshotORM"] = relationship(  # noqa: F821
        "ClientFinancialSnapshotORM", back_populates="plans"
    )
    reports: Mapped[list["ReportORM"]] = relationship(  # noqa: F821
        "ReportORM", back_populates="plan"
    )


class ReasoningStep(BaseModel):
    step_number: int
    step_name: str
    input_slice: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    confidence: Optional[float] = None
    completed: bool = False


# ---------------------------------------------------------------------------
# Reasoning Step Output Pydantic Models (Session 4)
# ---------------------------------------------------------------------------


class IrmaaRisk(BaseModel):
    flagged: bool
    reason: str
    tier_at_risk: Optional[int] = None


class TaxTrajectoryOutput(BaseModel):
    current_bracket: float
    current_agi: float
    retirement_bracket_estimate: float
    rmd_bracket_estimate: float
    irmaa_risk: IrmaaRisk
    conversion_window_years: list[int]
    conversion_window_rationale: str
    years_until_rmd: int
    projected_first_rmd: float
    projected_pretax_at_rmd: float
    urgency: Literal["high", "medium", "low"]
    ss_taxation_risk: bool
    narrative: str
    confidence: float
    data_gaps: list[str] = Field(default_factory=list)


class YearlyConversion(BaseModel):
    year: int
    convert_amount: float
    estimated_federal_tax: float
    estimated_state_tax: float
    bracket_used: str
    post_conversion_agi: float
    irmaa_safe: bool
    irmaa_note: Optional[str] = None
    aca_safe: bool
    aca_note: Optional[str] = None
    ss_taxation_impact: Optional[str] = None
    net_benefit_note: str


class ConversionOptimizerOutput(BaseModel):
    conversion_plan: list[YearlyConversion]
    total_converted: float
    estimated_total_tax_on_conversions: float
    liquidity_check_passed: bool
    liquidity_note: Optional[str] = None
    aca_cliff_risk_years: list[int] = Field(default_factory=list)
    irmaa_cliff_risk_years: list[int] = Field(default_factory=list)
    state_tax_note: str
    narrative: str
    confidence: float
    data_gaps: list[str] = Field(default_factory=list)


class TLHOpportunity(BaseModel):
    symbol: str
    description: str
    unrealized_loss: float
    holding_period: str
    action: str
    suggested_replacement: str
    wash_sale_risk: Literal["none", "low", "high"]
    wash_sale_note: str
    estimated_tax_benefit: float
    niit_benefit: Optional[float] = None


class AssetLocationMove(BaseModel):
    asset_description: str
    current_location: str
    recommended_location: str
    rationale: str
    priority: Literal["high", "medium", "low"]


class TLHAdvisorOutput(BaseModel):
    tlh_section_complete: bool
    tlh_unavailable_reason: Optional[str] = None
    tlh_opportunities: list[TLHOpportunity] = Field(default_factory=list)
    total_harvestable_losses: float = 0.0
    estimated_total_tax_benefit: float = 0.0
    asset_location_moves: list[AssetLocationMove] = Field(default_factory=list)
    narrative: str
    confidence: float
    data_gaps: list[str] = Field(default_factory=list)


class DataGap(BaseModel):
    field: str
    description: str
    plan_impact: str
    severity: Literal["high", "medium", "low"]


class ClientSnapshotSummary(BaseModel):
    age: int
    spouse_age: Optional[int] = None
    filing_status: str
    state: str
    retirement_target_age: int
    years_to_retirement: int
    current_agi: float
    total_pretax_balance: float
    total_roth_balance: float
    total_taxable_balance: float
    total_hsa_balance: float = 0.0
    cash_savings: float = 0.0
    years_until_rmd: int
    projected_first_rmd: float


class DoNothingComparison(BaseModel):
    projected_rmd_at_73: float
    rmd_bracket: float
    irmaa_triggered: bool
    estimated_lifetime_tax_savings: float
    narrative: str


class YearlyConversionRow(BaseModel):
    year: int
    pre_conversion_income: float
    convert_amount: float
    post_conversion_agi: float
    federal_tax: float
    state_tax: float
    total_tax: float
    effective_rate_pct: float
    cumulative_converted: float
    irmaa_safe: bool
    note: Optional[str] = None


class ConversionTableSummary(BaseModel):
    rows: list[YearlyConversionRow]
    total_converted: float
    total_tax_paid: float
    blended_effective_rate_pct: float
    il_state_tax_note: str


class TLHSummary(BaseModel):
    available: bool
    total_harvestable_losses: float = 0.0
    estimated_total_tax_benefit: float = 0.0
    niit_benefit: float = 0.0
    action_items: list[str] = Field(default_factory=list)
    unavailable_reason: Optional[str] = None


class PriorityAction(BaseModel):
    priority: int
    category: Literal["roth_conversion", "tlh", "asset_location", "other"]
    action: str
    rationale: str
    estimated_benefit: str
    urgency: Literal["immediate", "this_year", "multi_year"]
    confidence: Literal["high", "medium", "low"]


class PlanSynthesizerOutput(BaseModel):
    executive_summary: str
    client_snapshot_summary: ClientSnapshotSummary
    do_nothing_comparison: DoNothingComparison
    priority_actions: list[PriorityAction]
    conversion_table: ConversionTableSummary
    tlh_summary: TLHSummary
    key_assumptions: list[str] = Field(default_factory=list)
    data_gaps: list[DataGap] = Field(default_factory=list)
    plan_confidence: float
    urgency: Literal["high", "medium", "low"]
    disclaimer: str
    narrative: str
