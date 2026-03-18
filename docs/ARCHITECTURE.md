# TaxWise Advisor — System Architecture

## Overview

TaxWise Advisor is a B2B SaaS platform for financial advisors. It ingests uploaded
financial documents (PDFs), extracts structured data, reasons over multi-year tax
trajectories, and produces prioritized tax optimization plans (Roth conversion strategy,
tax-loss harvesting) with client-ready PDF reports.

**Positioning:** Software tool, not a financial advisor. Advisors retain full professional
responsibility. The system surfaces analysis; the advisor reviews, approves, and advises.

**V1 Scope:** Single-advisor account model (one account = one advisor). No multi-tenancy,
no client portal, no live account connectivity.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend API | Python 3.12 / FastAPI | Async, Pydantic v2 for models |
| AI Extraction & Reasoning | Anthropic Claude (`claude-sonnet-4-5`) | Structured output via tool use |
| PDF Text Extraction | PyMuPDF (`fitz`) | Raw text layer before Claude sees it |
| Database | PostgreSQL + JSONB | Relational + flexible schema for snapshots |
| Document Storage | Amazon S3 | Raw PDFs stored permanently |
| Frontend | Next.js 14 (App Router, TypeScript) | |
| Authentication | Clerk | Single-advisor model |
| PDF Report Output | WeasyPrint | HTML/CSS template → PDF |
| Deployment | Railway or Render (initial) | Easy migration to AWS later |
| Prompt Management | `/prompts/*.txt` files | Versioned separately from code |

---

## Repository Structure

```
taxwiseAdvisor/
├── backend/
│   ├── main.py                        # FastAPI app entry point
│   ├── config.py                      # Settings (env vars, thresholds)
│   ├── database.py                    # DB connection, session management
│   ├── api/
│   │   └── routes/
│   │       ├── documents.py           # Upload, classify, extract
│   │       ├── snapshots.py           # Snapshot CRUD, gate status
│   │       ├── plans.py               # Trigger reasoning, retrieve plan
│   │       └── reports.py            # Generate PDF
│   ├── extraction/
│   │   ├── classifier.py              # Document type detection
│   │   ├── validator.py               # Cross-document consistency checks
│   │   └── extractors/
│   │       ├── base.py                # Base extractor class
│   │       ├── form_1040.py           # IRS Form 1040
│   │       ├── brokerage.py           # Brokerage statements
│   │       ├── retirement_account.py  # 401k, IRA statements
│   │       └── w2.py                  # W-2 forms
│   ├── gates/
│   │   └── confidence_gate.py         # Gate logic and threshold config
│   ├── reasoning/
│   │   ├── tax_trajectory.py          # Step 1: bracket + window analysis
│   │   ├── conversion_optimizer.py    # Step 2: year-by-year Roth plan
│   │   ├── tlh_advisor.py             # Step 3: TLH + asset location
│   │   └── plan_synthesizer.py        # Step 4: final prioritized plan
│   ├── models/
│   │   ├── document.py                # Document, ExtractionResult
│   │   ├── snapshot.py                # ClientFinancialSnapshot
│   │   ├── plan.py                    # Plan, ReasoningStep
│   │   └── report.py                  # Report
│   ├── reports/
│   │   ├── generator.py               # WeasyPrint PDF generation
│   │   └── templates/
│   │       └── report.html            # Jinja2 HTML template
│   └── tests/
│       ├── fixtures/                  # Synthetic test documents
│       ├── test_extraction.py
│       ├── test_reasoning.py
│       └── test_gates.py
├── frontend/
│   ├── app/
│   │   ├── clients/                   # Client list
│   │   ├── upload/                    # Document upload flow
│   │   ├── review/                    # Confidence gate review screens
│   │   ├── income-table/              # Income projection input
│   │   └── plan/                      # Plan viewer + report download
│   └── components/
├── prompts/
│   ├── classify.txt                   # Document classification prompt
│   ├── extract_1040.txt               # 1040 extraction prompt + schema
│   ├── extract_brokerage.txt          # Brokerage extraction prompt + schema
│   ├── extract_retirement.txt         # 401k/IRA extraction prompt + schema
│   ├── tax_trajectory.txt             # Step 1 reasoning prompt
│   ├── conversion_optimizer.txt       # Step 2 reasoning prompt
│   ├── tlh_advisor.txt                # Step 3 reasoning prompt
│   └── plan_synthesizer.txt           # Step 4 reasoning prompt
├── docs/
│   ├── ARCHITECTURE.md                # This file
│   └── TAX_LOGIC.md                   # CPA-reviewed tax rules
├── migrations/                        # Alembic DB migrations
├── .env.example
├── requirements.txt
└── README.md
```

---

## Data Flow

### Ingestion Phase

```
PDF Upload
    │
    ▼
Document Classification
    │  confidence >= 0.90? → proceed
    │  confidence < 0.90?  → reject with explanation
    ▼
Type-Specific Extraction (per document)
    │  field-level confidence scores assigned
    ▼
CONFIDENCE GATE
    │  all Hard Required fields >= 0.85? → proceed
    │  any field < 0.85?                 → halt, advisor review screen
    ▼
Advisor Review Screen (explicit confirmation per flagged field)
    ▼
Cross-Document Validation (deterministic consistency checks)
    │  all checks pass?          → proceed
    │  contradictions detected?  → halt, advisor resolves each one
    ▼
VALIDATION GATE (all contradictions resolved)
    ▼
Snapshot Assembly
    ▼
SNAPSHOT GATE (all Hard Required snapshot fields present)
    ▼
Advisor Income Projection Table (manual entry)
    ▼
→ Ingestion complete. Reasoning engine unlocked.
```

### Reasoning Phase

```
Snapshot (locked)
    │
    ▼
Step 1: Tax Trajectory Analyzer
    │  → current bracket, retirement bracket, RMD bracket
    │  → IRMAA risk flag
    │  → conversion window years identified
    ▼
Step 2: Conversion Optimizer
    │  → year-by-year conversion amounts
    │  → bracket fill strategy
    │  → IRMAA / ACA cliff checks
    │  → estimated tax per year
    ▼
Step 3: TLH & Asset Location Advisor
    │  → specific securities to harvest (if cost basis available)
    │  → wash-sale risk flags
    │  → asset location moves recommended
    ▼
Step 4: Plan Synthesizer
    │  → prioritized action list
    │  → plain-English rationale per recommendation
    │  → confidence flags on each item
    ▼
→ Plan stored. Report generation unlocked.
```

### Output Phase

```
Plan (all 4 steps complete)
    │
    ▼
Report Generator (WeasyPrint)
    │
    ▼
Client-Ready PDF
```

---

## Core Data Models

### ClientFinancialSnapshot

The canonical structured representation of a client's financial picture.
Assembled from extracted documents + advisor-entered data.
Single source of truth for the reasoning engine.

```python
{
  "client_id": "uuid",
  "snapshot_date": "2026-03-17",
  "personal": {
    "age": 54,
    "spouse_age": 51,                    # null if single
    "filing_status": "married_filing_jointly",
    "state": "IL",
    "retirement_target_age": 62,
    "spouse_retirement_target_age": 60   # null if single
  },
  "income": {
    "current_year_agi": 271000,
    "projections": [
      {
        "year": 2026,
        "estimated_income": 295000,
        "notes": "current year",
        "source": "advisor_input"
      },
      {
        "year": 2027,
        "estimated_income": 0,
        "notes": "planned sabbatical",
        "source": "advisor_input"
      }
    ],
    "social_security": {
      "start_age": 70,
      "monthly_benefit_estimate": 3520
    }
  },
  "accounts": {
    "taxable_brokerage": [
      {
        "institution": "Fidelity",
        "total_value": 487250,
        "cash_balance": 12400,
        "holdings": [
          {
            "symbol": "VTI",
            "description": "Vanguard Total Stock Market ETF",
            "shares": 142.5,
            "price_per_share": 245.10,
            "market_value": 34927,
            "cost_basis": 28400,         # null if not available
            "unrealized_gain_loss": 6527,
            "holding_period": "long_term" # "short_term" | "long_term" | "unknown"
          }
        ]
      }
    ],
    "traditional_401k": [
      { "institution": "Fidelity", "balance": 842000, "employer": "Acme Corp" }
    ],
    "roth_401k": [],
    "traditional_ira": [
      { "institution": "Vanguard", "balance": 215000, "basis": 0 }
    ],
    "roth_ira": [
      { "institution": "Vanguard", "balance": 88000 }
    ],
    "hsa": [
      { "institution": "HealthEquity", "balance": 24000 }
    ],
    "cash_savings": 65000
  },
  "tax_profile": {
    "current_marginal_bracket": 0.32,
    "current_agi": 271000,
    "ltcg_rate": 0.15,
    "state_income_tax_rate": 0.0495,
    "irmaa_exposure": false,
    "irmaa_tier1_threshold_mfj": 212000,
    "irmaa_buffer": 59000,
    "niit_exposure": true,
    "niit_threshold_mfj": 250000,
    "aca_relevant": false               # true if pre-Medicare and no employer coverage
  },
  "rmd_profile": {
    "rmd_start_age": 73,
    "years_until_rmd": 19,
    "projected_pretax_balance_at_rmd": 2100000,
    "projected_first_rmd": 76642
  },
  "data_provenance": {
    "source_documents": ["1040_2024.pdf", "fidelity_dec2025.pdf"],
    "advisor_overrides": [
      {
        "field": "cost_basis",
        "original_extracted": 284000,
        "advisor_confirmed": 291000,
        "timestamp": "2026-03-17T14:22:00Z"
      }
    ],
    "low_confidence_fields": ["cost_basis"],
    "missing_soft_required": ["ssa_benefit_estimate"],
    "snapshot_version": 1,
    "created_at": "2026-03-17T14:25:00Z"
  }
}
```

---

## Confidence Gate System

### Field Classification

**Hard Required** — blocks plan generation if below threshold:
- Filing status
- Client age
- Current year AGI
- Retirement target age
- Total pre-tax retirement balance (401k + traditional IRA)
- Total Roth balance
- Total taxable brokerage balance
- State of residence

**Soft Required** — plan generates but affected section is flagged incomplete:
- Cost basis on taxable holdings (TLH section grayed out)
- Social Security benefit estimate (conversion window less precise)
- Income projections beyond current year (advisor enters manually)
- HSA balance (asset location section incomplete)

**Optional** — enriches plan, absence noted in provenance:
- Lot-level cost basis
- Pension / defined benefit details
- Prior year tax returns
- RMD worksheets

### Thresholds

| Gate | Threshold | Behavior Below Threshold |
|---|---|---|
| Document classification | 0.90 | Reject document; explain why |
| Hard Required field | 0.85 | Halt; advisor review screen |
| Soft Required field | 0.75 | Proceed; section flagged incomplete |
| Optional field | 0.60 | Include if above; silently omit if below |
| Cross-document consistency | N/A (deterministic) | Halt; surface contradiction |

### Advisor Review Screen Requirements

When a field fails the Hard Required threshold, the UI must show:
- Field name (human-readable)
- Extracted value
- Confidence score
- Specific reason for low confidence (not a generic message)
- Two explicit actions: [Enter corrected value] or [Confirm extracted value is correct]

The advisor cannot proceed past the review screen until every flagged item has an explicit action.

---

## Extraction Pipeline Detail

### Stage 1: Classification

**Input:** Raw PDF (text extracted via PyMuPDF)
**Model:** Claude claude-sonnet-4-5
**Prompt:** `prompts/classify.txt`

**Output schema:**
```json
{
  "document_type": "form_1040 | brokerage_statement | retirement_401k | retirement_ira | w2 | ssa_estimate | unknown",
  "institution": "fidelity | schwab | vanguard | merrill | td_ameritrade | other | null",
  "tax_year": 2024,
  "confidence": 0.95,
  "rejection_reason": null
}
```

If `confidence < 0.90` or `document_type == "unknown"`, reject immediately.
Do not proceed to extraction.

### Stage 2: Extraction

Each document type has a dedicated extractor in `backend/extraction/extractors/`.
Each extractor:
1. Receives the PyMuPDF text output
2. Calls Claude with the type-specific prompt from `prompts/`
3. Parses the structured JSON response
4. Assigns confidence scores per field
5. Returns an `ExtractionResult` with the structured data and confidence map

**Every extractor must:**
- Output field-level confidence scores (not just an overall score)
- Flag fields where the value is inferred vs. explicitly stated in the document
- Return `null` for fields not found (never hallucinate a value)
- Include an `extraction_notes` array for any ambiguities

### Priority Extraction Order

1. **Form 1040** — highest standardization, most data density, build and validate first
2. **Brokerage statements** — most variation by institution; build Fidelity, Schwab, Vanguard variants
3. **Traditional IRA statements**
4. **401(k) statements**
5. **Roth IRA statements**

---

## Reasoning Engine Detail

### Design Rules

1. Each step receives a **targeted slice** of the snapshot — not the entire snapshot
2. Each step outputs **structured JSON + plain-English narrative**
3. Each step's output is **validated deterministically** before passing to the next step
4. Steps are **sequential and dependent** — Step 2 receives Step 1's output as input
5. If a step cannot run due to missing data, it returns a partial output with explicit gaps flagged
6. The reasoning engine **never** modifies the snapshot — it only reads from it

### Step Input/Output Contracts

**Step 1 — Tax Trajectory Analyzer**

Input slice:
```json
{
  "age": 54, "spouse_age": 51,
  "filing_status": "married_filing_jointly",
  "state": "IL",
  "retirement_target_age": 62,
  "income_projections": [...],
  "current_agi": 271000,
  "social_security": { "start_age": 70, "monthly_benefit": 3520 },
  "total_pretax_balance": 1057000,
  "total_roth_balance": 88000,
  "rmd_start_age": 73
}
```

Required output fields:
- `current_bracket` (float)
- `retirement_bracket_estimate` (float)
- `rmd_bracket_estimate` (float)
- `irmaa_risk` (object with `flagged` bool and `reason` string)
- `conversion_window_years` (array of years)
- `conversion_window_rationale` (string)
- `urgency` ("high" | "medium" | "low")
- `confidence` (float)

**Step 2 — Conversion Optimizer**

Input: Step 1 output + account balances + liquidity assessment

Required output fields:
- `conversion_plan` (array of yearly objects)
  - `year`, `convert_amount`, `estimated_tax`, `bracket_used`
  - `post_conversion_agi`, `irmaa_safe` (bool), `aca_safe` (bool)
  - `note` (string — any flags or warnings for this year)
- `total_converted` (float)
- `liquidity_check_passed` (bool)
- `confidence` (float)

**Step 3 — TLH & Asset Location**

Input: Taxable holdings with cost basis data

Required output fields:
- `tlh_opportunities` (array) — only populated if cost basis available
  - `symbol`, `unrealized_loss`, `action`, `suggested_replacement`
  - `wash_sale_risk` (string), `estimated_tax_benefit` (float)
- `asset_location_moves` (array)
  - `asset_description`, `current_location`, `recommended_location`, `rationale`
- `tlh_section_complete` (bool — false if cost basis unavailable)
- `confidence` (float)

**Step 4 — Plan Synthesizer**

Input: Steps 1-3 outputs combined

Required output fields:
- `priority_actions` (array, ordered by impact)
  - `priority` (int), `category` ("roth_conversion" | "tlh" | "asset_location" | "other")
  - `action` (string — specific, actionable), `rationale` (string — plain English)
  - `estimated_benefit` (string), `urgency` ("immediate" | "this_year" | "multi_year")
  - `confidence` ("high" | "medium" | "low")
- `executive_summary` (string — 3-4 sentences for the report cover)
- `key_assumptions` (array of strings)
- `plan_confidence` (float)

---

## Report Structure

The client-ready PDF includes these sections in order:

1. **Executive Summary** — 3-4 sentences, top recommendation
2. **Your Financial Snapshot** — structured picture as of today
3. **Tax Trajectory Analysis** — current/retirement/RMD brackets, IRMAA risk
4. **Roth Conversion Strategy** — year-by-year table + rationale
5. **Tax-Loss Harvesting Opportunities** — specific holdings (if cost basis available)
6. **Asset Location Recommendations** — what should be where and why
7. **Key Assumptions** — what the plan assumes (advisor can caveat)
8. **Next Steps** — concrete action items with urgency flags
9. **Disclaimer** — standard software-tool disclaimer (required on every report)

---

## API Routes

### Documents
- `POST /api/documents/upload` — upload PDF(s) for a client
- `GET /api/documents/{client_id}` — list documents for a client
- `GET /api/documents/{document_id}/extraction` — get extraction result

### Snapshots
- `GET /api/snapshots/{client_id}` — get current snapshot + gate status
- `POST /api/snapshots/{client_id}/confirm-field` — advisor confirms a flagged field
- `POST /api/snapshots/{client_id}/resolve-contradiction` — advisor resolves a contradiction
- `POST /api/snapshots/{client_id}/income-projections` — save income projection table
- `GET /api/snapshots/{client_id}/gate-status` — get current gate state

### Plans
- `POST /api/plans/{client_id}/generate` — trigger reasoning engine (requires all gates clear)
- `GET /api/plans/{client_id}/latest` — get most recent plan
- `GET /api/plans/{plan_id}/step/{step_number}` — get individual reasoning step output

### Reports
- `POST /api/reports/{plan_id}/generate` — generate PDF report
- `GET /api/reports/{report_id}/download` — download PDF

---

## Environment Variables

```bash
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/taxwise

# S3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
S3_BUCKET_NAME=taxwise-documents

# Auth (Clerk)
CLERK_SECRET_KEY=...
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=...

# App
ENVIRONMENT=development
LOG_LEVEL=INFO

# Confidence thresholds (can be tuned without code changes)
CONFIDENCE_THRESHOLD_CLASSIFICATION=0.90
CONFIDENCE_THRESHOLD_HARD_REQUIRED=0.85
CONFIDENCE_THRESHOLD_SOFT_REQUIRED=0.75
CONFIDENCE_THRESHOLD_OPTIONAL=0.60
```

---

## Development Phases

| Phase | Scope | Done When |
|---|---|---|
| **Phase 1** | FastAPI skeleton + PostgreSQL models + 1040 extractor + confidence gate | `GET /health` returns 200; 1040 extractor hits ≥92% accuracy on test corpus |
| **Phase 2** | Brokerage, IRA, 401k extractors + cross-document validation | All document types extract with confidence scores; validation catches injected contradictions |
| **Phase 3** | Snapshot assembly + gate UI + income table API | End-to-end: upload 3 docs, clear gates, fill income table, snapshot assembled |
| **Phase 4** | Reasoning engine (all 4 steps) + CPA review | Tax math validated; conversion plan matches manual calculations within 5% |
| **Phase 5** | Report generator + full advisor UI | Advisor completes full workflow without engineering support |
| **Phase 6** | Pilot with 3-5 advisor firms | NPS ≥ 40 from pilot advisors |

---

## Key Design Decisions (and Why)

**Why sequential reasoning steps instead of one big prompt?**
Each step's output can be validated deterministically before passing forward. Errors don't compound silently. Individual steps can be re-run when the advisor changes an assumption.

**Why store raw PDFs permanently?**
Extraction quality will improve over time. You want to re-extract against improved prompts without asking advisors to re-upload documents.

**Why prompts in separate files, not hardcoded in Python?**
Prompt tuning and code changes have different cadences. Tracking them separately makes it clear what changed and why a result changed.

**Why halt on low confidence rather than degrade gracefully?**
Advisor trust is the product. A wrong number presented confidently destroys trust permanently. A clear "we need more information" builds trust.

**Why no client portal in V1?**
Reduces scope by 40%. Advisors handle client communication — that's their job. Build the tool advisors need first.
