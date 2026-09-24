"""候挂队列测例：入队不占位 / 消化成功出队占位 / 队头失败不跳过后单 / 不重复入队。"""

from datetime import datetime, timedelta

from app.models.models import HangRail, RailPlacement, Store, WaitQueueEntry, WorkOrder


def make_store_with_rail(db, length_cm=100):
    store = Store(name="测试店")
    db.add(store)
    db.flush()
    rail = HangRail(store_id=store.id, label="A 杆", length_cm=length_cm)
    db.add(rail)
    db.flush()
    return store, rail


def make_order(db, store, ticket, length_cm, status="ready"):
    order = WorkOrder(
        store_id=store.id,
        ticket_code=ticket,
        garment_name=f"衣物-{ticket}",
        length_cm=length_cm,
        status=status,
        due_at=datetime.utcnow() + timedelta(days=1),
    )
    db.add(order)
    db.flush()
    return order


def occupy(db, rail, order, start_cm, end_cm):
    db.add(RailPlacement(rail_id=rail.id, order_id=order.id, start_cm=start_cm, end_cm=end_cm))
    db.flush()


def queue_ids(db):
    return [e.order_id for e in db.query(WaitQueueEntry).order_by(WaitQueueEntry.id).all()]


def active_segments(db, rail):
    rows = db.query(RailPlacement).filter_by(rail_id=rail.id, active=1).all()
    return sorted((p.start_cm, p.end_cm) for p in rows)


def test_enqueue_does_not_occupy(client, db_session):
    store, rail = make_store_with_rail(db_session, 100)
    blocker = make_order(db_session, store, "HR-001", 100, status="hung")
    occupy(db_session, rail, blocker, 0, 100)  # 杆已满
    order = make_order(db_session, store, "HR-002", 30)
    db_session.commit()

    res = client.post("/api/hang", json={"order_id": order.id})
    assert res.status_code == 200
    assert res.json()["status"] == "waiting"

    # 已入队，但未产生任何新占位
    assert queue_ids(db_session) == [order.id]
    assert active_segments(db_session, rail) == [(0, 100)]
    db_session.refresh(order)
    assert order.status == "waiting"
    assert order.hung_at is None


def test_drain_success_dequeues_and_occupies(client, db_session):
    store, rail = make_store_with_rail(db_session, 100)
    blocker = make_order(db_session, store, "HR-001", 100, status="hung")
    occupy(db_session, rail, blocker, 0, 100)
    order = make_order(db_session, store, "HR-002", 40)
    db_session.commit()

    client.post("/api/hang", json={"order_id": order.id})
    assert queue_ids(db_session) == [order.id]

    # 取走占位工单，腾出整杆空间
    res = client.post("/api/pickup", json={"ticket_code": "HR-001"})
    assert res.status_code == 200
    assert active_segments(db_session, rail) == []

    res = client.post("/api/queue/drain")
    assert res.status_code == 200
    body = res.json()
    assert [o["ticket_code"] for o in body["hung"]] == ["HR-002"]
    assert body["blocked_order_id"] is None
    assert body["remaining"] == 0

    # 出队 + 占位 + 状态落库
    assert queue_ids(db_session) == []
    assert active_segments(db_session, rail) == [(0, 40)]
    db_session.refresh(order)
    assert order.status == "hung"
    assert order.hung_at is not None


def test_drain_head_failure_does_not_skip(client, db_session):
    store, rail = make_store_with_rail(db_session, 100)
    blocker = make_order(db_session, store, "HR-001", 100, status="hung")
    occupy(db_session, rail, blocker, 0, 100)
    big = make_order(db_session, store, "HR-002", 50)   # 队头：需要 50cm
    small = make_order(db_session, store, "HR-003", 30)  # 后单：只需 30cm
    db_session.commit()

    client.post("/api/hang", json={"order_id": big.id})
    client.post("/api/hang", json={"order_id": small.id})
    assert queue_ids(db_session) == [big.id, small.id]

    # 腾出 40cm：够后单、不够队头
    placement = db_session.query(RailPlacement).filter_by(order_id=blocker.id).one()
    placement.end_cm = 60
    db_session.commit()

    res = client.post("/api/queue/drain")
    assert res.status_code == 200
    body = res.json()
    assert body["hung"] == []
    assert body["blocked_order_id"] == big.id
    assert body["remaining"] == 2

    # 队头失败即停：后单不被跳过消化，队列顺序不变，无新占位
    assert queue_ids(db_session) == [big.id, small.id]
    assert active_segments(db_session, rail) == [(0, 60)]
    db_session.refresh(small)
    assert small.status == "waiting"


def test_same_order_not_enqueued_twice(client, db_session):
    store, rail = make_store_with_rail(db_session, 100)
    blocker = make_order(db_session, store, "HR-001", 100, status="hung")
    occupy(db_session, rail, blocker, 0, 100)
    order = make_order(db_session, store, "HR-002", 30)
    db_session.commit()

    for _ in range(3):
        res = client.post("/api/hang", json={"order_id": order.id})
        assert res.status_code == 200
        assert res.json()["status"] == "waiting"

    assert queue_ids(db_session) == [order.id]
    assert client.get("/api/queue").json()[0]["ticket_code"] == "HR-002"
    assert len(client.get("/api/queue").json()) == 1
