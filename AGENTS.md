# AGENTS.md

## Project identity

- Canonical app/project name is `trade-order-bridge`.
- Primary ingress route is `POST /webhooks/tradingview/ibkr`.

## Stack and run commands

- Python service: FastAPI + SQLAlchemy (manifest: `pyproject.toml`).
- Async execution in current phase is an in-process queue worker + IBKR stub adapter.
- Optional live path uses `BROKER_ADAPTER=ibkr_live` and `ib_insync` extra.
- Local run:
  - `pip install -e .`
  - `uvicorn trade_order_bridge.main:app --reload`
- Default local DB is SQLite file from `DATABASE_URL` (`.env.example`).

## High-signal behavior constraints

- Webhook auth for POC uses JSON `auth_key` (not signature).
- Keep `transmit_enabled` as an explicit operator toggle in settings/UI.
- `execution_mode=safe_test` is expected default and blocks market orders.
- `queued` orders are auto-processed by worker into `submitted_to_ibkr` then adapter result statuses.
- `transmit=false` orders may not appear in TradingView broker view; verify in app logs/status + TWS/Gateway.

## Admin/API expectations

- Admin routes require `X-Admin-Token` matching `ADMIN_TOKEN`.
- Key management flow is built into API (`/admin/keys`, disable, rotate); do not store plaintext keys after creation response.

## Source-of-truth docs

- Functional spec: `SPEC.md`
- Build status checklist: `IMPLEMENTATION_CHECKLIST.md`
