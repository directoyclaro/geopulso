"""Perfilador de gustos/preferencias por zona a partir del contenido enriquecido."""

from __future__ import annotations

import logging

from ..config import Config
from ..storage.duckdb_store import DuckDBStore

logger = logging.getLogger("geopulse.preferences")


class PreferenceProfiler:
    def __init__(self, config: Config, store: DuckDBStore) -> None:
        self.config = config
        self.store = store

    def run(self, weeks: int = 8) -> dict[str, int]:
        rows = self.store.refresh_preferences(weeks=weeks)
        zones = self.store.query("SELECT COUNT(DISTINCT zona) FROM preferences")[0][0]
        logger.info("Preferencias recalculadas: %d filas, %d zonas", rows, zones)
        return {"rows": rows, "zones": int(zones)}

    def top_by_zone(self, zona: str, limit: int = 10) -> list[tuple]:
        return self.store.query(
            "SELECT categoria, peso FROM preferences WHERE zona = ? ORDER BY peso DESC LIMIT ?",
            [zona, limit],
        )
