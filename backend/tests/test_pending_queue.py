"""候挂队列:入队不占位、消化出队占位、队头失败不跳过、不重复入队。

用 SQLite 内存库 + dependency_overrides 替换 get_db;TestClient 不进入
with 块,lifespan 不会触发,因此不会连接 Postgres。
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import HangRail, PendingQueueEntry, RailPlacement, Store, WorkOrder
from app.services.hang_service import enqueue

engine = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


def make_store_with_rail(length_cm: float = 100) -> tuple[int, int]:
    db = TestingSession()
    store = Store(name="测试店")
    db.add(store)
    db.flush()
    rail = HangRail(store_id=store.id, label="A 杆", length_cm=length_cm)
    db.add(rail)
    db.commit()
    ids = (store.id, rail.id)
    db.close()
    return ids


def make_order(store_id: int, ticket_code: str, length_cm: float, status: str = "ready") -> int:
    db = TestingSession()
    order = WorkOrder(
        store_id=store_id,
        ticket_code=ticket_code,
        garment_name="测试衣",
        length_cm=length_cm,
        status=status,
        due_at=datetime.utcnow() + timedelta(days=1),
    )
    db.add(order)
    db.commit()
    order_id = order.id
    db.close()
    return order_id


def occupy(rail_id: int, order_id: int, start_cm: float, end_cm: float) -> None:
    db = TestingSession()
    db.add(RailPlacement(rail_id=rail_id, order_id=order_id, start_cm=start_cm, end_cm=end_cm))
    db.commit()
    db.close()


def test_hang_no_space_enqueues_without_placement():
    """空间不足时入候挂队列,不产生占位。"""
    store_id, rail_id = make_store_with_rail(100)
    blocker = make_order(store_id, "T-001", 80, status="hung")
    occupy(rail_id, blocker, 0, 80)  # 仅剩 20cm
    order_id = make_order(store_id, "T-002", 30)

    res = client.post("/api/hang", json={"order_id": order_id})
    assert res.status_code == 200
    assert res.json()["status"] == "queued"

    db = TestingSession()
    assert db.scalars(select(RailPlacement).where(RailPlacement.order_id == order_id)).all() == []
    assert [e.order_id for e in db.scalars(select(PendingQueueEntry)).all()] == [order_id]
    db.close()

    queue = client.get("/api/queue").json()
    assert [e["ticket_code"] for e in queue] == ["T-002"]


def test_drain_success_dequeues_and_occupies():
    """消化成功:出队、占位、状态置 hung。"""
    store_id, rail_id = make_store_with_rail(100)
    blocker = make_order(store_id, "T-001", 80, status="hung")
    occupy(rail_id, blocker, 0, 80)
    order_id = make_order(store_id, "T-002", 30)
    client.post("/api/hang", json={"order_id": order_id})  # 入队

    db = TestingSession()  # 释放空间
    placement = db.scalar(select(RailPlacement).where(RailPlacement.order_id == blocker))
    placement.active = 0
    db.commit()
    db.close()

    res = client.post("/api/queue/drain")
    assert res.status_code == 200
    body = res.json()
    assert [o["ticket_code"] for o in body["hung"]] == ["T-002"]
    assert body["remaining"] == []

    db = TestingSession()
    order = db.get(WorkOrder, order_id)
    assert order.status == "hung"
    placement = db.scalar(
        select(RailPlacement).where(
            RailPlacement.order_id == order_id, RailPlacement.active == 1
        )
    )
    assert placement is not None
    assert placement.end_cm - placement.start_cm == 30
    assert db.scalars(select(PendingQueueEntry)).all() == []
    db.close()


def test_drain_stops_at_failing_head():
    """队头放不下则停住,不跳过它去消化后单。"""
    store_id, rail_id = make_store_with_rail(100)
    blocker = make_order(store_id, "T-001", 80, status="hung")
    occupy(rail_id, blocker, 0, 80)  # 仅剩 20cm
    big = make_order(store_id, "T-002", 30)
    small = make_order(store_id, "T-003", 25)
    client.post("/api/hang", json={"order_id": big})  # 放不下,入队
    client.post("/api/hang", json={"order_id": small})  # 放不下,入队

    db = TestingSession()  # 释放到剩 25cm:队头 30 仍放不下,次单 25 本可放下
    placement = db.scalar(select(RailPlacement).where(RailPlacement.order_id == blocker))
    placement.end_cm = 75
    db.commit()
    db.close()

    res = client.post("/api/queue/drain")
    body = res.json()
    assert body["hung"] == []
    assert [e["ticket_code"] for e in body["remaining"]] == ["T-002", "T-003"]

    db = TestingSession()
    small_order = db.scalar(select(WorkOrder).where(WorkOrder.ticket_code == "T-003"))
    assert small_order.status == "queued"  # 未被跳过尝试
    assert (
        db.scalars(select(RailPlacement).where(RailPlacement.order_id == small_order.id)).all()
        == []
    )
    db.close()


def test_no_duplicate_enqueue():
    """同一工单不得重复入队。"""
    store_id, rail_id = make_store_with_rail(100)
    blocker = make_order(store_id, "T-001", 80, status="hung")
    occupy(rail_id, blocker, 0, 80)
    order_id = make_order(store_id, "T-002", 30)
    client.post("/api/hang", json={"order_id": order_id})

    db = TestingSession()  # 服务层重复入队是幂等的
    order = db.get(WorkOrder, order_id)
    enqueue(db, order)
    enqueue(db, order)
    db.commit()
    assert len(db.scalars(select(PendingQueueEntry)).all()) == 1
    db.close()

    res = client.post("/api/hang", json={"order_id": order_id})  # 候挂中不可再上杆
    assert res.status_code == 400
    assert len(client.get("/api/queue").json()) == 1
