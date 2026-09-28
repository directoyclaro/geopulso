"""Agente descubridor: carga semillas y descubre cuentas locales nuevas."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..config import Config
from ..storage.duckdb_store import DuckDBStore
from .base import logger


def load_seed_sources(config: Config, store: DuckDBStore) -> int:
    """Carga el listado semilla (config/sources_seed.yml) a la tabla sources."""
    sources = config.sources_seed.get("sources", [])
    return store.upsert_sources(sources)


class DiscoveryAgent:
    def __init__(self, config: Config, store: DuckDBStore, min_hits: int = 2) -> None:
        self.config = config
        self.store = store
        self.min_hits = min_hits
        self.keywords = [k.lower() for k in config.keywords]

    def _score(self, name: str, hits: int, verified: bool) -> float:
        base = 0.8 if verified else 0.2
        keyword_bonus = 0.2 if any(k in (name or "").lower() for k in self.keywords) else 0.0
        freq_bonus = min(0.4, 0.04 * hits)
        return round(min(1.0, base + keyword_bonus + freq_bonus), 2)

    def discover_instagram(self, amount: int = 30) -> int:
        """Descubre cuentas que publican con hashtags locales."""
        client = self._login_ig()
        if client is None:
            return 0

        counter: Counter[str] = Counter()
        for tag in self.config.hashtags:
            try:
                medias = client.hashtag_medias_recent(tag, amount=amount)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Descubrimiento: error en #%s: %s", tag, exc)
                continue
            for media in medias:
                username = getattr(getattr(media, "user", None), "username", None)
                if username:
                    counter[username] += 1

        added = 0
        for username, hits in counter.most_common():
            if hits < self.min_hits:
                continue
            self.store.upsert_source(
                {
                    "name": username,
                    "platform": "instagram",
                    "handle": username,
                    "url": f"https://www.instagram.com/{username}/",
                    "type": "descubierta",
                    "verified": False,
                    "status": "dudosa",
                    "score": self._score(username, hits, verified=False),
                }
            )
            added += 1
        logger.info("Descubrimiento IG: %d cuentas candidatas", added)
        return added

    def accounts_helper(self) -> dict[str, Any] | None:
        from .base import AccountManager

        return AccountManager(self.config).current("instagram")

    def _login_ig(self):
        from .base import AccountManager
        from .instagram import build_ig_client

        manager = AccountManager(self.config)
        account = manager.current("instagram")
        if not account:
            logger.warning("Sin cuenta IG para descubrimiento.")
            return None
        proxy = manager.proxy_for(account)
        session_path = manager.session_path("instagram", account)
        try:
            return build_ig_client(self.config, account, proxy, session_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Login IG para descubrimiento fallo: %s", exc)
            return None

    def resolve_locations(self, names: list[str] | None = None) -> dict[str, int]:
        """Resuelve nombres de lugares a pk de Instagram y cachea el resultado."""
        client = self._login_ig()
        if client is None:
            return {"resolved": 0, "total": 0}

        if names is None:
            zones = self.config.zones
            names = [m for m in self.config.municipios]
            names += [m.get("alt_name") for m in zones.get("municipios", []) if m.get("alt_name")]
            names += ["Lago Enriquillo", "Neiba", "Bahoruco"]

        resolved: list[dict[str, Any]] = []
        for name in dict.fromkeys(n for n in names if n):
            try:
                results = client.fbsearch_places(name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudo resolver location %s: %s", name, exc)
                continue
            if results:
                first = results[0]
                resolved.append({"pk": first.pk, "name": name})
                logger.info("Location resuelta: %s -> %s", name, first.pk)

        cache = self.config.settings.resolve(
            f"{self.config.settings.paths.gold_dir}/instagram_locations.json"
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"resolved": len(resolved), "total": len(names)}

    def search_profiles(self, queries: list[str] | None = None, amount: int = 20) -> int:
        """Busca perfiles por palabra clave y los agrega como fuentes candidatas."""
        client = self._login_ig()
        if client is None:
            return 0
        queries = queries or self.config.keywords
        added = 0
        for query in queries:
            try:
                users = client.search_users(query, amount) if hasattr(client, "search_users") else []
            except Exception as exc:  # noqa: BLE001
                logger.warning("Busqueda de perfiles '%s' fallo: %s", query, exc)
                continue
            for user in users:
                username = getattr(user, "username", None)
                if not username:
                    continue
                self.store.upsert_source(
                    {
                        "name": getattr(user, "full_name", username) or username,
                        "platform": "instagram",
                        "handle": username,
                        "url": f"https://www.instagram.com/{username}/",
                        "type": "descubierta",
                        "verified": False,
                        "status": "dudosa",
                        "score": self._score(username, hits=1, verified=False),
                    }
                )
                added += 1
        logger.info("Busqueda de perfiles: %d candidatos", added)
        return added

    def run(self) -> int:
        total = load_seed_sources(self.config, self.store)
        total += self.discover_instagram()
        return total
