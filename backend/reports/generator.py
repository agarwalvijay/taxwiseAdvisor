"""
PDF Report Generator using WeasyPrint + Jinja2.
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.plan import PlanORM
from backend.models.report import ReportORM
from backend.models.snapshot import ClientFinancialSnapshotORM
from backend.models.document import Client

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_LOCAL_REPORTS_DIR = Path("/tmp/taxwise_reports")

_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)


class ReportGenerationError(Exception):
    pass


class ReportGenerator:
    async def generate(
        self,
        plan_id: uuid.UUID,
        advisor_name: str,
        client_name: str,
        db: AsyncSession,
    ) -> bytes:
        """
        Load Plan from DB, render HTML template, convert to PDF via WeasyPrint.
        Returns PDF bytes.
        Raises ReportGenerationError if plan status != "complete".
        """
        # Load plan
        result = await db.execute(select(PlanORM).where(PlanORM.id == plan_id))
        plan = result.scalar_one_or_none()
        if plan is None:
            raise ReportGenerationError(f"Plan {plan_id} not found.")
        if plan.plan_status != "complete":
            raise ReportGenerationError(
                f"Plan is not complete (status: {plan.plan_status}). Cannot generate report."
            )

        # Load snapshot
        result = await db.execute(
            select(ClientFinancialSnapshotORM).where(
                ClientFinancialSnapshotORM.id == plan.snapshot_id
            )
        )
        snapshot_orm = result.scalar_one_or_none()
        snapshot_data = snapshot_orm.snapshot_data if snapshot_orm else {}

        step_outputs = plan.step_outputs
        try:
            template = _jinja_env.get_template("report.html")
            html = template.render(
                advisor_name=advisor_name,
                client_name=client_name,
                analysis_date=datetime.now(timezone.utc).strftime("%B %d, %Y"),
                snapshot=snapshot_data,
                step1=step_outputs.get("step_1", {}),
                step2=step_outputs.get("step_2", {}),
                step3=step_outputs.get("step_3", {}),
                step4=step_outputs.get("step_4", {}),
            )
        except Exception as exc:
            raise ReportGenerationError(f"Template render failed: {exc}") from exc

        # Convert to PDF
        try:
            import weasyprint
            pdf_bytes = weasyprint.HTML(string=html).write_pdf()
        except Exception as exc:
            raise ReportGenerationError(f"PDF render failed: {exc}") from exc
        return pdf_bytes

    def _local_path(self, plan_id: uuid.UUID, timestamp: str) -> Path:
        _LOCAL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        return _LOCAL_REPORTS_DIR / f"{plan_id}_{timestamp}.pdf"
