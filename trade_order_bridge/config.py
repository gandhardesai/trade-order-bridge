from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "trade-order-bridge"
    database_url: str = "sqlite:///./trade_order_bridge.db"
    admin_token: str = "change-me-admin-token"
    key_hash_iterations: int = 120000
    broker_adapter: str = "stub"
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 23
    ibkr_account: str = ""


settings = Settings()
