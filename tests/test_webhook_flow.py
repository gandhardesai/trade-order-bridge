import os
import time
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_trade_order_bridge.db"
os.environ["ADMIN_TOKEN"] = "test-admin-token"

from fastapi.testclient import TestClient

from trade_order_bridge.database import Base, engine
from trade_order_bridge.main import app, webhook_rate_limiter


class WebhookFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        webhook_rate_limiter.reset()
        self.client = TestClient(app)
        self.client.get("/healthz")

        self.key_response = self.client.post(
            "/admin/keys",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"name": "test-tv", "platform": "tradingview", "broker": "ibkr"},
        )
        self.assertEqual(self.key_response.status_code, 200)
        self.webhook_key = self.key_response.json()["plaintext_key"]

    def tearDown(self) -> None:
        self.client.close()

    def test_webhook_idempotency_and_async_ack(self) -> None:
        payload = {
            "auth_key": self.webhook_key,
            "idempotency_key": "idem-001",
            "symbol": "AAPL",
            "action": "buy",
            "quantity": 1,
            "quantity_type": "fixed",
            "limit_price": 100.0,
        }

        first = self.client.post("/webhooks/tradingview/ibkr", json=payload)
        self.assertEqual(first.status_code, 202)
        order_id = first.json()["order_id"]

        second = self.client.post("/webhooks/tradingview/ibkr", json=payload)
        self.assertEqual(second.status_code, 202)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(order_id, second.json()["order_id"])

        for _ in range(20):
            order = self.client.get(f"/orders/{order_id}").json()
            if order["status"] in {"acknowledged", "failed"}:
                break
            time.sleep(0.05)

        latest = self.client.get(f"/orders/{order_id}").json()
        if latest["status"] == "queued":
            self.client.post(
                f"/admin/orders/{order_id}/process",
                headers={"X-Admin-Token": "test-admin-token"},
            )

        final = self.client.get(f"/orders/{order_id}")
        self.assertEqual(final.status_code, 200)
        body = final.json()
        self.assertIn(body["status"], {"acknowledged", "failed", "submitted_to_ibkr", "queued"})
        self.assertGreaterEqual(len(body["events"]), 3)

    def test_safe_test_blocks_market_orders(self) -> None:
        payload = {
            "auth_key": self.webhook_key,
            "idempotency_key": "idem-market-001",
            "symbol": "MSFT",
            "action": "buy",
            "quantity": 1,
            "quantity_type": "fixed",
        }

        response = self.client.post("/webhooks/tradingview/ibkr", json=payload)
        self.assertEqual(response.status_code, 202)
        order_id = response.json()["order_id"]

        order = self.client.get(f"/orders/{order_id}")
        self.assertEqual(order.status_code, 200)
        body = order.json()
        self.assertEqual(body["status"], "rejected")
        self.assertTrue(
            "market" in body["rejection_reason"].lower()
            or "not allowed" in body["rejection_reason"].lower()
        )

    def test_live_mode_with_transmit_enabled_sets_transmit_true(self) -> None:
        settings_update = {
            "execution_enabled": True,
            "transmit_enabled": True,
            "execution_mode": "live",
            "allowed_order_types": ["market", "limit", "stop", "stop_limit"],
            "symbol_allowlist": [],
            "max_quantity": 100,
            "max_notional": 100000,
        }
        update_response = self.client.put(
            "/admin/settings",
            headers={"X-Admin-Token": "test-admin-token"},
            json=settings_update,
        )
        self.assertEqual(update_response.status_code, 200)

        payload = {
            "auth_key": self.webhook_key,
            "idempotency_key": "idem-live-001",
            "symbol": "AAPL",
            "action": "buy",
            "quantity": 1,
            "quantity_type": "fixed",
            "limit_price": 100.0,
        }

        response = self.client.post("/webhooks/tradingview/ibkr", json=payload)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["transmit"])

    def test_request_id_header_is_present(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response.headers)

    def test_webhook_rate_limit_returns_429(self) -> None:
        original_limit = webhook_rate_limiter.limit_count
        original_window = webhook_rate_limiter.window_sec
        webhook_rate_limiter.limit_count = 2
        webhook_rate_limiter.window_sec = 60
        webhook_rate_limiter.reset()

        try:
            for index in range(2):
                response = self.client.post(
                    "/webhooks/tradingview/ibkr",
                    json={
                        "auth_key": self.webhook_key,
                        "idempotency_key": f"idem-rate-{index}",
                        "symbol": "AAPL",
                        "action": "buy",
                        "quantity": 1,
                        "quantity_type": "fixed",
                        "limit_price": 100.0,
                    },
                )
                self.assertEqual(response.status_code, 202)

            blocked = self.client.post(
                "/webhooks/tradingview/ibkr",
                json={
                    "auth_key": self.webhook_key,
                    "idempotency_key": "idem-rate-blocked",
                    "symbol": "AAPL",
                    "action": "buy",
                    "quantity": 1,
                    "quantity_type": "fixed",
                    "limit_price": 100.0,
                },
            )
            self.assertEqual(blocked.status_code, 429)
        finally:
            webhook_rate_limiter.limit_count = original_limit
            webhook_rate_limiter.window_sec = original_window
            webhook_rate_limiter.reset()

    def test_admin_actions_are_audited(self) -> None:
        update_response = self.client.put(
            "/admin/settings",
            headers={"X-Admin-Token": "test-admin-token"},
            json={
                "execution_enabled": True,
                "transmit_enabled": False,
                "execution_mode": "safe_test",
                "allowed_order_types": ["limit", "stop", "stop_limit"],
                "symbol_allowlist": ["AAPL"],
                "max_quantity": 50,
                "max_notional": 50000,
            },
        )
        self.assertEqual(update_response.status_code, 200)

        logs_response = self.client.get(
            "/admin/audit-logs",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        self.assertEqual(logs_response.status_code, 200)
        rows = logs_response.json()
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(any(item["action"] == "settings.update" for item in rows))

    def test_broker_health_endpoint(self) -> None:
        unauthorized = self.client.get("/admin/broker/health")
        self.assertEqual(unauthorized.status_code, 401)

        authorized = self.client.get(
            "/admin/broker/health",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        self.assertEqual(authorized.status_code, 200)
        body = authorized.json()
        self.assertIn("ok", body)
        self.assertIn("adapter", body)


if __name__ == "__main__":
    unittest.main()
