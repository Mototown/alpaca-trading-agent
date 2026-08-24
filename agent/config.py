"""
config.py — pydantic-settings Config model.

All settings are loaded from environment variables (or a .env file).
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # ignore unknown env vars (e.g. GITHUB_PAT)
    )

    # ── Alpaca ───────────────────────────────────────────────────────────────
    alpaca_api_key: str = ""     # required at runtime; empty default allows tests to load
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True

    # ── MCP server ───────────────────────────────────────────────────────────
    mcp_server_url: str = "http://localhost:3001/mcp"

    # ── LLM ─────────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Watchlist ────────────────────────────────────────────────────────────
    watchlist: list[str] = Field(default=["SPY", "QQQ", "AAPL", "MSFT"])

    @field_validator("watchlist", mode="before")
    @classmethod
    def parse_watchlist(cls, v: object) -> list[str]:
        """Accept both JSON array and bare CSV string from .env."""
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json as _json
                return _json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        return v  # type: ignore[return-value]

    # ── Loop ─────────────────────────────────────────────────────────────────
    loop_interval_seconds: int = 300
    dry_run: bool = False

    # ── Risk parameters ──────────────────────────────────────────────────────
    max_hedge_pct: float = 0.02          # max premium per tick as % of equity
    target_delta: float = 0.10           # desired net portfolio delta
    max_delta: float = 0.30              # hard delta limit before forced hedge
    max_option_exposure_pct: float = 0.10  # total option notional / equity
    max_contracts_per_symbol: int = 5
    max_positions: int = 20
    daily_loss_limit: float = 0.03       # fraction of equity
    drawdown_alert_pct: float = 0.05
    drawdown_pause_pct: float = 0.08

    # ── Options selection ────────────────────────────────────────────────────
    min_dte: int = 21                    # minimum days-to-expiry
    preferred_dte_low: int = 21
    preferred_dte_high: int = 45
    min_open_interest: int = 100
    max_spread_width: float = 0.15       # max bid/ask spread in dollars

    # ── Dashboard ────────────────────────────────────────────────────────────
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8080

    # ── Database ─────────────────────────────────────────────────────────────
    db_path: str = "./data/daha.db"

    @property
    def use_llm(self) -> bool:
        return bool(self.openai_api_key or self.anthropic_api_key)

    @property
    def alpaca_base_url(self) -> str:
        return (
            "https://paper-api.alpaca.markets"
            if self.alpaca_paper
            else "https://api.alpaca.markets"
        )


# Module-level singleton — import this everywhere
settings = Config()  # type: ignore[call-arg]
