from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trade_order_bridge import models
from trade_order_bridge.config import settings


@dataclass
class BrokerSubmitResult:
    success: bool
    broker_order_ref: str | None
    status: str
    message: str


class BrokerAdapter(Protocol):
    def submit_order(self, order: models.Order) -> BrokerSubmitResult:
        ...


class IbkrStubAdapter:
    def submit_order(self, order: models.Order) -> BrokerSubmitResult:
        if order.symbol.upper().startswith("FAIL"):
            return BrokerSubmitResult(
                success=False,
                broker_order_ref=None,
                status="failed",
                message="Simulated broker failure for testing",
            )

        action = order.action.lower()
        if action == "cancel":
            return BrokerSubmitResult(
                success=True,
                broker_order_ref=f"ibkr-cancel-{order.id[:8]}",
                status="acknowledged",
                message="Cancel request acknowledged by stub adapter",
            )

        if action == "close":
            return BrokerSubmitResult(
                success=True,
                broker_order_ref=f"ibkr-close-{order.id[:8]}",
                status="acknowledged",
                message="Close request acknowledged by stub adapter",
            )

        return BrokerSubmitResult(
            success=True,
            broker_order_ref=f"ibkr-order-{order.id[:8]}",
            status="acknowledged",
            message="Order accepted by IBKR stub adapter",
        )


class IbkrLiveAdapter:
    def submit_order(self, order: models.Order) -> BrokerSubmitResult:
        try:
            from ib_insync import IB, Contract, LimitOrder, MarketOrder, StopLimitOrder, StopOrder
        except Exception:
            return BrokerSubmitResult(
                success=False,
                broker_order_ref=None,
                status="failed",
                message="ib_insync is not installed; cannot use live adapter",
            )

        ib = IB()
        try:
            ib.connect(settings.ibkr_host, settings.ibkr_port, clientId=settings.ibkr_client_id, timeout=5)
        except Exception as exc:
            return BrokerSubmitResult(
                success=False,
                broker_order_ref=None,
                status="failed",
                message=f"IBKR connection failed: {exc}",
            )

        try:
            contract = _build_contract(Contract, order.symbol)
            if not contract:
                return BrokerSubmitResult(
                    success=False,
                    broker_order_ref=None,
                    status="failed",
                    message=f"Unsupported symbol format: {order.symbol}",
                )

            qualified = ib.qualifyContracts(contract)
            if not qualified:
                return BrokerSubmitResult(
                    success=False,
                    broker_order_ref=None,
                    status="failed",
                    message=f"Unable to qualify contract for symbol {order.symbol}",
                )
            contract = qualified[0]

            action = order.action.lower()
            if action == "cancel":
                return _cancel_matching_orders(ib, contract, order)

            if action == "close":
                return _close_symbol_position(ib, contract, order, MarketOrder)

            ib_order = _build_order(order, MarketOrder, LimitOrder, StopOrder, StopLimitOrder)
            if not ib_order:
                return BrokerSubmitResult(
                    success=False,
                    broker_order_ref=None,
                    status="failed",
                    message=f"Unsupported order action/type: {order.action}/{order.order_type}",
                )

            trade = ib.placeOrder(contract, ib_order)
            ib.sleep(0.3)
            status_value = _trade_status(trade)

            if status_value in {"cancelled", "inactive", "api_cancelled"}:
                return BrokerSubmitResult(
                    success=False,
                    broker_order_ref=str(trade.order.orderId),
                    status="failed",
                    message=f"IBKR rejected/cancelled order with status {status_value}",
                )

            return BrokerSubmitResult(
                success=True,
                broker_order_ref=str(trade.order.orderId),
                status="acknowledged",
                message=f"IBKR accepted order with status {status_value}",
            )
        except Exception as exc:
            return BrokerSubmitResult(
                success=False,
                broker_order_ref=None,
                status="failed",
                message=f"IBKR order submit failed: {exc}",
            )
        finally:
            if ib.isConnected():
                ib.disconnect()


def _build_contract(contract_type, symbol: str):
    symbol_value = symbol.upper().strip()
    if not symbol_value or " " in symbol_value:
        return None
    contract = contract_type()
    contract.symbol = symbol_value
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract


def _build_order(order: models.Order, market_order, limit_order, stop_order, stop_limit_order):
    action = "BUY" if order.action.lower() == "buy" else "SELL"
    if order.action.lower() not in {"buy", "sell"}:
        return None

    quantity = order.quantity
    order_type = order.order_type.lower()

    if order_type == "market":
        obj = market_order(action, quantity)
    elif order_type == "limit" and order.limit_price is not None:
        obj = limit_order(action, quantity, order.limit_price)
    elif order_type == "stop" and order.stop_price is not None:
        obj = stop_order(action, quantity, order.stop_price)
    elif order_type == "stop_limit" and order.stop_price is not None and order.limit_price is not None:
        obj = stop_limit_order(action, quantity, order.limit_price, order.stop_price)
    else:
        return None

    obj.tif = "DAY"
    obj.transmit = order.transmit
    if settings.ibkr_account:
        obj.account = settings.ibkr_account

    if order.client_tag:
        obj.orderRef = order.client_tag

    return obj


def _cancel_matching_orders(ib, contract, order: models.Order) -> BrokerSubmitResult:
    open_trades = ib.openTrades()
    candidates = []
    for trade in open_trades:
        if getattr(trade.contract, "conId", None) == getattr(contract, "conId", None):
            if order.client_tag and getattr(trade.order, "orderRef", "") != order.client_tag:
                continue
            candidates.append(trade)

    if not candidates:
        return BrokerSubmitResult(
            success=False,
            broker_order_ref=None,
            status="failed",
            message=f"No open orders found to cancel for {order.symbol}",
        )

    refs: list[str] = []
    for trade in candidates:
        refs.append(str(trade.order.orderId))
        ib.cancelOrder(trade.order)

    ib.sleep(0.3)
    joined = ",".join(refs)
    return BrokerSubmitResult(
        success=True,
        broker_order_ref=joined,
        status="acknowledged",
        message=f"Cancelled {len(refs)} open order(s) for {order.symbol}",
    )


def _close_symbol_position(ib, contract, order: models.Order, market_order) -> BrokerSubmitResult:
    positions = ib.positions()
    net_position = 0.0
    for item in positions:
        same_contract = getattr(item.contract, "conId", None) == getattr(contract, "conId", None)
        account_ok = not settings.ibkr_account or getattr(item, "account", "") == settings.ibkr_account
        if same_contract and account_ok:
            net_position += float(item.position)

    if abs(net_position) < 1e-9:
        return BrokerSubmitResult(
            success=False,
            broker_order_ref=None,
            status="failed",
            message=f"No open position found for {order.symbol}",
        )

    close_action = "SELL" if net_position > 0 else "BUY"
    close_qty = abs(net_position)
    ib_order = market_order(close_action, close_qty)
    ib_order.tif = "DAY"
    ib_order.transmit = order.transmit
    if settings.ibkr_account:
        ib_order.account = settings.ibkr_account
    if order.client_tag:
        ib_order.orderRef = order.client_tag

    trade = ib.placeOrder(contract, ib_order)
    ib.sleep(0.3)
    status_value = _trade_status(trade)
    if status_value in {"cancelled", "inactive", "api_cancelled"}:
        return BrokerSubmitResult(
            success=False,
            broker_order_ref=str(trade.order.orderId),
            status="failed",
            message=f"Close request failed with status {status_value}",
        )

    return BrokerSubmitResult(
        success=True,
        broker_order_ref=str(trade.order.orderId),
        status="acknowledged",
        message=f"Close request accepted with status {status_value}",
    )


def _trade_status(trade) -> str:
    if trade and trade.orderStatus and trade.orderStatus.status:
        return str(trade.orderStatus.status).lower()
    return "submitted"
