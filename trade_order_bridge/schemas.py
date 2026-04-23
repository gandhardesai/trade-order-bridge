from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ActionType = Literal["buy", "sell", "close", "cancel"]
QuantityType = Literal["fixed", "cash", "percent_of_equity"]
ExecutionMode = Literal["safe_test", "live"]


class TradingViewWebhookRequest(BaseModel):
    auth_key: str = Field(min_length=8)
    idempotency_key: str | None = Field(default=None, max_length=128)
    symbol: str = Field(min_length=1, max_length=64)
    action: ActionType
    quantity: float = Field(gt=0)
    quantity_type: QuantityType = "fixed"
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    take_profit_price: float | None = Field(default=None, gt=0)
    stop_loss_price: float | None = Field(default=None, gt=0)
    client_tag: str | None = Field(default=None, max_length=128)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_price_logic(self):
        if self.action in {"close", "cancel"}:
            return self

        entry_price = self.limit_price or self.stop_price
        if entry_price is None:
            return self

        if self.action == "buy":
            if self.stop_loss_price is not None and self.stop_loss_price >= entry_price:
                raise ValueError("stop_loss_price must be lower than entry price for buy")
            if self.take_profit_price is not None and self.take_profit_price <= entry_price:
                raise ValueError("take_profit_price must be higher than entry price for buy")
        if self.action == "sell":
            if self.stop_loss_price is not None and self.stop_loss_price <= entry_price:
                raise ValueError("stop_loss_price must be higher than entry price for sell")
            if self.take_profit_price is not None and self.take_profit_price >= entry_price:
                raise ValueError("take_profit_price must be lower than entry price for sell")
        return self


class WebhookAcceptedResponse(BaseModel):
    order_id: str
    status: str
    transmit: bool
    execution_mode: str
    duplicate: bool = False


class RuntimeSettingsUpdate(BaseModel):
    execution_enabled: bool
    transmit_enabled: bool
    execution_mode: ExecutionMode
    allowed_order_types: list[str] = Field(default_factory=lambda: ["limit", "stop", "stop_limit"])
    symbol_allowlist: list[str] = Field(default_factory=list)
    max_quantity: float = Field(gt=0)
    max_notional: float = Field(gt=0)


class RuntimeSettingsResponse(RuntimeSettingsUpdate):
    updated_at: datetime


class CreateWebhookKeyRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    platform: str = Field(default="tradingview", max_length=32)
    broker: str = Field(default="ibkr", max_length=32)


class WebhookKeyResponse(BaseModel):
    id: str
    name: str
    platform: str
    broker: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None


class CreateWebhookKeyResponse(WebhookKeyResponse):
    plaintext_key: str


class OrderEventResponse(BaseModel):
    event_type: str
    message: str
    created_at: datetime


class BrokerSubmissionResponse(BaseModel):
    broker_order_ref: str | None
    status: str
    message: str
    created_at: datetime


class OrderResponse(BaseModel):
    id: str
    source_platform: str
    broker: str
    symbol: str
    action: str
    quantity: float
    quantity_type: str
    order_type: str
    status: str
    transmit: bool
    execution_mode: str
    idempotency_key: str | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime
    events: list[OrderEventResponse]
    submissions: list[BrokerSubmissionResponse]


class DashboardSummary(BaseModel):
    total_orders: int
    queued_orders: int
    rejected_orders: int
    accepted_orders: int
