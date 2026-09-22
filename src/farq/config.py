"""Configurable search thresholds. Numbers here are defaults, not claims of quality."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SearchConfig:
    """Quality-gate knobs.

    Product, vehicle, and property intents request live Haraj because the
    measured local corpus stores sellers, not ads. That is a coverage fact,
    not a tuned relevance score.
    """

    min_qualified_local: int = field(default_factory=lambda: _env_int("FARQ_MIN_QUALIFIED_LOCAL", 3))
    live_page_size: int = field(default_factory=lambda: _env_int("FARQ_LIVE_PAGE_SIZE", 10))
    live_max_pages: int = field(default_factory=lambda: _env_int("FARQ_LIVE_MAX_PAGES", 2))
    live_max_queries: int = field(default_factory=lambda: _env_int("FARQ_LIVE_MAX_QUERIES", 3))
    live_timeout_seconds: float = field(default_factory=lambda: _env_float("FARQ_LIVE_TIMEOUT_SECONDS", 8))
    live_concurrency: int = field(default_factory=lambda: _env_int("FARQ_LIVE_CONCURRENCY", 2))
    enable_live: bool = field(default_factory=lambda: _env_bool("FARQ_ENABLE_LIVE", True))
    # Local rows are sellers, not ads, including trades. Live Haraj is what
    # can return the ad text those intents need. This is coverage, not a score.
    live_when_local_has_no_ads_for: tuple[str, ...] = (
        "product",
        "vehicle",
        "property",
        "other",
        "service",
    )
    max_results: int = field(default_factory=lambda: _env_int("FARQ_MAX_RESULTS", 20))
    fresh_days: int = field(default_factory=lambda: _env_int("FARQ_FRESH_DAYS", 30))
    recent_days: int = field(default_factory=lambda: _env_int("FARQ_RECENT_DAYS", 180))


@dataclass(frozen=True)
class PaymentsConfig:
    """Moyasar + subscription plumbing. Every value here is read from the
    environment; nothing is invented. Missing secrets degrade the relevant
    endpoint instead of crashing the whole app, so search/quotes keep working
    even before payments are configured.
    """

    database_url: str | None = field(default_factory=lambda: os.environ.get("DATABASE_URL") or os.environ.get("FARQ_DATABASE_URL"))
    moyasar_secret_key: str | None = field(default_factory=lambda: os.environ.get("MOYASAR_SECRET_KEY"))
    moyasar_publishable_key: str | None = field(default_factory=lambda: os.environ.get("MOYASAR_PUBLISHABLE_KEY"))
    moyasar_webhook_secret: str | None = field(default_factory=lambda: os.environ.get("MOYASAR_WEBHOOK_SECRET"))
    moyasar_base_url: str = field(default_factory=lambda: os.environ.get("MOYASAR_BASE_URL", "https://api.moyasar.com/v1"))
    apple_pay_merchant_id: str | None = field(default_factory=lambda: os.environ.get("APPLE_PAY_MERCHANT_ID"))
    public_base_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "https://taseer.farq.sa"))

    @property
    def payments_configured(self) -> bool:
        return bool(self.moyasar_secret_key and self.moyasar_publishable_key)
