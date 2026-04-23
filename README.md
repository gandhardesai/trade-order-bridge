# trade-order-bridge

POC webhook service for receiving TradingView alerts and routing normalized orders to brokers (IBKR first).

## Current POC scope

- Endpoint: `POST /webhooks/tradingview/ibkr`
- Authentication: `auth_key` in JSON payload
- Storage: SQLite by default (can switch to Postgres URL)
- Safety controls: `execution_enabled`, `transmit_enabled`, `execution_mode`
- Admin key rotation + settings endpoints
- Async in-process execution worker with IBKR stub adapter

## Run locally

1. Create a virtual environment and install deps:

```bash
pip install -e .
```

For live IBKR adapter support, install optional dependency:

```bash
pip install -e .[ibkr]
```

2. Optional: set env vars from `.env.example`.

3. Run API:

```bash
python -m uvicorn trade_order_bridge.main:app --reload
```

## TradingView sample payload

```json
{
  "auth_key": "replace_with_generated_key",
  "idempotency_key": "{{strategy.order.id}}-{{timenow}}",
  "symbol": "{{ticker}}",
  "action": "{{strategy.order.action}}",
  "quantity": {{strategy.order.contracts}},
  "quantity_type": "fixed"
}
```

## Admin bootstrap

- Default admin token is loaded from `ADMIN_TOKEN`.
- Include `X-Admin-Token` header on `/admin/*` routes.
- Generate first webhook key via `POST /admin/keys`.

## Broker execution behavior in this phase

- Accepted orders are queued, then processed asynchronously by a background worker.
- Current broker integration is a stub adapter for lifecycle validation (`submitted_to_ibkr` -> `acknowledged`/`failed`).
- Orders with symbols prefixed by `FAIL` are intentionally failed by the stub for testing error paths.
- Set `BROKER_ADAPTER=ibkr_live` to enable live adapter execution.
- Live adapter connection settings come from `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, and optional `IBKR_ACCOUNT`.
- Live adapter currently assumes stock-style contracts and includes initial symbol-based `close`/`cancel` handling.

## Docker Compose (local/VPS)

- Build and run:

```bash
docker-compose up -d --build
```

- Service includes Portal labels with generic description and app id `trade-order-bridge`.
