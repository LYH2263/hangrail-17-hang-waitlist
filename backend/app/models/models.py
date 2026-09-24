from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    rails: Mapped[list["HangRail"]] = relationship(back_populates="store")


class HangRail(Base):
    __tablename__ = "hang_rails"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    label: Mapped[str] = mapped_column(String(40))
    length_cm: Mapped[float] = mapped_column(Float)
    store: Mapped[Store] = relationship(back_populates="rails")
    placements: Mapped[list["RailPlacement"]] = relationship(back_populates="rail")


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    ticket_code: Mapped[str] = mapped_column(String(40), unique=True)
    garment_name: Mapped[str] = mapped_column(String(80))
    length_cm: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready/waiting/hung/picked/overdue
    due_at: Mapped[datetime] = mapped_column(DateTime)
    hung_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RailPlacement(Base):
    __tablename__ = "rail_placements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rail_id: Mapped[int] = mapped_column(ForeignKey("hang_rails.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"))
    start_cm: Mapped[float] = mapped_column(Float)
    end_cm: Mapped[float] = mapped_column(Float)
    active: Mapped[int] = mapped_column(Integer, default=1)
    rail: Mapped[HangRail] = relationship(back_populates="placements")


class WaitQueueEntry(Base):
    """候挂队列条目：上杆空间不足时排队，按入队顺序(id 升序)消化。"""

    __tablename__ = "wait_queue_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("work_orders.id"), unique=True)  # 同一工单不得重复入队
    enqueued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    order: Mapped[WorkOrder] = relationship()
