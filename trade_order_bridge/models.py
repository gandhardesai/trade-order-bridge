from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_order_bridge.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeSettings(Base):
    __tablename__ = "runtime_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    execution_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    transmit_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_mode: Mapped[str] = mapped_column(String(32), default="safe_test")
    allowed_order_types: Mapped[str] = mapped_column(String(255), default="limit,stop,stop_limit")
    symbol_allowlist: Mapped[str] = mapped_column(Text, default="")
    max_quantity: Mapped[float] = mapped_column(Float, default=100.0)
    max_notional: Mapped[float] = mapped_column(Float, default=50000.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WebhookKey(Base):
    __tablename__ = "webhook_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(32), default="tradingview")
    broker: Mapped[str] = mapped_column(String(32), default="ibkr")
    key_prefix: Mapped[str] = mapped_column(String(16))
    key_salt: Mapped[str] = mapped_column(String(64))
    key_hash: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("source_platform", "broker", "idempotency_key", name="uq_source_broker_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_platform: Mapped[str] = mapped_column(String(32), default="tradingview")
    broker: Mapped[str] = mapped_column(String(32), default="ibkr")
    symbol: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float] = mapped_column(Float)
    quantity_type: Mapped[str] = mapped_column(String(32), default="fixed")
    order_type: Mapped[str] = mapped_column(String(32), default="market")
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    transmit: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_mode: Mapped[str] = mapped_column(String(32), default="safe_test")
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["OrderEvent"]] = relationship(back_populates="order", cascade="all,delete-orphan")
    submissions: Mapped[list["BrokerSubmission"]] = relationship(back_populates="order", cascade="all,delete-orphan")


class OrderEvent(Base):
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"))
    event_type: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="events")


class BrokerSubmission(Base):
    __tablename__ = "broker_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"))
    broker_order_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="submissions")
