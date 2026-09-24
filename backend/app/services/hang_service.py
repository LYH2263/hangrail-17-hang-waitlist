"""上杆/候挂编排：现网 First-Fit 占位、候挂队列入队与按序消化。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.models import HangRail, RailPlacement, WaitQueueEntry, WorkOrder
from app.services.rail_engine import Segment, first_fit

HANGABLE_STATUSES = ("ready", "overdue", "waiting")


def try_place(db: Session, order: WorkOrder, rail_id: int | None = None) -> RailPlacement | None:
    """对该工单所属门店的挂杆跑 First-Fit；成功则占位并置 hung，同时确保出队。

    不写候挂队列、不 commit，由调用方决定后续动作与事务边界。
    """
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
        # 占位成功必须出队（若曾在候挂队列中），保证队列里不留已上杆工单
        db.execute(delete(WaitQueueEntry).where(WaitQueueEntry.order_id == order.id))
        return placement
    return None


def enqueue(db: Session, order: WorkOrder) -> WaitQueueEntry:
    """把工单写入候挂队列；同一工单不得重复入队（已在队则原样返回）。"""
    existing = db.scalar(select(WaitQueueEntry).where(WaitQueueEntry.order_id == order.id))
    if existing is not None:
        return existing
    entry = WaitQueueEntry(order_id=order.id, enqueued_at=datetime.utcnow())
    db.add(entry)
    order.status = "waiting"
    return entry


def drain_queue(db: Session) -> tuple[list[WorkOrder], int | None, int]:
    """按入队顺序消化候挂队列。

    对队头工单再跑现网 First-Fit：成功则出队并占位，继续处理新队头；
    失败则停在队头、不跳过后单。返回 (已上杆工单, 阻塞队头工单id, 剩余队列长度)。
    """
    hung: list[WorkOrder] = []
    blocked_order_id: int | None = None
    while True:
        head = db.scalars(select(WaitQueueEntry).order_by(WaitQueueEntry.id).limit(1)).first()
        if head is None:
            break
        order = db.get(WorkOrder, head.order_id)
        if order is None or order.status not in HANGABLE_STATUSES:
            db.delete(head)  # 清理死条目，避免永远堵住队列
            continue
        if try_place(db, order) is None:
            blocked_order_id = order.id
            break
        hung.append(order)
    remaining = len(db.scalars(select(WaitQueueEntry.id)).all())
    return hung, blocked_order_id, remaining
