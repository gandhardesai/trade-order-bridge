import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi import Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from trade_order_bridge import models, schemas, security, services
from trade_order_bridge.config import settings
from trade_order_bridge.database import Base, engine
from trade_order_bridge.deps import db_session, require_admin_token
from trade_order_bridge.execution import get_default_broker_adapter, process_order_submission
from trade_order_bridge.logging_utils import configure_logging, request_logger
from trade_order_bridge.queue_worker import enqueue_order, start_worker, stop_worker
from trade_order_bridge.rate_limit import SlidingWindowRateLimiter

app = FastAPI(title=settings.app_name)
webhook_rate_limiter = SlidingWindowRateLimiter(
    limit_count=settings.webhook_rate_limit_count,
    window_sec=settings.webhook_rate_limit_window_sec,
)


@app.on_event("startup")
def startup() -> None:
    configure_logging()
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        services.get_or_create_runtime_settings(db)
    start_worker()


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    logger = request_logger()
    started = time.perf_counter()
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    client_host = request.client.host if request.client else "unknown"

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.exception(
            "request_failed request_id=%s method=%s path=%s client=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            client_host,
            duration_ms,
        )
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "request_complete request_id=%s method=%s path=%s status=%s client=%s duration_ms=%s",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        client_host,
        duration_ms,
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.on_event("shutdown")
def shutdown() -> None:
    stop_worker()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz(db: Session = Depends(db_session)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.post("/webhooks/tradingview/ibkr", response_model=schemas.WebhookAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
def webhook_tradingview_ibkr(
    payload: schemas.TradingViewWebhookRequest,
    request: Request,
    db: Session = Depends(db_session),
) -> schemas.WebhookAcceptedResponse:
    client_host = request.client.host if request.client else "unknown"
    rate_key = f"webhook:{client_host}:{payload.auth_key[:8]}"
    if not webhook_rate_limiter.allow(rate_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Webhook rate limit exceeded")

    key = services.find_active_key(db, payload.auth_key, platform="tradingview", broker="ibkr")
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth key")
    key.last_used_at = services.now_utc()

    duplicate = services.get_existing_idempotent_order(db, "tradingview", "ibkr", payload.idempotency_key)
    if duplicate:
        return schemas.WebhookAcceptedResponse(
            order_id=duplicate.id,
            status=duplicate.status,
            transmit=duplicate.transmit,
            execution_mode=duplicate.execution_mode,
            duplicate=True,
        )

    runtime_settings = services.get_or_create_runtime_settings(db)
    order_type = services.resolve_order_type(payload)
    is_allowed, reject_reason = services.enforce_runtime_policy(runtime_settings, payload, order_type)
    transmit = runtime_settings.transmit_enabled and runtime_settings.execution_mode == "live"

    order = models.Order(
        source_platform="tradingview",
        broker="ibkr",
        symbol=payload.symbol,
        action=payload.action,
        quantity=payload.quantity,
        quantity_type=payload.quantity_type,
        order_type=order_type,
        limit_price=payload.limit_price,
        stop_price=payload.stop_price,
        take_profit_price=payload.take_profit_price,
        stop_loss_price=payload.stop_loss_price,
        idempotency_key=payload.idempotency_key,
        client_tag=payload.client_tag,
        status="queued" if is_allowed else "rejected",
        transmit=transmit if is_allowed else False,
        execution_mode=runtime_settings.execution_mode,
        rejection_reason=reject_reason,
    )
    db.add(order)
    db.flush()

    services.create_event(db, order.id, "received", "Webhook payload received")
    services.create_event(db, order.id, "authenticated", "auth_key validated")
    if is_allowed:
        services.create_event(db, order.id, "queued", "Order queued for broker submission")
    else:
        services.create_event(db, order.id, "rejected", reject_reason or "Rejected by policy")

    db.commit()
    db.refresh(order)

    if order.status == "queued":
        enqueue_order(order.id)

    return schemas.WebhookAcceptedResponse(
        order_id=order.id,
        status=order.status,
        transmit=order.transmit,
        execution_mode=order.execution_mode,
    )


@app.get("/orders/{order_id}", response_model=schemas.OrderResponse)
def get_order(order_id: str, db: Session = Depends(db_session)) -> schemas.OrderResponse:
    order = services.order_or_404(db, order_id)
    return _serialize_order(order)


@app.get("/orders", response_model=list[schemas.OrderResponse])
def list_orders(
    status_filter: str | None = None,
    symbol: str | None = None,
    limit: int = 50,
    db: Session = Depends(db_session),
) -> list[schemas.OrderResponse]:
    query = db.query(models.Order)
    if status_filter:
        query = query.filter(models.Order.status == status_filter)
    if symbol:
        query = query.filter(models.Order.symbol == symbol.upper())
    orders = query.order_by(models.Order.created_at.desc()).limit(min(limit, 200)).all()
    return [_serialize_order(order) for order in orders]


@app.get("/dashboard/summary", response_model=schemas.DashboardSummary, dependencies=[Depends(require_admin_token)])
def dashboard_summary(db: Session = Depends(db_session)) -> schemas.DashboardSummary:
    return schemas.DashboardSummary(**services.get_dashboard_summary(db))


@app.get("/admin/broker/health", response_model=schemas.BrokerHealthResponse, dependencies=[Depends(require_admin_token)])
def admin_broker_health() -> schemas.BrokerHealthResponse:
    adapter = get_default_broker_adapter()
    result = adapter.health_check()
    return schemas.BrokerHealthResponse(ok=result.ok, adapter=result.adapter, message=result.message)


@app.get("/admin/settings", response_model=schemas.RuntimeSettingsResponse, dependencies=[Depends(require_admin_token)])
def admin_get_settings(db: Session = Depends(db_session)) -> schemas.RuntimeSettingsResponse:
    runtime = services.get_or_create_runtime_settings(db)
    return schemas.RuntimeSettingsResponse(
        execution_enabled=runtime.execution_enabled,
        transmit_enabled=runtime.transmit_enabled,
        execution_mode=runtime.execution_mode,
        allowed_order_types=[value.strip() for value in runtime.allowed_order_types.split(",") if value.strip()],
        symbol_allowlist=services.split_csv(runtime.symbol_allowlist),
        max_quantity=runtime.max_quantity,
        max_notional=runtime.max_notional,
        updated_at=runtime.updated_at,
    )


@app.put("/admin/settings", response_model=schemas.RuntimeSettingsResponse, dependencies=[Depends(require_admin_token)])
def admin_update_settings(
    payload: schemas.RuntimeSettingsUpdate,
    request: Request,
    db: Session = Depends(db_session),
) -> schemas.RuntimeSettingsResponse:
    runtime = services.get_or_create_runtime_settings(db)
    runtime.execution_enabled = payload.execution_enabled
    runtime.transmit_enabled = payload.transmit_enabled
    runtime.execution_mode = payload.execution_mode
    runtime.allowed_order_types = ",".join(payload.allowed_order_types)
    runtime.symbol_allowlist = ",".join(symbol.upper() for symbol in payload.symbol_allowlist)
    runtime.max_quantity = payload.max_quantity
    runtime.max_notional = payload.max_notional
    services.add_admin_audit_log(
        db,
        actor=request.client.host if request.client else "unknown",
        action="settings.update",
        target="runtime_settings",
        details=f"execution_mode={runtime.execution_mode}, transmit_enabled={runtime.transmit_enabled}",
    )
    db.commit()
    db.refresh(runtime)
    return schemas.RuntimeSettingsResponse(
        execution_enabled=runtime.execution_enabled,
        transmit_enabled=runtime.transmit_enabled,
        execution_mode=runtime.execution_mode,
        allowed_order_types=[value.strip() for value in runtime.allowed_order_types.split(",") if value.strip()],
        symbol_allowlist=services.split_csv(runtime.symbol_allowlist),
        max_quantity=runtime.max_quantity,
        max_notional=runtime.max_notional,
        updated_at=runtime.updated_at,
    )


@app.get("/admin/keys", response_model=list[schemas.WebhookKeyResponse], dependencies=[Depends(require_admin_token)])
def admin_list_keys(db: Session = Depends(db_session)) -> list[schemas.WebhookKeyResponse]:
    keys = db.query(models.WebhookKey).order_by(models.WebhookKey.created_at.desc()).all()
    return [
        schemas.WebhookKeyResponse(
            id=key.id,
            name=key.name,
            platform=key.platform,
            broker=key.broker,
            key_prefix=key.key_prefix,
            is_active=key.is_active,
            created_at=key.created_at,
            last_used_at=key.last_used_at,
        )
        for key in keys
    ]


@app.post("/admin/keys", response_model=schemas.CreateWebhookKeyResponse, dependencies=[Depends(require_admin_token)])
def admin_create_key(
    payload: schemas.CreateWebhookKeyRequest,
    request: Request,
    db: Session = Depends(db_session),
) -> schemas.CreateWebhookKeyResponse:
    plaintext = security.generate_webhook_key()
    salt = security.random_salt()
    item = models.WebhookKey(
        name=payload.name,
        platform=payload.platform.lower(),
        broker=payload.broker.lower(),
        key_prefix=security.key_prefix(plaintext),
        key_salt=salt,
        key_hash=security.hash_key(plaintext, salt),
        is_active=True,
    )
    db.add(item)
    services.add_admin_audit_log(
        db,
        actor=request.client.host if request.client else "unknown",
        action="keys.create",
        target=payload.name,
        details=f"platform={item.platform}, broker={item.broker}",
    )
    db.commit()
    db.refresh(item)
    return schemas.CreateWebhookKeyResponse(
        id=item.id,
        name=item.name,
        platform=item.platform,
        broker=item.broker,
        key_prefix=item.key_prefix,
        is_active=item.is_active,
        created_at=item.created_at,
        last_used_at=item.last_used_at,
        plaintext_key=plaintext,
    )


@app.post("/admin/keys/{key_id}/disable", response_model=schemas.WebhookKeyResponse, dependencies=[Depends(require_admin_token)])
def admin_disable_key(key_id: str, request: Request, db: Session = Depends(db_session)) -> schemas.WebhookKeyResponse:
    item = db.get(models.WebhookKey, key_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    item.is_active = False
    services.add_admin_audit_log(
        db,
        actor=request.client.host if request.client else "unknown",
        action="keys.disable",
        target=item.name,
        details=f"key_id={item.id}",
    )
    db.commit()
    db.refresh(item)
    return schemas.WebhookKeyResponse(
        id=item.id,
        name=item.name,
        platform=item.platform,
        broker=item.broker,
        key_prefix=item.key_prefix,
        is_active=item.is_active,
        created_at=item.created_at,
        last_used_at=item.last_used_at,
    )


@app.post("/admin/keys/{key_id}/rotate", response_model=schemas.CreateWebhookKeyResponse, dependencies=[Depends(require_admin_token)])
def admin_rotate_key(key_id: str, request: Request, db: Session = Depends(db_session)) -> schemas.CreateWebhookKeyResponse:
    old = db.get(models.WebhookKey, key_id)
    if not old:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    old.is_active = False

    plaintext = security.generate_webhook_key()
    salt = security.random_salt()
    new_item = models.WebhookKey(
        name=f"{old.name}-rotated",
        platform=old.platform,
        broker=old.broker,
        key_prefix=security.key_prefix(plaintext),
        key_salt=salt,
        key_hash=security.hash_key(plaintext, salt),
        is_active=True,
    )
    db.add(new_item)
    services.add_admin_audit_log(
        db,
        actor=request.client.host if request.client else "unknown",
        action="keys.rotate",
        target=old.name,
        details=f"old_key_id={old.id}, new_key_id={new_item.id}",
    )
    db.commit()
    db.refresh(new_item)

    return schemas.CreateWebhookKeyResponse(
        id=new_item.id,
        name=new_item.name,
        platform=new_item.platform,
        broker=new_item.broker,
        key_prefix=new_item.key_prefix,
        is_active=new_item.is_active,
        created_at=new_item.created_at,
        last_used_at=new_item.last_used_at,
        plaintext_key=plaintext,
    )


@app.post("/admin/orders/{order_id}/process", response_model=schemas.OrderResponse, dependencies=[Depends(require_admin_token)])
def admin_process_order(order_id: str, request: Request, db: Session = Depends(db_session)) -> schemas.OrderResponse:
    order = services.order_or_404(db, order_id)
    services.add_admin_audit_log(
        db,
        actor=request.client.host if request.client else "unknown",
        action="orders.process",
        target=order.id,
        details=f"status_before={order.status}",
    )
    if order.status == "queued":
        processed = process_order_submission(db, order.id)
        if processed:
            return _serialize_order(processed)
    db.commit()
    return _serialize_order(order)


@app.get("/admin/audit-logs", response_model=list[schemas.AdminAuditLogResponse], dependencies=[Depends(require_admin_token)])
def admin_audit_logs(limit: int = 100, db: Session = Depends(db_session)) -> list[schemas.AdminAuditLogResponse]:
    rows = (
        db.query(models.AdminAuditLog)
        .order_by(models.AdminAuditLog.created_at.desc())
        .limit(min(limit, 500))
        .all()
    )
    return [
        schemas.AdminAuditLogResponse(
            actor=row.actor,
            action=row.action,
            target=row.target,
            details=row.details,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _serialize_order(order: models.Order) -> schemas.OrderResponse:
    events = [
        schemas.OrderEventResponse(
            event_type=event.event_type,
            message=event.message,
            created_at=event.created_at,
        )
        for event in sorted(order.events, key=lambda item: item.created_at)
    ]
    return schemas.OrderResponse(
        id=order.id,
        source_platform=order.source_platform,
        broker=order.broker,
        symbol=order.symbol,
        action=order.action,
        quantity=order.quantity,
        quantity_type=order.quantity_type,
        order_type=order.order_type,
        status=order.status,
        transmit=order.transmit,
        execution_mode=order.execution_mode,
        idempotency_key=order.idempotency_key,
        rejection_reason=order.rejection_reason,
        created_at=order.created_at,
        updated_at=order.updated_at,
        events=events,
        submissions=[
            schemas.BrokerSubmissionResponse(
                broker_order_ref=item.broker_order_ref,
                status=item.status,
                message=item.message,
                created_at=item.created_at,
            )
            for item in sorted(order.submissions, key=lambda value: value.created_at)
        ],
    )
