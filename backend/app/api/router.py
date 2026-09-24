from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import HangRail, RailPlacement, Store, WorkOrder
from app.schemas.schemas import (
    DrainOut,
    HangRequest,
    OccupancyOut,
    OccupancySeg,
    OrderOut,
    PickupRequest,
    QueueEntryOut,
    RailOut,
    StoreOut,
)
from app.services.hang_service import attempt_hang, drain, enqueue, queue_entries

api_router = APIRouter()


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/stores", response_model=list[StoreOut])
def stores(db: Session = Depends(get_db)):
    return db.scalars(select(Store).order_by(Store.id)).all()


@api_router.get("/rails", response_model=list[RailOut])
def rails(db: Session = Depends(get_db)):
    return db.scalars(select(HangRail).order_by(HangRail.id)).all()


@api_router.get("/orders", response_model=list[OrderOut])
def orders(db: Session = Depends(get_db)):
    return db.scalars(select(WorkOrder).order_by(WorkOrder.id.desc())).all()


@api_router.get("/occupancy/{rail_id}", response_model=OccupancyOut)
def occupancy(rail_id: int, db: Session = Depends(get_db)):
    rail = db.get(HangRail, rail_id)
    if not rail:
        raise HTTPException(404, "挂杆不存在")
    placements = db.scalars(
        select(RailPlacement).where(RailPlacement.rail_id == rail_id, RailPlacement.active == 1)
    ).all()
    segs = []
    for p in placements:
        order = db.get(WorkOrder, p.order_id)
        if not order:
            continue
        segs.append(
            OccupancySeg(
                order_id=order.id,
                ticket_code=order.ticket_code,
                garment_name=order.garment_name,
                start_cm=p.start_cm,
                end_cm=p.end_cm,
            )
        )
    segs.sort(key=lambda s: s.start_cm)
    return OccupancyOut(rail_id=rail.id, label=rail.label, length_cm=rail.length_cm, segments=segs)


@api_router.post("/hang", response_model=OrderOut)
def hang(body: HangRequest, db: Session = Depends(get_db)):
    order = db.get(WorkOrder, body.order_id)
    if not order:
        raise HTTPException(404, "工单不存在")
    if order.status not in ("ready", "overdue"):
        raise HTTPException(400, "工单状态不可上杆")
    rail_q = select(HangRail.id).where(HangRail.store_id == order.store_id)
    if body.rail_id:
        rail_q = rail_q.where(HangRail.id == body.rail_id)
    if not db.scalar(rail_q.limit(1)):
        raise HTTPException(404, "无可用挂杆")
    if attempt_hang(db, order, body.rail_id) is None:
        # 空间不足:写入候挂队列(不重复入队),而不是仅返回错误
        enqueue(db, order)
    db.commit()
    db.refresh(order)
    return order


@api_router.get("/queue", response_model=list[QueueEntryOut])
def queue_list(db: Session = Depends(get_db)):
    out = []
    for entry in queue_entries(db):
        order = db.get(WorkOrder, entry.order_id)
        if not order:
            continue
        out.append(
            QueueEntryOut(
                id=entry.id,
                order_id=order.id,
                ticket_code=order.ticket_code,
                garment_name=order.garment_name,
                length_cm=order.length_cm,
                enqueued_at=entry.enqueued_at,
            )
        )
    return out


@api_router.post("/queue/drain", response_model=DrainOut)
def queue_drain(db: Session = Depends(get_db)):
    hung = drain(db)
    db.commit()
    for order in hung:
        db.refresh(order)
    return DrainOut(hung=hung, remaining=queue_list(db))


@api_router.post("/pickup", response_model=OrderOut)
def pickup(body: PickupRequest, db: Session = Depends(get_db)):
    order = db.scalar(select(WorkOrder).where(WorkOrder.ticket_code == body.ticket_code))
    if not order:
        raise HTTPException(404, "取件码无效")
    if order.status != "hung":
        raise HTTPException(400, "工单未在挂杆上")
    placements = db.scalars(
        select(RailPlacement).where(RailPlacement.order_id == order.id, RailPlacement.active == 1)
    ).all()
    for p in placements:
        p.active = 0
    order.status = "picked"
    db.commit()
    db.refresh(order)
    return order


@api_router.post("/overdue/scan", response_model=list[OrderOut])
def overdue_scan(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    hung = db.scalars(select(WorkOrder).where(WorkOrder.status == "hung")).all()
    marked = []
    for o in hung:
        if o.due_at < now:
            o.status = "overdue"
            marked.append(o)
    ready = db.scalars(select(WorkOrder).where(WorkOrder.status == "ready")).all()
    for o in ready:
        if o.due_at < now:
            o.status = "overdue"
            marked.append(o)
    db.commit()
    return marked


@api_router.get("/overdue", response_model=list[OrderOut])
def overdue_list(db: Session = Depends(get_db)):
    return db.scalars(select(WorkOrder).where(WorkOrder.status == "overdue").order_by(WorkOrder.due_at)).all()
