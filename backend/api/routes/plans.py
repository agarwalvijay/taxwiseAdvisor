"""Plan API routes."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db, AsyncSessionLocal
from backend.gates.confidence_gate import can_generate_plan
from backend.models.plan import PlanORM
from backend.models.snapshot import ClientFinancialSnapshotORM
from backend.reasoning.orchestrator import PlanOrchestrator, PlanGenerationError

router = APIRouter()


@router.post("/{client_id}/generate")
async def generate_plan(
    client_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Trigger plan generation. Returns immediately with status='generating'."""
    gate = await can_generate_plan(str(client_id), db)
    if not gate["allowed"]:
        raise HTTPException(status_code=422, detail={"blocking_reason": gate["reason"]})

    async def _run():
        async with AsyncSessionLocal() as bg_db:
            try:
                orchestrator = PlanOrchestrator()
                await orchestrator.generate_plan(client_id, bg_db)
            except PlanGenerationError:
                pass  # error already persisted to plan record

    background_tasks.add_task(_run)

    return {
        "status": "generating",
        "message": "Plan generation started",
    }


@router.get("/{client_id}/latest")
async def get_latest_plan(client_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Return the most recent plan with all step outputs."""
    result = await db.execute(
        select(PlanORM)
        .join(ClientFinancialSnapshotORM, PlanORM.snapshot_id == ClientFinancialSnapshotORM.id)
        .where(ClientFinancialSnapshotORM.client_id == client_id)
        .order_by(PlanORM.created_at.desc())
        .limit(1)
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="No plan found for this client.")

    return {
        "plan_id": str(plan.id),
        "status": plan.plan_status,
        "step_outputs": plan.step_outputs,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


@router.get("/{plan_id}/status")
async def get_plan_status(plan_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PlanORM).where(PlanORM.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    completed_at = None
    if plan.plan_status == "complete" and plan.created_at:
        completed_at = plan.created_at.isoformat()

    step_map = {
        "step_1_complete": 1,
        "step_2_complete": 2,
        "step_3_complete": 3,
        "step_4_complete": 4,
        "complete": 4,
        "failed": None,
        "generating": 0,
        "queued": 0,
    }
    current_step = step_map.get(plan.plan_status, 0)

    return {
        "plan_id": str(plan.id),
        "status": plan.plan_status,
        "current_step": current_step,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "completed_at": completed_at,
    }


@router.get("/{plan_id}/step/{step_number}")
async def get_plan_step(
    plan_id: uuid.UUID, step_number: int, db: AsyncSession = Depends(get_db)
):
    if step_number not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="step_number must be 1, 2, 3, or 4.")
    result = await db.execute(select(PlanORM).where(PlanORM.id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    key = f"step_{step_number}"
    step_output = plan.step_outputs.get(key)
    if step_output is None:
        raise HTTPException(
            status_code=404, detail=f"Step {step_number} output not yet available."
        )

    return {"plan_id": str(plan.id), "step": step_number, "output": step_output}
