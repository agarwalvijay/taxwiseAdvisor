"""
Plan Orchestrator — runs all 4 reasoning steps sequentially and persists results.
"""
import uuid
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from backend.gates.confidence_gate import can_generate_plan
from backend.models.plan import PlanORM
from backend.models.snapshot import ClientFinancialSnapshotORM, ClientFinancialSnapshotSchema
from backend.reasoning.tax_trajectory import TaxTrajectoryAnalyzer, ReasoningStepError
from backend.reasoning.conversion_optimizer import ConversionOptimizer
from backend.reasoning.tlh_advisor import TLHAdvisor
from backend.reasoning.plan_synthesizer import PlanSynthesizer

logger = logging.getLogger(__name__)


class PlanGenerationError(Exception):
    """Raised when plan generation is blocked or a step fails unrecoverably."""


class PlanOrchestrator:
    async def generate_plan(self, client_id: uuid.UUID, db: AsyncSession) -> PlanORM:
        # 1. Gate check
        gate = await can_generate_plan(str(client_id), db)
        if not gate["allowed"]:
            raise PlanGenerationError(gate["reason"])

        # 2. Load snapshot
        result = await db.execute(
            select(ClientFinancialSnapshotORM)
            .where(ClientFinancialSnapshotORM.client_id == client_id)
            .order_by(ClientFinancialSnapshotORM.version.desc())
        )
        snapshot_orm = result.scalar_one_or_none()
        if snapshot_orm is None:
            raise PlanGenerationError("Snapshot not found.")

        snapshot = ClientFinancialSnapshotSchema(**snapshot_orm.snapshot_data)

        # 3. Create Plan record — commit immediately so getLatest can find it
        plan = PlanORM(
            snapshot_id=snapshot_orm.id,
            step_outputs={},
            plan_status="generating",
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)

        try:
            # Step 1
            plan.plan_status = "step_1_running"
            await db.commit()
            trajectory = await TaxTrajectoryAnalyzer().run(snapshot)
            plan.step_outputs = {**plan.step_outputs, "step_1": trajectory.model_dump()}
            plan.plan_status = "step_1_complete"
            flag_modified(plan, "step_outputs")
            await db.commit()

            # Step 2
            plan.plan_status = "step_2_running"
            await db.commit()
            conversions = await ConversionOptimizer().run(snapshot, trajectory)
            plan.step_outputs = {**plan.step_outputs, "step_2": conversions.model_dump()}
            plan.plan_status = "step_2_complete"
            flag_modified(plan, "step_outputs")
            await db.commit()

            # Step 3
            plan.plan_status = "step_3_running"
            await db.commit()
            tlh = await TLHAdvisor().run(snapshot)
            plan.step_outputs = {**plan.step_outputs, "step_3": tlh.model_dump()}
            plan.plan_status = "step_3_complete"
            flag_modified(plan, "step_outputs")
            await db.commit()

            # Step 4
            plan.plan_status = "step_4_running"
            await db.commit()
            synthesis = await PlanSynthesizer().run(snapshot, trajectory, conversions, tlh)
            plan.step_outputs = {**plan.step_outputs, "step_4": synthesis.model_dump()}
            plan.plan_status = "complete"
            flag_modified(plan, "step_outputs")
            await db.commit()

        except (ReasoningStepError, Exception) as exc:
            plan.plan_status = "failed"
            error_detail = str(exc)
            plan.step_outputs = {**plan.step_outputs, "error": error_detail}
            flag_modified(plan, "step_outputs")
            await db.commit()
            raise PlanGenerationError(f"Plan generation failed: {error_detail}")

        await db.refresh(plan)
        return plan
