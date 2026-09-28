"""Pipeline de ingesta: collect -> normalize -> geo -> store."""

from __future__ import annotations

from typing import Any

from .agents.geo import GeoMapper
from .agents.normalizer import NormalizerAgent
from .config import Config, get_config
from .keywords import effective_keywords
from .storage.duckdb_store import DuckDBStore


def build_store(config: Config | None = None) -> DuckDBStore:
    config = config or get_config()
    config.ensure_dirs()
    store = DuckDBStore(config.db_path)
    store.init_schema()
    return store


def _sources_by_id(store: DuckDBStore) -> dict[str, dict[str, Any]]:
    return {s["source_id"]: s for s in store.get_sources()}


def _existing_simhashes(store: DuckDBStore) -> list[int]:
    rows = store.query("SELECT simhash FROM posts WHERE simhash IS NOT NULL")
    return [r[0] for r in rows]


def _process_records(config: Config, store: DuckDBStore, raw: list[dict]) -> dict[str, int]:
    """Normaliza, almacena y geolocaliza una lista de registros crudos."""
    sources = _sources_by_id(store)
    normalizer = NormalizerAgent(
        config,
        existing_simhashes=_existing_simhashes(store),
        keywords=effective_keywords(config, store),
    )
    posts = normalizer.process(raw, sources)
    inserted = store.insert_posts(posts)
    geo = GeoMapper(config)
    geo_records = geo.process(posts, sources)
    store.insert_posts_geo(geo_records)
    return {"raw": len(raw), "normalized": len(posts), "stored": inserted, "geocoded": len(geo_records)}


def search_content(config: Config, store: DuckDBStore, query: str, amount: int = 20) -> dict[str, int]:
    """Busca contenido por palabra clave (Instagram explore search)."""
    from .collectors.instagram_web import InstagramWebCollector

    collector = InstagramWebCollector(
        config, store, amount=amount, queries=[query], include_config=False
    )
    return _process_records(config, store, collector.run())


def search_keywords(config: Config, store: DuckDBStore, amount: int = 10, limit: int = 25) -> dict[str, int]:
    """Busca contenido para todas las keywords activas (temas/etiquetas)."""
    from .collectors.instagram_web import InstagramWebCollector

    terms = [k["term"] for k in store.get_keywords(active_only=True)][:limit]
    if not terms:
        return {"terms": 0, "raw": 0, "normalized": 0, "stored": 0, "geocoded": 0}
    collector = InstagramWebCollector(config, store, amount=amount, queries=terms, include_config=False)
    result = _process_records(config, store, collector.run())
    result["terms"] = len(terms)
    return result


def discover_places(
    config: Config,
    store: DuckDBStore,
    platforms: tuple[str, ...] = ("instagram", "facebook"),
    per_place: int = 8,
    reset: bool = False,
) -> dict:
    """Descubre perfiles/paginas publicas por municipio y distrito."""
    from .collectors.profile_discovery import discover_places as _discover

    return _discover(config, store, platforms=platforms, per_place=per_place, reset=reset)


def scan_profile(
    config: Config,
    store: DuckDBStore,
    handle: str,
    amount: int = 30,
    keywords: list[str] | None = None,
) -> dict[str, int]:
    """Recorre los posts de un perfil publico, opcionalmente filtrados por keywords."""
    from .collectors.instagram_web import InstagramWebCollector

    collector = InstagramWebCollector(
        config,
        store,
        amount=amount,
        profiles=[handle],
        include_config=False,
        keyword_filter=keywords,
    )
    return _process_records(config, store, collector.run())


def discover_profiles(config: Config, store: DuckDBStore, term: str, amount: int = 20) -> dict:
    """Busca perfiles publicos por termino y los registra como fuentes candidatas."""
    from .collectors.base import AccountManager
    from .collectors.instagram_web import search_public_profiles

    manager = AccountManager(config)
    account = manager.current("instagram")
    if not account:
        raise RuntimeError("Sin cuenta de Instagram en accounts.yml")
    session_path = manager.session_path("instagram", account)
    users = search_public_profiles(config, account, session_path, term, amount=amount)

    added = 0
    for user in users:
        store.upsert_source(
            {
                "name": user.get("full_name") or user["username"],
                "platform": "instagram",
                "handle": user["username"],
                "url": f"https://www.instagram.com/{user['username']}/",
                "type": "descubierta",
                "verified": bool(user.get("verified")),
                "status": "dudosa",
                "score": 0.4,
            }
        )
        added += 1
    return {"found": len(users), "added": added, "users": [u["username"] for u in users]}


def collect_and_process(config: Config, store: DuckDBStore, platform: str, amount: int = 30) -> dict[str, int]:
    """Ejecuta un ciclo completo para una plataforma."""
    if platform == "instagram":
        if config.collection.instagram_backend == "api":
            from .collectors.instagram import InstagramCollector

            collector = InstagramCollector(config, store, amount=amount)
        else:
            from .collectors.instagram_web import InstagramWebCollector

            collector = InstagramWebCollector(config, store, amount=amount)
    elif platform == "facebook":
        from .collectors.facebook import FacebookCollector

        collector = FacebookCollector(config, store)
    else:
        raise ValueError(f"Plataforma no soportada: {platform}")

    return _process_records(config, store, collector.run())


def run_discovery(config: Config, store: DuckDBStore) -> int:
    from .collectors.discovery import DiscoveryAgent

    return DiscoveryAgent(config, store).run()


def regeo_all(config: Config, store: DuckDBStore) -> dict[str, int]:
    """Recalcula la geolocalizacion de todos los posts existentes."""
    cols = ["raw_id", "text", "hashtags", "keywords", "location_text", "source_id"]
    rows = store.query(f"SELECT {', '.join(cols)} FROM posts")
    posts = [dict(zip(cols, row)) for row in rows]
    sources = _sources_by_id(store)
    geo_records = GeoMapper(config).process(posts, sources)
    store.insert_posts_geo(geo_records)
    resolved = sum(1 for g in geo_records if g.get("municipality"))
    return {"posts": len(posts), "resolved": resolved}


def run_trends(config: Config, store: DuckDBStore) -> dict[str, int]:
    from .agents.trends import TrendDetector

    return TrendDetector(config, store).compute()


def run_report(config: Config, store: DuckDBStore) -> dict[str, str]:
    from .agents.reporter import ReporterAgent

    return {"report": str(ReporterAgent(config, store).generate())}


def run_enrichment(config: Config, store: DuckDBStore, limit: int = 500) -> dict[str, object]:
    """Enriquece posts pendientes y recalcula preferencias por zona."""
    from .agents.enricher import EnricherAgent
    from .agents.preferences import PreferenceProfiler

    result = dict(EnricherAgent(config, store).enrich(limit=limit))
    result["preferences"] = PreferenceProfiler(config, store).run()
    return result
