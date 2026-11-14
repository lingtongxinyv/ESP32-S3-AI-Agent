from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .database import get_db
from .models import Memory, User
from .security import get_current_user

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


class MemoryResponse(BaseModel):
    id: int
    fact: str
    source: str
    created_at: datetime


class ItemsResponse(BaseModel):
    items: list[MemoryResponse]


@router.get("", response_model=ItemsResponse)
def list_memories(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db=Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按 user_id 硬过滤，仅返回当前用户的记忆。"""
    rows = db.execute(
        select(Memory)
        .where(Memory.user_id == user.id)
        .order_by(Memory.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    return ItemsResponse(items=[
        MemoryResponse(
            id=row.id,
            fact=row.fact,
            source=row.source,
            created_at=row.created_at,
        )
        for row in rows
    ])


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    memory_id: int,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        select(Memory).where(Memory.id == memory_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在")
    if row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权删除")
    db.delete(row)
    db.commit()
    return None
