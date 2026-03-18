"""
Cross-document consistency validator.

Runs deterministic (no AI) consistency checks across all extracted documents
for a single client. Called after individual extraction + confidence gate review,
before snapshot assembly.

Design principles:
- No Claude calls — pure deterministic logic
- Contradictions are specific enough for an advisor to resolve
- Warnings are non-blocking; contradictions halt snapshot assembly
- Each check is a standalone function for testability
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.models.document import ExtractionResult

# Tolerance for numeric comparisons (e.g. rounding, multi-account scenarios)
_NUMERIC_TOLERANCE_PCT = 0.05   # 5% — accounts for rounding and timing differences
_WAGES_TOLERANCE_PCT = 0.02     # 2% — W-2 vs 1040 wages should match tightly


@dataclass
class ValidationIssue:
    check_name: str
    severity: str               # "contradiction" | "warning"
    description: str
    fields_involved: list[str]
    documents_involved: list[str]   # document_type values
    suggested_resolution: str
    # Cross-document comparison fields (populated for new checks)
    field_a: Optional[str] = None
    source_a: Optional[str] = None   # document_type of the source
    value_a: Optional[Any] = None
    field_b: Optional[str] = None
    source_b: Optional[str] = None
    value_b: Optional[Any] = None
    contradiction_id: Optional[str] = None  # 8-char hash generated after creation


@dataclass
class ValidationResult:
    passed: bool                    # False if any contradiction present
    issues: list[ValidationIssue] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    @property
    def contradictions(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "contradiction"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def validate_documents(extractions: list[ExtractionResult]) -> ValidationResult:
    """
    Run all consistency checks across a set of extracted documents for one client.

    Args:
        extractions: List of ExtractionResult objects from all documents for this client.
                     Documents should all have passed their individual confidence gates
                     before reaching this step.

    Returns:
        ValidationResult — passed=True only if zero contradictions detected.
    """
    if len(extractions) < 2:
        return ValidationResult(
            passed=True,
            checks_run=["skipped_single_document"],
        )

    issues: list[ValidationIssue] = []
    checks_run: list[str] = []

    _run_check(
        check_tax_year_consistency, extractions, issues, checks_run
    )
    _run_check(
        check_duplicate_documents, extractions, issues, checks_run
    )
    _run_check(
        check_w2_wages_match_1040, extractions, issues, checks_run
    )
    _run_check(
        check_brokerage_income_consistent_with_1040, extractions, issues, checks_run
    )
    _run_check(
        check_retirement_balance_plausibility, extractions, issues, checks_run
    )
    _run_check(
        check_agi_vs_income_sources, extractions, issues, checks_run
    )
    _run_check(
        check_ira_contribution_plausibility, extractions, issues, checks_run
    )

    # Assign a deterministic contradiction_id to each issue
    for i, issue in enumerate(issues):
        raw = f"{issue.check_name}{i}"
        issue.contradiction_id = hashlib.md5(raw.encode()).hexdigest()[:8]

    contradictions = [i for i in issues if i.severity == "contradiction"]
    return ValidationResult(
        passed=len(contradictions) == 0,
        issues=issues,
        checks_run=checks_run,
    )


def _run_check(check_fn, extractions, issues, checks_run):
    """Run a check function, appending its name and any issues it returns."""
    checks_run.append(check_fn.__name__)
    new_issues = check_fn(extractions)
    issues.extend(new_issues)


# ---------------------------------------------------------------------------
# Individual check functions
# Each returns a list[ValidationIssue] (empty = no problems found).
# ---------------------------------------------------------------------------


def check_tax_year_consistency(
    extractions: list[ExtractionResult],
) -> list[ValidationIssue]:
    """
    All documents in the same upload batch should be for the same tax year,
    or at most span two consecutive years (e.g. 2024 tax return + Dec 2024 statement).
    Flag anything more than 1 year apart as a contradiction.
    """
    issues: list[ValidationIssue] = []

    years_with_docs: dict[int, list[str]] = {}
    for ex in extractions:
        if ex.tax_year is not None:
            years_with_docs.setdefault(ex.tax_year, []).append(ex.document_type)

    if len(years_with_docs) <= 1:
        return issues

    all_years = sorted(years_with_docs.keys())
    year_range = all_years[-1] - all_years[0]

    if year_range > 1:
        issues.append(
            ValidationIssue(
                check_name="check_tax_year_consistency",
                severity="contradiction",
                description=(
                    f"Documents span {year_range} tax years ({all_years[0]}–{all_years[-1]}). "
                    f"Expected documents for the same or consecutive tax years. "
                    f"Years found: {years_with_docs}"
                ),
                fields_involved=["tax_year"],
                documents_involved=list(years_with_docs.values()),
                suggested_resolution=(
                    "Confirm all uploaded documents are for the same tax planning year. "
                    "Remove documents from prior years unless intentionally included."
                ),
            )
        )
    elif year_range == 1:
        # Warn but don't block — common to mix tax return year with year-end statement
        issues.append(
            ValidationIssue(
                check_name="check_tax_year_consistency",
                severity="warning",
                description=(
                    f"Documents cover two consecutive years ({all_years[0]} and {all_years[-1]}). "
                    "This is normal if mixing a tax return with a year-end account statement."
                ),
                fields_involved=["tax_year"],
                documents_involved=[ex.document_type for ex in extractions],
                suggested_resolution=(
                    "Verify this is intentional. "
                    "Tax plan will use the most recent year's data where there is overlap."
                ),
            )
        )

    return issues


def check_duplicate_documents(
    extractions: list[ExtractionResult],
) -> list[ValidationIssue]:
    """
    Detect duplicate document uploads: same document_type + institution + tax_year
    appearing more than once in the same batch.
    """
    issues: list[ValidationIssue] = []
    seen: dict[tuple, int] = {}

    for ex in extractions:
        key = (ex.document_type, ex.institution, ex.tax_year)
        seen[key] = seen.get(key, 0) + 1

    for (doc_type, institution, tax_year), count in seen.items():
        if count > 1:
            label = f"{doc_type}"
            if institution:
                label += f" ({institution})"
            if tax_year:
                label += f" {tax_year}"
            issues.append(
                ValidationIssue(
                    check_name="check_duplicate_documents",
                    severity="contradiction",
                    description=(
                        f"'{label}' appears {count} times in this upload batch. "
                        "Duplicate documents will cause double-counting in the financial snapshot."
                    ),
                    fields_involved=["document_type", "institution", "tax_year"],
                    documents_involved=[doc_type] * count,
                    suggested_resolution=(
                        f"Remove the duplicate '{label}' document before proceeding."
                    ),
                )
            )

    return issues


def check_w2_wages_match_1040(
    extractions: list[ExtractionResult],
) -> list[ValidationIssue]:
    """
    If both a W-2 and Form 1040 are present, wages reported on the W-2 should
    approximately match wages on the 1040 (within 2% tolerance for multiple W-2s,
    pre-tax deductions, etc.).

    Note: A client may have multiple W-2s; we compare total W-2 wages to 1040 wages.
    """
    issues: list[ValidationIssue] = []

    form_1040s = [e for e in extractions if e.document_type == "form_1040"]
    w2s = [e for e in extractions if e.document_type == "w2"]

    if not form_1040s or not w2s:
        return issues

    form_1040 = form_1040s[0]
    wages_1040_field = form_1040.fields.get("wages_salaries_tips")

    if wages_1040_field is None or wages_1040_field.value is None:
        return issues

    wages_1040 = float(wages_1040_field.value)

    # Sum wages across all W-2s
    total_w2_wages = 0.0
    for w2 in w2s:
        w2_wages_field = w2.fields.get("box1_wages")
        if w2_wages_field and w2_wages_field.value is not None:
            total_w2_wages += float(w2_wages_field.value)

    if total_w2_wages == 0.0:
        return issues

    # Allow for rounding and small differences (cafeteria plan, etc.)
    tolerance = max(wages_1040 * _WAGES_TOLERANCE_PCT, 500.0)  # At least $500 buffer
    diff = abs(wages_1040 - total_w2_wages)

    if diff > tolerance:
        issues.append(
            ValidationIssue(
                check_name="check_w2_wages_match_1040",
                severity="contradiction",
                description=(
                    f"W-2 total wages (${total_w2_wages:,.0f}) differ from "
                    f"Form 1040 wages (${wages_1040:,.0f}) by ${diff:,.0f}, "
                    f"exceeding the {_WAGES_TOLERANCE_PCT:.0%} tolerance. "
                    "This may indicate a missing W-2, an incorrect document, or a data extraction error."
                ),
                fields_involved=["wages_salaries_tips", "box1_wages"],
                documents_involved=["form_1040", "w2"],
                suggested_resolution=(
                    "Verify all W-2s for this tax year are uploaded. "
                    "Confirm wages on line 1a of the 1040 match the sum of box 1 on all W-2s. "
                    "Differences may also arise from pre-tax cafeteria plan deductions."
                ),
            )
        )

    return issues


def check_brokerage_income_consistent_with_1040(
    extractions: list[ExtractionResult],
) -> list[ValidationIssue]:
    """
    Cross-check brokerage-reported YTD dividends/interest against the 1040.

    The brokerage total should be ≤ the 1040 total (the client may have
    additional accounts). Flag if brokerage total significantly exceeds 1040.
    """
    issues: list[ValidationIssue] = []

    form_1040s = [e for e in extractions if e.document_type == "form_1040"]
    brokerages = [e for e in extractions if e.document_type == "brokerage_statement"]

    if not form_1040s or not brokerages:
        return issues

    form_1040 = form_1040s[0]

    # Check dividends
    dividends_1040_field = form_1040.fields.get("ordinary_dividends")
    dividends_1040 = (
        float(dividends_1040_field.value)
        if dividends_1040_field and dividends_1040_field.value is not None
        else None
    )

    total_brokerage_dividends = 0.0
    for brok in brokerages:
        f = brok.fields.get("ytd_dividends")
        if f and f.value is not None:
            total_brokerage_dividends += float(f.value)

    if dividends_1040 is not None and total_brokerage_dividends > 0:
        # Brokerage total should not exceed 1040 total by more than tolerance
        tolerance = max(dividends_1040 * _NUMERIC_TOLERANCE_PCT, 100.0)
        if total_brokerage_dividends > dividends_1040 + tolerance:
            issues.append(
                ValidationIssue(
                    check_name="check_brokerage_income_consistent_with_1040",
                    severity="warning",
                    description=(
                        f"Brokerage YTD dividends (${total_brokerage_dividends:,.0f}) exceed "
                        f"ordinary dividends on Form 1040 (${dividends_1040:,.0f}). "
                        "This may indicate a missing 1040 schedule, a data extraction error, "
                        "or the brokerage statement covers a different period than the tax return."
                    ),
                    fields_involved=["ordinary_dividends", "ytd_dividends"],
                    documents_involved=["form_1040", "brokerage_statement"],
                    suggested_resolution=(
                        "Verify the brokerage statement and 1040 cover the same tax year. "
                        "Check Schedule B on the 1040 for dividend detail."
                    ),
                )
            )

    # Check taxable interest
    interest_1040_field = form_1040.fields.get("taxable_interest")
    interest_1040 = (
        float(interest_1040_field.value)
        if interest_1040_field and interest_1040_field.value is not None
        else None
    )

    total_brokerage_interest = 0.0
    for brok in brokerages:
        f = brok.fields.get("ytd_interest")
        if f and f.value is not None:
            total_brokerage_interest += float(f.value)

    if interest_1040 is not None and total_brokerage_interest > 0:
        tolerance = max(interest_1040 * _NUMERIC_TOLERANCE_PCT, 100.0)
        if total_brokerage_interest > interest_1040 + tolerance:
            issues.append(
                ValidationIssue(
                    check_name="check_brokerage_income_consistent_with_1040",
                    severity="warning",
                    description=(
                        f"Brokerage YTD interest (${total_brokerage_interest:,.0f}) exceeds "
                        f"taxable interest on Form 1040 (${interest_1040:,.0f}). "
                        "The client may have savings accounts or bonds not captured in this brokerage statement."
                    ),
                    fields_involved=["taxable_interest", "ytd_interest"],
                    documents_involved=["form_1040", "brokerage_statement"],
                    suggested_resolution=(
                        "Verify taxable interest on the 1040 includes all sources. "
                        "Differences may be due to bank savings accounts reported elsewhere."
                    ),
                )
            )

    return issues


def check_retirement_balance_plausibility(
    extractions: list[ExtractionResult],
) -> list[ValidationIssue]:
    """
    Basic sanity checks on retirement account balances:
    - Flag balances that are suspiciously large (> $50M) — possible extraction error
    - Flag balances of exactly $0 — likely extraction failure (not a real $0 account)
    - Flag negative balances — always an extraction error
    """
    issues: list[ValidationIssue] = []

    retirement_types = {"retirement_ira", "retirement_401k", "traditional_ira",
                        "roth_ira", "traditional_401k", "roth_401k"}

    for ex in extractions:
        if ex.document_type not in retirement_types:
            continue

        balance_field = ex.fields.get("account_value")
        if balance_field is None or balance_field.value is None:
            continue

        balance = float(balance_field.value)

        if balance < 0:
            issues.append(
                ValidationIssue(
                    check_name="check_retirement_balance_plausibility",
                    severity="contradiction",
                    description=(
                        f"{ex.document_type} account shows a negative balance "
                        f"(${balance:,.0f}). This is always a data extraction error."
                    ),
                    fields_involved=["account_value"],
                    documents_involved=[ex.document_type],
                    suggested_resolution=(
                        "Verify the account balance from the original statement. "
                        "Re-upload the document if extraction appears incorrect."
                    ),
                )
            )

        elif balance == 0.0:
            issues.append(
                ValidationIssue(
                    check_name="check_retirement_balance_plausibility",
                    severity="warning",
                    description=(
                        f"{ex.document_type} account balance extracted as exactly $0. "
                        "This may indicate an extraction error unless the account was recently opened."
                    ),
                    fields_involved=["account_value"],
                    documents_involved=[ex.document_type],
                    suggested_resolution=(
                        "Verify the account balance. If the account has a non-zero balance, "
                        "check the extraction result and manually enter the correct value."
                    ),
                )
            )

        elif balance > 50_000_000:
            issues.append(
                ValidationIssue(
                    check_name="check_retirement_balance_plausibility",
                    severity="warning",
                    description=(
                        f"{ex.document_type} account balance is ${balance:,.0f}, "
                        "which is unusually large. Please verify this is correct."
                    ),
                    fields_involved=["account_value"],
                    documents_involved=[ex.document_type],
                    suggested_resolution=(
                        "Confirm the account balance from the original statement. "
                        "This may indicate a unit error (e.g. cents instead of dollars)."
                    ),
                )
            )

    return issues


def check_agi_vs_income_sources(
    extractions: list[ExtractionResult],
) -> list[ValidationIssue]:
    """
    Only runs if a form_1040 is present. Reconstructs income from component fields
    and compares to the reported AGI. If the difference is > $1000, flag a warning
    (schedules not captured or extraction gap).
    """
    issues: list[ValidationIssue] = []

    form_1040s = [e for e in extractions if e.document_type == "form_1040"]
    if not form_1040s:
        return issues

    form_1040 = form_1040s[0]

    agi_field = form_1040.fields.get("agi")
    if agi_field is None or agi_field.value is None:
        return issues

    agi = float(agi_field.value)

    def _fv(field_name: str) -> float:
        """Get float value of a field, defaulting to 0 if missing or None."""
        f = form_1040.fields.get(field_name)
        if f is None or f.value is None:
            return 0.0
        return float(f.value)

    computed = (
        _fv("wages_salaries_tips")
        + _fv("taxable_interest")
        + _fv("ordinary_dividends")
        + _fv("capital_gains_or_loss")
        + _fv("ira_distributions_taxable")
        + _fv("pensions_annuities_taxable")
        + _fv("social_security_taxable")
        + _fv("schedule_1_additional_income")
        - _fv("adjustments_to_income")
    )

    if abs(agi - computed) > 1000:
        issues.append(
            ValidationIssue(
                check_name="check_agi_vs_income_sources",
                severity="warning",
                description=(
                    f"Reported AGI (${agi:,.0f}) differs from the sum of extracted "
                    f"income components (${computed:,.0f}) by ${abs(agi - computed):,.0f}. "
                    "This may indicate income schedules not captured in this extraction."
                ),
                fields_involved=["agi", "computed_income_sum"],
                documents_involved=["form_1040"],
                suggested_resolution=(
                    "Verify all income schedules (Schedule 1, etc.) are captured. "
                    "The difference may be due to additional income sources not extracted."
                ),
                field_a="agi",
                source_a="form_1040",
                value_a=agi,
                field_b="computed_income_sum",
                source_b="form_1040",
                value_b=computed,
            )
        )

    return issues


def check_ira_contribution_plausibility(
    extractions: list[ExtractionResult],
) -> list[ValidationIssue]:
    """
    Only runs if both form_1040 and retirement_ira present.
    If IRA statement shows ytd_employee_contributions > 0 AND 1040 adjustments_to_income
    is null or 0, flag a warning: IRA contributions may be deductible but no deduction found.
    """
    issues: list[ValidationIssue] = []

    form_1040s = [e for e in extractions if e.document_type == "form_1040"]
    iras = [e for e in extractions if e.document_type == "retirement_ira"]

    if not form_1040s or not iras:
        return issues

    form_1040 = form_1040s[0]
    ira = iras[0]

    contrib_field = ira.fields.get("ytd_employee_contributions")
    if contrib_field is None or contrib_field.value is None:
        return issues

    contrib = float(contrib_field.value)
    if contrib <= 0:
        return issues

    adj_field = form_1040.fields.get("adjustments_to_income")
    adj = float(adj_field.value) if (adj_field and adj_field.value is not None) else 0.0

    if adj is None or adj == 0.0:
        issues.append(
            ValidationIssue(
                check_name="check_ira_contribution_plausibility",
                severity="warning",
                description=(
                    f"IRA statement shows ${contrib:,.0f} in employee contributions "
                    "but no deduction for IRA contributions was found on Form 1040 "
                    "(adjustments_to_income is zero or missing). "
                    "Traditional IRA contributions may be deductible."
                ),
                fields_involved=["ytd_employee_contributions", "adjustments_to_income"],
                documents_involved=["retirement_ira", "form_1040"],
                suggested_resolution=(
                    "Verify whether the client is eligible for a traditional IRA deduction. "
                    "If eligible, confirm the IRA deduction was taken on Schedule 1."
                ),
                field_a="ytd_employee_contributions",
                source_a="retirement_ira",
                value_a=contrib,
                field_b="adjustments_to_income",
                source_b="form_1040",
                value_b=adj,
            )
        )

    return issues
