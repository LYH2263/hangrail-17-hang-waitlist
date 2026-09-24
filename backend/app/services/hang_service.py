"""候挂队列:自动上杆失败时入队,按入队顺序消化。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import HangRail, PendingQueueEntry, RailPlacement, WorkOrder
from app.services.rail_engine import Segment, first_fit


def attempt_hang(db: Session, order: WorkOrder, rail_id: int | None = None) -> RailPlacement | None:
    """对工单跑现网 First-Fit;成功则占位并置 hung,失败返回 None(不落库)。"""
    rail_q = select(HangRail).where(HangRail.store_id == order.store_id)
    if rail_id is not None:
        rail_q = rail_q.where(HangRail.id == rail_id)
    rails = db.scalars(rail_q.order_by(HangRail.id)).all()
    for rail in rails:
        active = db.scalars(
            select(RailPlacement).where(RailPlacement.rail_id == rail.id, RailPlacement.active == 1)
        ).all()
        occupied = [Segment(p.start_cm, p.end_cm) for p in active]
        place = first_fit(rail.length_cm, occupied, order.length_cm)
        if place is None:
            continue
        placement = RailPlacement(
            rail_id=rail.id,
            order_id=order.id,
            start_cm=place.start_cm,
            end_cm=place.end_cm,
        )
        db.add(placement)
        order.status = "hung"
        order.hung_at = datetime.utcnow()
        return placement
    return None


def enqueue(db: Session, order: WorkOrder) -> PendingQueueEntry:
    """写入候挂队列;同一工单已在队则原样返回,不重复入队。"""
    existing = db.scalar(select(PendingQueueEntry).where(PendingQueueEntry.order_id == order.id))
    if existing is not None:
        return existing
    entry = PendingQueueEntry(order_id=order.id, enqueued_at=datetime.utcnow())
    db.add(entry)
    order.status = "queued"
    db.flush()
    return entry


def queue_entries(db: Session) -> list[PendingQueueEntry]:
    """按入队顺序返回候挂队列。"""
    return list(db.scalars(select(PendingQueueEntry).order_by(PendingQueueEntry.id)).all())


def drain(db: Session) -> list[WorkOrder]:
    """按入队顺序消化:队头上杆成功则出队继续,失败则停在队头、不跳过后单。"""
    hung: list[WorkOrder] = []
    while True:
        head = db.scalars(select(PendingQueueEntry).order_by(PendingQueueEntry.id)).first()
        if head is None:
            break
        order = db.get(WorkOrder, head.order_id)
        if order is None or attempt_hang(db, order) is None:
            break
        db.delete(head)
        # autoflush=False:显式 flush 让占位与出队对下一轮查询可见
        db.flush()
        hung.append(order)
    return hung
