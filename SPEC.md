# trade-order-bridge POC Spec

## Objective

Build a self-hosted webhook service to replace SignalStack for TradingView-originated orders sent to IBKR.

## Scope

- Inbound platform: TradingView
- Broker: IBKR
- Endpoint: `POST /webhooks/tradingview/ibkr`
- Auth method: `auth_key` inside JSON payload
- Payload compatibility: SignalStack-style fields with minimal Pine Script changes
- Execution pipeline: async queue worker + broker adapter (`stub` default, `ibkr_live` optional)

## Safety controls

- `execution_enabled`: global kill switch
- `transmit_enabled`: explicit UI toggle for IBKR `transmit`
- `execution_mode`: `safe_test` (default) or `live`
- Risk controls: symbol allowlist, max quantity, max notional, allowed order types
- `safe_test` blocks market orders

## API summary

- Public:
  - `POST /webhooks/tradingview/ibkr`
  - `GET /healthz`
  - `GET /readyz`
  - `GET /orders`
  - `GET /orders/{order_id}`
- Admin (`X-Admin-Token`):
  - `GET /admin/settings`
  - `PUT /admin/settings`
  - `GET /admin/keys`
  - `POST /admin/keys`
  - `POST /admin/keys/{key_id}/disable`
  - `POST /admin/keys/{key_id}/rotate`
  - `POST /admin/orders/{order_id}/process` (manual retry/process trigger)
  - `GET /dashboard/summary`

## Accepted webhook payload

Required fields:

- `auth_key`
- `symbol`
- `action` (`buy`, `sell`, `close`, `cancel`)
- `quantity`

Optional fields:

- `quantity_type` (`fixed`, `cash`, `percent_of_equity`)
- `limit_price`, `stop_price`, `take_profit_price`, `stop_loss_price`
- `idempotency_key`
- `client_tag`

## Order lifecycle

- `queued` on acceptance
- `submitted_to_ibkr` when execution worker hands to adapter
- `acknowledged` or `failed` after broker adapter result
- `rejected` on policy failure
- Order events recorded for `received`, `authenticated`, `queued` or `rejected`

## Data model

- `runtime_settings`
- `webhook_keys`
- `orders`
- `order_events`
- `broker_submissions`

## TradingView template (recommended)

```json
{
  "auth_key": "REPLACE_WITH_GENERATED_KEY",
  "idempotency_key": "{{strategy.order.id}}-{{timenow}}",
  "symbol": "{{ticker}}",
  "action": "{{strategy.order.action}}",
  "quantity": {{strategy.order.contracts}},
  "quantity_type": "fixed"
}
```

## Notes

- `transmit=false` orders may not appear as active broker orders in TradingView.
- Verify first via bridge logs/order status and TWS/Gateway.
- Live adapter is enabled with `BROKER_ADAPTER=ibkr_live` and IBKR env settings.
