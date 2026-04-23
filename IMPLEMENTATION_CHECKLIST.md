# Implementation Checklist

## Milestone 1 - Ingress and policy foundation

- [x] Bootstrap FastAPI service and DB models
- [x] Implement `POST /webhooks/tradingview/ibkr`
- [x] Add `auth_key` verification against active webhook keys
- [x] Add idempotency support via `idempotency_key`
- [x] Add policy checks (`execution_enabled`, limits, allowlist, order types)
- [x] Add explicit `transmit_enabled` handling in order creation

## Milestone 2 - Operator controls and visibility

- [x] Add admin settings endpoints
- [x] Add webhook key create/disable/rotate endpoints
- [x] Add order query endpoints and event history
- [x] Add dashboard summary endpoint
- [x] Add admin audit logs endpoint
- [x] Add broker connectivity health endpoint

## Milestone 3 - Production readiness follow-ups

- [x] Integrate in-process queue worker for async broker submission (POC)
- [x] Implement IBKR stub adapter for submit/cancel/close lifecycle validation
- [x] Add optional live IBKR adapter path (`BROKER_ADAPTER=ibkr_live`) for buy/sell submit
- [x] Add initial live IBKR close/cancel handling (symbol-based)
- [ ] Expand live IBKR adapter with richer contract resolution (futures/options/forex mapping)
- [x] Add request logging with `X-Request-ID` correlation
- [x] Add in-memory webhook rate limiting
- [ ] Add role-based auth for admin UI/API
- [ ] Add migrations (Alembic) and Postgres deployment profile
- [x] Add automated tests for webhook validation and policy enforcement
- [x] Add nginx and docker-compose deployment manifests
