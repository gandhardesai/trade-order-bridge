from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from trade_order_bridge import models
from trade_order_bridge.schemas import TradingViewWebhookRequest
from trade_order_bridge.security import verify_key


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_runtime_settings(db: Session) -> models.RuntimeSettings:
    settings = db.get(models.RuntimeSettings, 1)
    if settings:
        return settings
    settings = models.RuntimeSettings(id=1)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def resolve_order_type(payload: TradingViewWebhookRequest) -> str:
    if payload.limit_price is not None and payload.stop_price is not None:
        return "stop_limit"
    if payload.limit_price is not None:
        return "limit"
    if payload.stop_price is not None:
        return "stop"
    return "market"


def find_active_key(db: Session, raw_key: str, platform: str, broker: str) -> models.WebhookKey | None:
    candidates = (
        db.query(models.WebhookKey)
        .filter(models.WebhookKey.is_active.is_(True))
        .filter(models.WebhookKey.platform == platform)
        .filter(models.WebhookKey.broker == broker)
        .all()
    )
    for key in candidates:
        if verify_key(raw_key, key.key_salt, key.key_hash):
            return key
    return None


def split_csv(csv_values: str) -> list[str]:
    return [value.strip().upper() for value in csv_values.split(",") if value.strip()]


def enforce_runtime_policy(settings: models.RuntimeSettings, payload: TradingViewWebhookRequest, order_type: str) -> tuple[bool, str | None]:
    if not settings.execution_enabled:
        return False, "Execution disabled by operator"

    if payload.quantity > settings.max_quantity:
        return False, "Quantity exceeds max_quantity"

    notional_base = payload.limit_price or payload.stop_price
    if notional_base is not None and payload.quantity * notional_base > settings.max_notional:
        return False, "Order notional exceeds max_notional"

    allowed_types = {value.lower() for value in settings.allowed_order_types.split(",") if value.strip()}
    if order_type.lower() not in allowed_types:
        return False, f"Order type {order_type} not allowed"

    allowed_symbols = split_csv(settings.symbol_allowlist)
    if allowed_symbols and payload.symbol.upper() not in allowed_symbols:
        return False, f"Symbol {payload.symbol} not allowlisted"

    if settings.execution_mode == "safe_test" and order_type == "market":
        return False, "Market orders blocked in safe_test mode"

    return True, None


def create_event(db: Session, order_id: str, event_type: str, message: str) -> None:
    db.add(models.OrderEvent(order_id=order_id, event_type=event_type, message=message))


def get_dashboard_summary(db: Session) -> dict[str, int]:
    total_orders = db.query(func.count(models.Order.id)).scalar() or 0
    queued_orders = db.query(func.count(models.Order.id)).filter(models.Order.status == "queued").scalar() or 0
    rejected_orders = db.query(func.count(models.Order.id)).filter(models.Order.status == "rejected").scalar() or 0
    accepted_orders = (
        db.query(func.count(models.Order.id))
        .filter(models.Order.status.in_(["queued", "submitted_to_ibkr", "acknowledged"]))
        .scalar()
        or 0
    )
    return {
        "total_orders": total_orders,
        "queued_orders": queued_orders,
        "rejected_orders": rejected_orders,
        "accepted_orders": accepted_orders,
    }


def get_existing_idempotent_order(db: Session, platform: str, broker: str, idempotency_key: str | None) -> models.Order | None:
    if not idempotency_key:
        return None
    return (
        db.query(models.Order)
        .filter(models.Order.source_platform == platform)
        .filter(models.Order.broker == broker)
        .filter(models.Order.idempotency_key == idempotency_key)
        .first()
    )


def order_or_404(db: Session, order_id: str) -> models.Order:
    order = db.get(models.Order, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order
