"""
Snapshot assembler.

Merges extracted documents for a client into a ClientFinancialSnapshot.
Applies advisor overrides. Validates hard required fields.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.document import Document, ExtractionResult
from backend.models.snapshot import (
    AccountsInfo,
    AdvisorOverride,
    ClientFinancialSnapshotORM,
    ClientFinancialSnapshotSchema,
    DataProvenance,
    HsaAccount,
    Holding,
    IncomeInfo,
    PersonalInfo,
    RetirementAccount,
    RmdProfile,
    TaxProfile,
    TaxableBrokerageAccount,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SnapshotAssemblyError(Exception):
    def __init__(self, message: str, missing_fields: list[str]):
        super().__init__(message)
        self.missing_fields = missing_fields


# ---------------------------------------------------------------------------
# Tax bracket helpers (2026 brackets)
# ---------------------------------------------------------------------------

# 2026 MFJ brackets: list of (upper_limit, rate). Last entry upper_limit=inf
_MFJ_BRACKETS = [
    (23850, 0.10), (96950, 0.12), (206700, 0.22), (394600, 0.24),
    (501050, 0.32), (751600, 0.35), (float("inf"), 0.37),
]
_SINGLE_BRACKETS = [
    (11925, 0.10), (48475, 0.12), (103350, 0.22), (197300, 0.24),
    (250525, 0.32), (626350, 0.35), (float("inf"), 0.37),
]
_HOH_BRACKETS = [
    (17000, 0.10), (64850, 0.12), (103350, 0.22), (197300, 0.24),
    (250500, 0.32), (626350, 0.35), (float("inf"), 0.37),
]
# 2026 LTCG MFJ thresholds
_MFJ_LTCG = [(96700, 0.0), (600050, 0.15), (float("inf"), 0.20)]
_SINGLE_LTCG = [(48350, 0.0), (533400, 0.15), (float("inf"), 0.20)]

_STATE_TAX_RATES = {"IL": 0.0495}  # flat; extend as needed
_IRMAA_THRESHOLD_MFJ = 212000.0
_NIIT_THRESHOLD_MFJ = 250000.0
_NIIT_THRESHOLD_SINGLE = 200000.0


def _marginal_bracket(taxable_income: float, filing_status: str) -> float:
    brackets = _MFJ_BRACKETS
    if filing_status in ("single", "married_filing_separately"):
        brackets = _SINGLE_BRACKETS
    elif filing_status == "head_of_household":
        brackets = _HOH_BRACKETS
    for limit, rate in brackets:
        if taxable_income <= limit:
            return rate
    return 0.37


def _ltcg_rate(agi: float, filing_status: str) -> float:
    thresholds = _MFJ_LTCG if filing_status == "married_filing_jointly" else _SINGLE_LTCG
    for limit, rate in thresholds:
        if agi <= limit:
            return rate
    return 0.20


# ---------------------------------------------------------------------------
# Pure assembly function
# ---------------------------------------------------------------------------

# Hard required snapshot fields that must be present
SNAPSHOT_HARD_REQUIRED = [
    "personal.filing_status",
    "personal.age",
    "personal.state",
    "income.current_year_agi",
    "personal.retirement_target_age",
]


def assemble_from_extractions(
    client_id: str,
    extractions: list[ExtractionResult],
    advisor_confirmations: dict,  # field_path (dot notation) → AdvisorConfirmation-like dict
    snapshot_date: str,
) -> ClientFinancialSnapshotSchema:
    """
    Pure function — no DB calls.

    Merges all extracted documents for a client into a single
    ClientFinancialSnapshotSchema, applying advisor confirmations as overrides.

    Raises SnapshotAssemblyError if hard required fields are missing.
    """

    def _get(field_path: str, extracted_value: Any) -> Any:
        """Return advisor-confirmed value if available, else extracted value."""
        confirmation = advisor_confirmations.get(field_path)
        if confirmation is not None:
            # Support both dict and AdvisorConfirmation-like objects
            if isinstance(confirmation, dict):
                return confirmation.get("confirmed_value", extracted_value)
            return getattr(confirmation, "confirmed_value", extracted_value)
        return extracted_value

    def _field_val(extraction: ExtractionResult, field_name: str) -> Any:
        """Get the value of a field from an extraction, or None."""
        fc = extraction.fields.get(field_name)
        return fc.value if fc is not None else None

    # Find the 1040 extraction
    form_1040 = next((e for e in extractions if e.document_type == "form_1040"), None)

    # ---------- Personal Info ----------
    raw_filing_status = _field_val(form_1040, "filing_status") if form_1040 else None
    raw_state = _field_val(form_1040, "state_of_residence") if form_1040 else None

    filing_status = _get("personal.filing_status", raw_filing_status)
    state = _get("personal.state", raw_state)
    age = _get("personal.age", None)  # age is never on the 1040; must come from confirmations
    retirement_target_age = _get("personal.retirement_target_age", None)

    # Spouse age and retirement target age (optional)
    spouse_age = _get("personal.spouse_age", None)
    spouse_retirement_target_age = _get("personal.spouse_retirement_target_age", None)

    personal = PersonalInfo(
        age=age,
        spouse_age=spouse_age,
        filing_status=filing_status,
        state=state,
        retirement_target_age=retirement_target_age,
        spouse_retirement_target_age=spouse_retirement_target_age,
    )

    # ---------- Income Info ----------
    raw_agi = _field_val(form_1040, "agi") if form_1040 else None
    # Accept "agi" flat key (from gate review Submit Correction) as fallback
    current_year_agi = _get("income.current_year_agi", None) or _get("agi", raw_agi)
    if current_year_agi is not None:
        current_year_agi = float(current_year_agi)

    income = IncomeInfo(
        current_year_agi=current_year_agi,
    )

    # ---------- Accounts ----------
    taxable_brokerage_list: list[TaxableBrokerageAccount] = []
    traditional_ira_list: list[RetirementAccount] = []
    roth_ira_list: list[RetirementAccount] = []
    traditional_401k_list: list[RetirementAccount] = []
    roth_401k_list: list[RetirementAccount] = []
    hsa_list: list[HsaAccount] = []

    for ex in extractions:
        dt = ex.document_type

        if dt == "brokerage_statement":
            holdings: list[Holding] = []
            # Parse holdings if present
            holdings_raw = ex.fields.get("holdings")
            if holdings_raw and holdings_raw.value:
                raw_holdings = holdings_raw.value
                if isinstance(raw_holdings, list):
                    for h in raw_holdings:
                        if isinstance(h, dict):
                            holdings.append(Holding(**{k: v for k, v in h.items() if k in Holding.model_fields}))

            total_value = _field_val(ex, "total_account_value")
            cash_balance = _field_val(ex, "cash_balance")
            taxable_brokerage_list.append(
                TaxableBrokerageAccount(
                    institution=ex.institution,
                    total_value=float(total_value) if total_value is not None else None,
                    cash_balance=float(cash_balance) if cash_balance is not None else None,
                    holdings=holdings,
                )
            )

        elif dt in (
            "retirement_ira", "traditional_ira", "roth_ira",
            "retirement_401k", "traditional_401k", "roth_401k",
        ):
            # For consolidated statements, use pre-computed totals when available
            total_roth = _field_val(ex, "total_roth_balance")
            total_pretax = _field_val(ex, "total_pretax_retirement_balance")
            total_hsa = _field_val(ex, "total_hsa_balance")

            if total_roth is not None or total_pretax is not None or total_hsa is not None:
                # Consolidated statement — split into category buckets
                if total_roth is not None:
                    roth_ira_list.append(
                        RetirementAccount(
                            institution=ex.institution,
                            balance=float(total_roth),
                        )
                    )
                if total_pretax is not None:
                    traditional_ira_list.append(
                        RetirementAccount(
                            institution=ex.institution,
                            balance=float(total_pretax),
                        )
                    )
                if total_hsa is not None:
                    hsa_list.append(
                        HsaAccount(
                            institution=ex.institution,
                            balance=float(total_hsa),
                        )
                    )
            else:
                # Single-account statement — use account_value with doc-type routing
                balance = _field_val(ex, "account_value")
                if dt in ("retirement_ira", "traditional_ira"):
                    traditional_ira_list.append(
                        RetirementAccount(
                            institution=ex.institution,
                            balance=float(balance) if balance is not None else None,
                        )
                    )
                elif dt == "roth_ira":
                    roth_ira_list.append(
                        RetirementAccount(
                            institution=ex.institution,
                            balance=float(balance) if balance is not None else None,
                        )
                    )
                elif dt in ("retirement_401k", "traditional_401k"):
                    employer = _field_val(ex, "employer")
                    traditional_401k_list.append(
                        RetirementAccount(
                            institution=ex.institution,
                            balance=float(balance) if balance is not None else None,
                            employer=employer,
                        )
                    )
                elif dt == "roth_401k":
                    roth_401k_list.append(
                        RetirementAccount(
                            institution=ex.institution,
                            balance=float(balance) if balance is not None else None,
                        )
                    )

        elif dt == "hsa":
            balance = _field_val(ex, "account_value")
            hsa_list.append(
                HsaAccount(
                    institution=ex.institution,
                    balance=float(balance) if balance is not None else None,
                )
            )

    accounts = AccountsInfo(
        taxable_brokerage=taxable_brokerage_list,
        traditional_ira=traditional_ira_list,
        roth_ira=roth_ira_list,
        traditional_401k=traditional_401k_list,
        roth_401k=roth_401k_list,
        hsa=hsa_list,
    )

    # ---------- Tax Profile ----------
    raw_taxable_income = _field_val(form_1040, "taxable_income") if form_1040 else None
    taxable_income = float(raw_taxable_income) if raw_taxable_income is not None else None
    agi_for_tax = current_year_agi

    tax_profile = TaxProfile()
    if filing_status and taxable_income is not None:
        tax_profile.current_marginal_bracket = _marginal_bracket(taxable_income, filing_status)

    if agi_for_tax is not None and filing_status:
        tax_profile.current_agi = agi_for_tax
        tax_profile.ltcg_rate = _ltcg_rate(agi_for_tax, filing_status)

        state_rate = _STATE_TAX_RATES.get(state) if state else None
        tax_profile.state_income_tax_rate = state_rate

        # IRMAA exposure
        if filing_status == "married_filing_jointly":
            tax_profile.irmaa_exposure = agi_for_tax > _IRMAA_THRESHOLD_MFJ
            tax_profile.irmaa_tier1_threshold_mfj = _IRMAA_THRESHOLD_MFJ
            tax_profile.irmaa_buffer = max(0.0, _IRMAA_THRESHOLD_MFJ - agi_for_tax)
        else:
            tax_profile.irmaa_exposure = agi_for_tax > (_IRMAA_THRESHOLD_MFJ / 2)

        # NIIT exposure
        niit_threshold = (
            _NIIT_THRESHOLD_MFJ
            if filing_status == "married_filing_jointly"
            else _NIIT_THRESHOLD_SINGLE
        )
        tax_profile.niit_exposure = agi_for_tax > niit_threshold
        tax_profile.niit_threshold_mfj = _NIIT_THRESHOLD_MFJ

    # ---------- RMD Profile ----------
    _RMD_START_AGE = 73
    rmd_profile = RmdProfile(rmd_start_age=_RMD_START_AGE)
    if age is not None:
        years_until_rmd = max(0, _RMD_START_AGE - int(age))
        rmd_profile.years_until_rmd = years_until_rmd

    # ---------- Data Provenance ----------
    source_documents = [ex.document_type for ex in extractions]
    # Collect advisor overrides from confirmations
    advisor_overrides: list[AdvisorOverride] = []
    for field_path, conf in advisor_confirmations.items():
        if isinstance(conf, dict):
            advisor_overrides.append(
                AdvisorOverride(
                    field=field_path,
                    original_extracted=conf.get("original_extracted"),
                    advisor_confirmed=conf.get("confirmed_value"),
                    timestamp=conf.get("confirmed_at", snapshot_date),
                )
            )
        else:
            advisor_overrides.append(
                AdvisorOverride(
                    field=field_path,
                    original_extracted=getattr(conf, "original_extracted", None),
                    advisor_confirmed=getattr(conf, "confirmed_value", None),
                    timestamp=getattr(conf, "confirmed_at", snapshot_date),
                )
            )

    data_provenance = DataProvenance(
        source_documents=source_documents,
        advisor_overrides=advisor_overrides,
        snapshot_version=1,
        created_at=snapshot_date,
    )

    # ---------- Build Snapshot ----------
    snapshot = ClientFinancialSnapshotSchema(
        client_id=client_id,
        snapshot_date=snapshot_date,
        personal=personal,
        income=income,
        accounts=accounts,
        tax_profile=tax_profile,
        rmd_profile=rmd_profile,
        data_provenance=data_provenance,
    )

    # ---------- Validate Hard Required Fields ----------
    snapshot_hard_required_values = {
        "personal.filing_status": snapshot.personal.filing_status,
        "personal.age": snapshot.personal.age,
        "personal.state": snapshot.personal.state,
        "income.current_year_agi": snapshot.income.current_year_agi,
        "personal.retirement_target_age": snapshot.personal.retirement_target_age,
    }

    missing: list[str] = [
        field_path
        for field_path, value in snapshot_hard_required_values.items()
        if value is None
    ]

    if missing:
        raise SnapshotAssemblyError(
            f"Snapshot assembly failed: missing required fields: {', '.join(missing)}",
            missing_fields=missing,
        )

    return snapshot


# ---------------------------------------------------------------------------
# Async DB function
# ---------------------------------------------------------------------------


async def assemble_snapshot(client_id: str, db: AsyncSession) -> ClientFinancialSnapshotORM:
    """
    Load all classified documents for a client, assemble the snapshot, and upsert it.

    Returns the persisted ClientFinancialSnapshotORM.
    """
    import uuid as _uuid

    client_uuid = _uuid.UUID(client_id)

    # Step 1: Load all classified documents with raw extractions
    result = await db.execute(
        select(Document).where(
            Document.client_id == client_uuid,
            Document.classification_status == "classified",
            Document.raw_extraction.isnot(None),
        )
    )
    documents = result.scalars().all()

    # Step 2: Parse each into ExtractionResult
    extractions: list[ExtractionResult] = []
    for doc in documents:
        if doc.raw_extraction:
            try:
                extractions.append(ExtractionResult(**doc.raw_extraction))
            except Exception:
                pass

    # Step 3: Load existing snapshot record to get advisor_confirmations
    existing_result = await db.execute(
        select(ClientFinancialSnapshotORM)
        .where(ClientFinancialSnapshotORM.client_id == client_uuid)
        .order_by(ClientFinancialSnapshotORM.version.desc())
    )
    existing_snapshot = existing_result.scalar_one_or_none()

    advisor_confirmations: dict = {}
    existing_version = 0
    if existing_snapshot:
        existing_version = existing_snapshot.version
        gate_status = existing_snapshot.gate_status or {}
        advisor_confirmations = gate_status.get("advisor_confirmations", {})

    # Step 4: Assemble
    snapshot_date = datetime.now(timezone.utc).isoformat()
    assembled = assemble_from_extractions(
        client_id=client_id,
        extractions=extractions,
        advisor_confirmations=advisor_confirmations,
        snapshot_date=snapshot_date,
    )

    # Step 5: Upsert snapshot record
    snapshot_data = assembled.model_dump()

    if existing_snapshot is None:
        # Create new
        new_snapshot = ClientFinancialSnapshotORM(
            client_id=client_uuid,
            snapshot_data=snapshot_data,
            gate_status={"advisor_confirmations": advisor_confirmations},
            data_provenance=snapshot_data.get("data_provenance", {}),
            version=1,
        )
        db.add(new_snapshot)
        await db.commit()
        await db.refresh(new_snapshot)
        return new_snapshot
    else:
        # Update existing
        existing_snapshot.snapshot_data = snapshot_data
        existing_snapshot.version = existing_version + 1
        existing_snapshot.data_provenance = snapshot_data.get("data_provenance", {})
        await db.commit()
        await db.refresh(existing_snapshot)
        return existing_snapshot
