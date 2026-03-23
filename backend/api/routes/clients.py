"""Simple client management routes."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.document import Advisor, Client, Document
from backend.models.snapshot import ClientFinancialSnapshotORM
from backend.models.plan import PlanORM

router = APIRouter()


class CreateClientRequest(BaseModel):
    name: str
    advisor_id: str  # Clerk user ID or UUID string
    advisor_name: str = "Advisor"
    advisor_email: str = "advisor@taxwise.app"


@router.post("")
async def create_client(request: CreateClientRequest, db: AsyncSession = Depends(get_db)):
    # Upsert advisor (single-advisor model — create if not exists)
    try:
        advisor_uuid = uuid.UUID(request.advisor_id)
    except ValueError:
        # If not a UUID (e.g., Clerk user ID), generate deterministic UUID
        import hashlib
        advisor_uuid = uuid.UUID(
            hashlib.md5(request.advisor_id.encode()).hexdigest()
        )

    result = await db.execute(select(Advisor).where(Advisor.id == advisor_uuid))
    advisor = result.scalar_one_or_none()
    if advisor is None:
        advisor = Advisor(
            id=advisor_uuid,
            email=request.advisor_email,
            name=request.advisor_name,
        )
        db.add(advisor)
        await db.flush()

    client = Client(advisor_id=advisor_uuid, name=request.name)
    db.add(client)
    await db.commit()
    await db.refresh(client)

    return {
        "client_id": str(client.id),
        "name": client.name,
        "advisor_id": str(client.advisor_id),
        "created_at": client.created_at.isoformat() if client.created_at else None,
    }


@router.get("")
async def list_clients(advisor_id: str, db: AsyncSession = Depends(get_db)):
    try:
        advisor_uuid = uuid.UUID(advisor_id)
    except ValueError:
        import hashlib
        advisor_uuid = uuid.UUID(hashlib.md5(advisor_id.encode()).hexdigest())

    result = await db.execute(
        select(Client)
        .where(Client.advisor_id == advisor_uuid)
        .order_by(Client.created_at.desc())
    )
    clients = result.scalars().all()

    client_list = []
    for client in clients:
        # Count documents
        doc_result = await db.execute(
            select(Document).where(Document.client_id == client.id)
        )
        docs = doc_result.scalars().all()
        doc_count = len(docs)

        # Get plan status from latest plan
        plan_result = await db.execute(
            select(PlanORM)
            .join(ClientFinancialSnapshotORM, PlanORM.snapshot_id == ClientFinancialSnapshotORM.id)
            .where(ClientFinancialSnapshotORM.client_id == client.id)
            .order_by(PlanORM.created_at.desc())
            .limit(1)
        )
        latest_plan = plan_result.scalar_one_or_none()

        plan_status = "no_plan"
        if latest_plan:
            plan_status = latest_plan.plan_status

        client_list.append({
            "client_id": str(client.id),
            "name": client.name,
            "document_count": doc_count,
            "plan_status": plan_status,
            "created_at": client.created_at.isoformat() if client.created_at else None,
        })

    return client_list


@router.get("/{client_id}")
async def get_client(client_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    return {
        "client_id": str(client.id),
        "name": client.name,
        "advisor_id": str(client.advisor_id),
        "created_at": client.created_at.isoformat() if client.created_at else None,
    }
