"""Gestion de ubicaciones geograficas (municipios, distritos, landmarks, custom)."""

from __future__ import annotations

import logging
from typing import Any

from .config import Config
from .storage.duckdb_store import DuckDBStore

logger = logging.getLogger("geopulse.locations")


def seed_locations(config: Config, store: DuckDBStore) -> int:
    """Carga el gazetteer + zonas a la tabla `locations` (idempotente)."""
    items: list[dict[str, Any]] = []
    for place in config.gazetteer.get("places", []):
        municipio = place.get("municipio")
        if not municipio and place.get("level") == "municipio":
            municipio = place["name"]
        items.append(
            {
                "name": place["name"],
                "level": place["level"],
                "municipio": municipio,
                "lat": place.get("lat"),
                "lon": place.get("lon"),
                "aliases": place.get("aliases", []),
                "source": "gazetteer",
            }
        )
    for landmark in config.gazetteer.get("landmarks", []):
        items.append(
            {
                "name": landmark["name"],
                "level": "landmark",
                "lat": landmark.get("lat"),
                "lon": landmark.get("lon"),
                "aliases": landmark.get("aliases", []),
                "source": "gazetteer",
            }
        )
    for dist in config.zones.get("distritos_extra", []):
        items.append({"name": dist, "level": "distrito", "source": "zones"})

    count = store.upsert_locations(items)
    logger.info("Ubicaciones sembradas/actualizadas: %d", count)
    return count


def effective_locations(config: Config, store: DuckDBStore | None = None) -> list[dict[str, Any]]:
    """Ubicaciones activas de la base o, si no hay, derivadas del gazetteer."""
    if store is not None:
        try:
            stored = store.get_locations(active_only=True)
            if stored:
                return stored
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudieron leer ubicaciones de la base: %s", exc)

    fallback: list[dict[str, Any]] = []
    for place in config.gazetteer.get("places", []):
        fallback.append(
            {
                "location_id": place["name"].lower(),
                "name": place["name"],
                "level": place["level"],
                "municipio": place.get("municipio"),
                "lat": place.get("lat"),
                "lon": place.get("lon"),
                "aliases": place.get("aliases", []),
            }
        )
    return fallback


def municipio_coordinates(config: Config, store: DuckDBStore | None = None) -> dict[str, tuple[float, float]]:
    """Mapa municipio -> (lat, lon) para el mapa de calor."""
    coords: dict[str, tuple[float, float]] = {}
    for loc in effective_locations(config, store):
        if loc.get("level") == "municipio" and loc.get("lat") is not None and loc.get("lon") is not None:
            key = (loc.get("municipio") or loc["name"]).strip()
            coords.setdefault(key, (float(loc["lat"]), float(loc["lon"])))
    return coords
