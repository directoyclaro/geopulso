"""Gestion de temas de conversacion (mapeados a la taxonomia)."""

from __future__ import annotations

import logging
from typing import Any

from .config import Config
from .llm.schemas import flatten_taxonomy
from .storage.duckdb_store import DuckDBStore

logger = logging.getLogger("geopulse.topics")


def seed_topics(config: Config, store: DuckDBStore) -> int:
    """Carga las hojas de la taxonomia a la tabla `topics` (idempotente)."""
    leaves = flatten_taxonomy(config.taxonomy)
    count = 0
    for leaf_id, label in leaves.items():
        store.upsert_topic(
            {
                "topic_id": leaf_id,
                "label": label,
                "taxonomy_id": leaf_id,
                "keywords": [],
                "source": "taxonomy",
            }
        )
        count += 1
    logger.info("Temas sembrados/actualizados: %d", count)
    return count


def effective_topics(config: Config, store: DuckDBStore | None = None, selected_only: bool = False) -> list[dict[str, Any]]:
    if store is not None:
        try:
            topics = store.get_topics(active_only=True, selected_only=selected_only)
            if topics:
                return topics
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudieron leer temas de la base: %s", exc)
    leaves = flatten_taxonomy(config.taxonomy)
    return [{"topic_id": k, "label": v, "taxonomy_id": k, "selected": True} for k, v in leaves.items()]
