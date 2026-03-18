# TaxWise Advisor

B2B SaaS platform for financial advisors. Ingests uploaded financial documents (PDFs),
extracts structured data via Claude AI, runs a multi-step tax optimization reasoning engine,
and produces prioritized Roth conversion and tax-loss harvesting plans with client-ready reports.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment variables
cp .env.example .env
# Fill in ANTHROPIC_API_KEY and DATABASE_URL at minimum

# Run DB migrations
alembic upgrade head

# Start the API server
uvicorn backend.main:app --reload
```

## Health Check

```
GET /health → {"status": "ok"}
```

## Project Structure

See `docs/ARCHITECTURE.md` for the full system design and `docs/TAX_LOGIC.md` for CPA-reviewed tax rules.

## Development Phases

| Phase | Scope |
|---|---|
| Phase 1 | FastAPI skeleton + PostgreSQL models + 1040 extractor + confidence gate |
| Phase 2 | Brokerage, IRA, 401k extractors + cross-document validation |
| Phase 3 | Snapshot assembly + gate UI + income table API |
| Phase 4 | Reasoning engine (all 4 steps) + CPA review |
| Phase 5 | Report generator + full advisor UI |
| Phase 6 | Pilot with 3-5 advisor firms |

## Running Tests

```bash
pytest backend/tests/
```
