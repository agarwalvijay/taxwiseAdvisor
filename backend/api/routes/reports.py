"""Report generation and download routes."""
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.plan import PlanORM
from backend.models.report import ReportORM
from backend.reports.generator import ReportGenerator, ReportGenerationError

router = APIRouter()

_LOCAL_REPORTS_DIR = Path("/tmp/taxwise_reports")


class GenerateReportRequest(BaseModel):
    advisor_name: str
    client_name: str


@router.post("/{plan_id}/generate")
async def generate_report(
    plan_id: uuid.UUID,
    request: GenerateReportRequest,
    db: AsyncSession = Depends(get_db),
):
    generator = ReportGenerator()
    try:
        pdf_bytes = await generator.generate(
            plan_id=plan_id,
            advisor_name=request.advisor_name,
            client_name=request.client_name,
            db=db,
        )
    except ReportGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Store locally (S3 stub for dev)
    _LOCAL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_client = request.client_name.replace(" ", "_").replace("/", "_")
    filename = f"TaxWise_Plan_{safe_client}_{timestamp}.pdf"
    local_path = _LOCAL_REPORTS_DIR / filename
    local_path.write_bytes(pdf_bytes)

    # Create Report record
    report = ReportORM(
        plan_id=plan_id,
        s3_key=str(local_path),
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    return {
        "report_id": str(report.id),
        "plan_id": str(plan_id),
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "download_url": f"/api/reports/{report.id}/download",
        "filename": filename,
    }


@router.get("/{report_id}/download")
async def download_report(report_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ReportORM).where(ReportORM.id == report_id))
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found.")

    local_path = Path(report.s3_key)
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found on disk.")

    filename = local_path.name
    return FileResponse(
        path=str(local_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{plan_id}/list")
async def list_reports(plan_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ReportORM)
        .where(ReportORM.plan_id == plan_id)
        .order_by(ReportORM.created_at.desc())
    )
    reports = result.scalars().all()
    return [
        {
            "report_id": str(r.id),
            "plan_id": str(r.plan_id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "download_url": f"/api/reports/{r.id}/download",
        }
        for r in reports
    ]
