"""Despacho de trabajos de la cola."""

from __future__ import annotations

import logging
from typing import Any

from ..config import Config
from ..pipeline import (
    collect_and_process,
    collect_comments,
    discover_places,
    regeo_all,
    run_discovery,
    run_enrichment,
    run_report,
    run_trends,
    search_keywords,
)
from ..storage.duckdb_store import DuckDBStore

logger = logging.getLogger("geopulse.jobs")


def run_job(config: Config, store: DuckDBStore, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if job_type == "collect_instagram":
        return collect_and_process(config, store, "instagram", amount=payload.get("amount", 30))
    if job_type == "collect_facebook":
        return collect_and_process(config, store, "facebook")
    if job_type == "discover":
        return {"discovered": run_discovery(config, store)}
    if job_type == "enrich":
        return run_enrichment(config, store, limit=payload.get("limit", 500))
    if job_type == "trends":
        return run_trends(config, store)
    if job_type == "report":
        return run_report(config, store)
    if job_type == "regeo":
        return regeo_all(config, store)
    if job_type == "collect_comments":
        return collect_comments(config, store, payload["target"], amount=payload.get("amount", 50))
    if job_type == "search_keywords":
        return search_keywords(
            config,
            store,
            amount=payload.get("amount", 10),
            limit=payload.get("limit", 25),
        )
    if job_type == "discover_places":
        platforms = tuple(payload.get("platforms", ["instagram", "facebook"]))
        return discover_places(
            config,
            store,
            platforms=platforms,
            per_place=payload.get("per_place", 8),
            reset=payload.get("reset", False),
        )
    logger.warning("Tipo de trabajo desconocido: %s", job_type)
    return {}
