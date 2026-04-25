import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi import Request, Response
from fastapi.responses import HTMLResponse
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


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>trade-order-bridge</title>
  <style>
    :root { --bg: #f7f8fa; --panel: #ffffff; --text: #111827; --muted: #6b7280; --line: #e5e7eb; --ok: #065f46; --warn: #7c2d12; --btn: #1f2937; --btnText: #ffffff; }
    body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
    .wrap { max-width: 980px; margin: 1.5rem auto; padding: 0 1rem; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 1rem; margin-bottom: 1rem; }
    h1, h2 { margin: 0 0 0.75rem 0; }
    h2 { font-size: 1rem; }
    p, li, label { color: var(--muted); }
    label { display: block; font-size: 0.9rem; margin-bottom: 0.25rem; }
    input, select { width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 8px; padding: 0.6rem; margin-bottom: 0.6rem; font-size: 0.95rem; }
    button { border: 0; background: var(--btn); color: var(--btnText); padding: 0.6rem 0.9rem; border-radius: 8px; cursor: pointer; }
    button.secondary { background: #4b5563; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem; }
    .links a { margin-right: 1rem; }
    .status { font-weight: 600; }
    .ok { color: var(--ok); }
    .warn { color: var(--warn); }
    pre { background: #0f172a; color: #e5e7eb; border-radius: 8px; padding: 0.75rem; overflow: auto; font-size: 0.85rem; }
    @media (max-width: 720px) { .row { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"panel\">
      <h1>trade-order-bridge</h1>
      <p>Admin console (API-backed). Keep this behind Authelia and use your <code>X-Admin-Token</code> for admin calls.</p>
      <div class=\"links\">
        <a href=\"healthz\">Health</a>
        <a href=\"readyz\">Readiness</a>
        <a href=\"docs\">API Docs</a>
      </div>
    </div>

    <div class=\"panel\">
      <h2>Admin Session</h2>
      <label for=\"adminToken\">Admin Token</label>
      <input id=\"adminToken\" type=\"password\" placeholder=\"Paste ADMIN_TOKEN\" />
      <button onclick=\"loadAll()\">Load Settings + Broker Health</button>
      <span id=\"sessionStatus\" class=\"status\"></span>
    </div>

    <div class=\"panel\">
      <h2>Broker Health</h2>
      <div id=\"brokerHealth\" class=\"status\">Not checked yet.</div>
      <button class=\"secondary\" onclick=\"loadBrokerHealth()\">Refresh Broker Health</button>
    </div>

    <div class=\"panel\">
      <h2>Runtime Settings</h2>
      <div class=\"row\">
        <div>
          <label for=\"executionEnabled\">Execution Enabled</label>
          <select id=\"executionEnabled\"><option value=\"true\">true</option><option value=\"false\">false</option></select>
        </div>
        <div>
          <label for=\"transmitEnabled\">Transmit Enabled</label>
          <select id=\"transmitEnabled\"><option value=\"false\">false</option><option value=\"true\">true</option></select>
        </div>
      </div>
      <div class=\"row\">
        <div>
          <label for=\"executionMode\">Execution Mode</label>
          <select id=\"executionMode\"><option value=\"safe_test\">safe_test</option><option value=\"live\">live</option></select>
        </div>
        <div>
          <label for=\"symbolAllowlist\">Symbol Allowlist (comma-separated)</label>
          <input id=\"symbolAllowlist\" type=\"text\" placeholder=\"AAPL,MSFT\" />
        </div>
      </div>
      <div class=\"row\">
        <div>
          <label for=\"maxQuantity\">Max Quantity</label>
          <input id=\"maxQuantity\" type=\"number\" step=\"0.01\" min=\"0.01\" />
        </div>
        <div>
          <label for=\"maxNotional\">Max Notional</label>
          <input id=\"maxNotional\" type=\"number\" step=\"0.01\" min=\"0.01\" />
        </div>
      </div>
      <button onclick=\"saveSettings()\">Save Settings</button>
      <button class=\"secondary\" onclick=\"loadSettings()\">Reload Settings</button>
      <div id=\"settingsStatus\" class=\"status\"></div>
    </div>

    <div class=\"panel\">
      <h2>Webhook Key Rotation</h2>
      <div class=\"row\">
        <div>
          <label for=\"keyName\">Key Name</label>
          <input id=\"keyName\" type=\"text\" value=\"tv-manual-rotation\" />
        </div>
        <div>
          <label>&nbsp;</label>
          <button onclick=\"createKey()\">Create New TradingView Key</button>
        </div>
      </div>
      <p>Store plaintext safely: it is shown only once at creation.</p>
      <pre id=\"keyOutput\">No key created in this session.</pre>
    </div>
  </div>

  <script>
    function adminHeaders() {
      const token = document.getElementById('adminToken').value.trim();
      if (!token) throw new Error('Admin token is required');
      return { 'Content-Type': 'application/json', 'X-Admin-Token': token };
    }

    async function adminGet(path) {
      const res = await fetch(path, { headers: adminHeaders() });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }

    async function adminJson(method, path, body) {
      const res = await fetch(path, { method, headers: adminHeaders(), body: JSON.stringify(body) });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }

    function setStatus(elId, text, ok) {
      const el = document.getElementById(elId);
      el.textContent = text;
      el.className = 'status ' + (ok ? 'ok' : 'warn');
    }

    async function loadBrokerHealth() {
      try {
        const data = await adminGet('admin/broker/health');
        setStatus('brokerHealth', `${data.ok ? 'OK' : 'NOT OK'} - ${data.message}`, data.ok);
      } catch (err) {
        setStatus('brokerHealth', `Error: ${err.message}`, false);
      }
    }

    async function loadSettings() {
      try {
        const s = await adminGet('admin/settings');
        document.getElementById('executionEnabled').value = String(s.execution_enabled);
        document.getElementById('transmitEnabled').value = String(s.transmit_enabled);
        document.getElementById('executionMode').value = s.execution_mode;
        document.getElementById('symbolAllowlist').value = (s.symbol_allowlist || []).join(',');
        document.getElementById('maxQuantity').value = s.max_quantity;
        document.getElementById('maxNotional').value = s.max_notional;
        setStatus('settingsStatus', 'Settings loaded.', true);
      } catch (err) {
        setStatus('settingsStatus', `Error: ${err.message}`, false);
      }
    }

    async function saveSettings() {
      try {
        const payload = {
          execution_enabled: document.getElementById('executionEnabled').value === 'true',
          transmit_enabled: document.getElementById('transmitEnabled').value === 'true',
          execution_mode: document.getElementById('executionMode').value,
          allowed_order_types: ['limit', 'stop', 'stop_limit'],
          symbol_allowlist: document.getElementById('symbolAllowlist').value.split(',').map(v => v.trim().toUpperCase()).filter(Boolean),
          max_quantity: Number(document.getElementById('maxQuantity').value || '1'),
          max_notional: Number(document.getElementById('maxNotional').value || '1000')
        };
        await adminJson('PUT', 'admin/settings', payload);
        setStatus('settingsStatus', 'Settings saved.', true);
      } catch (err) {
        setStatus('settingsStatus', `Save failed: ${err.message}`, false);
      }
    }

    async function createKey() {
      try {
        const name = document.getElementById('keyName').value.trim() || 'tv-manual-rotation';
        const data = await adminJson('POST', 'admin/keys', { name, platform: 'tradingview', broker: 'ibkr' });
        document.getElementById('keyOutput').textContent = JSON.stringify(data, null, 2);
      } catch (err) {
        document.getElementById('keyOutput').textContent = `Error: ${err.message}`;
      }
    }

    async function loadAll() {
      setStatus('sessionStatus', 'Loading...', true);
      await Promise.all([loadSettings(), loadBrokerHealth()]);
      setStatus('sessionStatus', 'Loaded.', true);
    }
  </script>
</body>
</html>"""


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
