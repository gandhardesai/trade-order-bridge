from __future__ import annotations

from sqlalchemy.orm import Session

from trade_order_bridge import models
from trade_order_bridge.broker import BrokerAdapter, IbkrLiveAdapter, IbkrStubAdapter
from trade_order_bridge.config import settings
from trade_order_bridge.services import create_event


def get_broker_adapter(order: models.Order) -> BrokerAdapter:
    if order.broker.lower() == "ibkr" and settings.broker_adapter.lower() == "ibkr_live":
        return IbkrLiveAdapter()
    return IbkrStubAdapter()


def process_order_submission(db: Session, order_id: str) -> models.Order | None:
    order = db.get(models.Order, order_id)
    if not order:
        return None

    if order.status != "queued":
        return order

    order.status = "submitted_to_ibkr"
    create_event(db, order.id, "submitted_to_ibkr", "Order handed off to broker adapter")

    adapter = get_broker_adapter(order)
    result = adapter.submit_order(order)

    db.add(
        models.BrokerSubmission(
            order_id=order.id,
            broker_order_ref=result.broker_order_ref,
            status=result.status,
            message=result.message,
        )
    )

    if result.success:
        order.status = result.status
        create_event(db, order.id, result.status, result.message)
    else:
        order.status = "failed"
        order.rejection_reason = result.message
        create_event(db, order.id, "failed", result.message)

    db.commit()
    db.refresh(order)
    return order
