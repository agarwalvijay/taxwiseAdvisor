from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import documents, plans, snapshots, reports, clients

app = FastAPI(
    title="TaxWise Advisor API",
    description="B2B SaaS platform for financial advisors — tax optimization engine",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten per environment in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(snapshots.router, prefix="/api/snapshots", tags=["snapshots"])
app.include_router(plans.router, prefix="/api/plans", tags=["plans"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(clients.router, prefix="/api/clients", tags=["clients"])


@app.get("/health")
async def health():
    return {"status": "ok"}
