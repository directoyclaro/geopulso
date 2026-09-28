"""Base comun para colectores: rate limiting, cuentas y ciclo de recoleccion."""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..config import Config
from ..storage.duckdb_store import DuckDBStore
from ..storage.parquet import write_bronze
from ..utils.retry import retry_call

logger = logging.getLogger("geopulse.collectors")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RateLimiter:
    """Limita peticiones por hora y aplica pausas con jitter."""

    def __init__(self, max_per_hour: int, delay_range: tuple[int, int]) -> None:
        self.max_per_hour = max_per_hour
        self.delay_range = delay_range
        self._timestamps: deque[float] = deque()

    def wait(self) -> None:
        now = time.time()
        while self._timestamps and now - self._timestamps[0] > 3600:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_per_hour:
            sleep_for = 3600 - (now - self._timestamps[0]) + 1
            logger.warning("Limite por hora alcanzado; esperando %.0fs", sleep_for)
            time.sleep(max(1.0, sleep_for))
        lo, hi = self.delay_range
        time.sleep(random.uniform(lo, hi))
        self._timestamps.append(time.time())


class AccountManager:
    """Carga y rota cuentas quemables desde config/secrets/accounts.yml."""

    REQUIRED_FIELDS = ("username",)

    def __init__(self, config: Config) -> None:
        self.config = config
        self.accounts = self._load()
        self._indices: dict[str, int] = {}
        self.proxies = list(config.collection.proxies)
        self._proxy_index = 0

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.settings.resolve(self.config.settings.paths.secrets_dir) / "accounts.yml"
        if not path.exists():
            logger.warning("No existe %s; los colectores con login no podran autenticarse.", path)
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return {platform: list(items or []) for platform, items in data.items()}

    def has(self, platform: str) -> bool:
        return bool(self.accounts.get(platform))

    def current(self, platform: str) -> dict[str, Any] | None:
        items = self.accounts.get(platform)
        if not items:
            return None
        idx = self._indices.get(platform, 0) % len(items)
        return items[idx]

    def rotate(self, platform: str) -> dict[str, Any] | None:
        items = self.accounts.get(platform)
        if not items:
            return None
        self._indices[platform] = (self._indices.get(platform, 0) + 1) % len(items)
        logger.info("Rotando cuenta de %s -> indice %d", platform, self._indices[platform])
        return self.current(platform)

    def session_path(self, platform: str, account: dict[str, Any]) -> Path:
        secrets = self.config.settings.resolve(self.config.settings.paths.secrets_dir)
        name = account.get("session_file") or f"{platform}_{account.get('username', 'default')}.json"
        return secrets / name

    def proxy_for(self, account: dict[str, Any] | None) -> str | None:
        """Proxy de la cuenta o, si no tiene, rota entre los proxies globales."""
        if account and account.get("proxy"):
            return account["proxy"]
        if not self.proxies:
            return None
        proxy = self.proxies[self._proxy_index % len(self.proxies)]
        self._proxy_index += 1
        return proxy

    def validate(self) -> dict[str, list[str]]:
        """Valida la estructura de accounts.yml por plataforma. Devuelve problemas."""
        problems: dict[str, list[str]] = {}
        if not self.accounts:
            problems["__file__"] = ["No existe config/secrets/accounts.yml o esta vacio."]
            return problems
        for platform, items in self.accounts.items():
            issues: list[str] = []
            if not items:
                issues.append("Sin cuentas definidas.")
            for i, acc in enumerate(items):
                for field in self.REQUIRED_FIELDS:
                    if not acc.get(field):
                        issues.append(f"Cuenta #{i + 1}: falta '{field}'.")
                if not acc.get("password") and not acc.get("sessionid") and not acc.get("cookies"):
                    issues.append(f"Cuenta #{i + 1}: necesita 'password' o 'sessionid' (IG) / 'cookies' (FB).")
            if issues:
                problems[platform] = issues
        return problems


class BaseCollector:
    """Contrato de colector. Las subclases implementan `collect`."""

    platform: str = "base"

    def __init__(self, config: Config, store: DuckDBStore | None = None) -> None:
        self.config = config
        self.store = store
        self.accounts = AccountManager(config)
        self.limiter = RateLimiter(
            config.collection.max_requests_per_hour,
            tuple(config.collection.request_delay_seconds),
        )
        self.max_retries = config.collection.max_retries
        self.backoff = tuple(config.collection.backoff_seconds)
        self.max_posts_per_source = config.collection.max_posts_per_source

    def retry(self, fn, description: str = ""):
        return retry_call(
            fn,
            retries=self.max_retries,
            backoff_range=self.backoff,
            logger=logger,
            description=description,
        )

    def collect(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def run(self) -> list[dict[str, Any]]:
        run_id = f"{self.platform}-{utcnow().strftime('%Y%m%d%H%M%S')}"
        started = utcnow()
        records: list[dict[str, Any]] = []
        status = "ok"
        error = None
        try:
            records = self.collect()
            write_bronze(self.config.settings.resolve(self.config.settings.paths.bronze_dir), self.platform, records)
        except Exception as exc:  # noqa: BLE001 - se registra y se reporta el error
            status = "error"
            error = str(exc)
            logger.exception("Fallo en colector %s: %s", self.platform, exc)
        finally:
            if self.store:
                self.store.record_run(run_id, "collect", self.platform, status, len(records), error, started)
        return records
