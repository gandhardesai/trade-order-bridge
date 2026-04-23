# IB Gateway on VPS

This stack runs IB Gateway headlessly with IBC automation using Docker.

## 1) Prepare credentials

```bash
cp .env.example .env
```

Edit `.env` and set at least:

- `TWS_USERID`
- `TWS_PASSWORD`
- `TRADING_MODE=live`
- `READ_ONLY_API=no`

Optional:

- `VNC_SERVER_PASSWORD` to enable VNC on `127.0.0.1:5900`.

## 2) Start IB Gateway

```bash
docker-compose up -d
docker logs -f ib-gateway
```

Wait for successful login completion (and approve 2FA when prompted).

## 3) Bridge settings

Set `trade-order-bridge` runtime env to:

- `BROKER_ADAPTER=ibkr_live`
- `IBKR_HOST=127.0.0.1`
- `IBKR_PORT=4001`
- `IBKR_CLIENT_ID=23` (or another stable integer)

Then recreate bridge:

```bash
docker-compose --env-file .env.production down --remove-orphans
docker-compose --env-file .env.production up -d --build
```

## 4) Verify

- `curl -sS http://127.0.0.1:8000/healthz`
- `curl -sS http://127.0.0.1:8000/readyz`
- `GET /admin/broker/health` with `X-Admin-Token`

## 5) Security notes

- Keep `4001/4002/5900` bound to `127.0.0.1` only.
- Keep webhook auth in app via `auth_key` and admin routes via `X-Admin-Token`.
- Expect occasional manual 2FA/session intervention.
