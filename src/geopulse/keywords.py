"""Gestion de palabras clave: manuales y sugeridas por tendencias."""

from __future__ import annotations

import logging

from .config import Config
from .llm.schemas import flatten_taxonomy
from .storage.duckdb_store import DuckDBStore

logger = logging.getLogger("geopulse.keywords")


def effective_keywords(config: Config, store: DuckDBStore | None = None) -> list[str]:
    """Keywords de config + las activas en la base, sin duplicados."""
    terms: list[str] = list(config.keywords)
    if store is not None:
        try:
            terms += [k["term"] for k in store.get_keywords(active_only=True)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudieron leer keywords de la base: %s", exc)
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(term.strip())
    return out


def suggest_from_trends(config: Config, store: DuckDBStore, limit: int = 20) -> list[str]:
    """Propone keywords a partir de tendencias, temas mas usados, hashtags y lugares."""
    labels = flatten_taxonomy(config.taxonomy)
    suggestions: list[str] = []

    # 1) Temas en tendencia (bursts primero) -> etiqueta legible
    rows = store.query(
        """
        SELECT tema, COUNT(*) AS n FROM trends
        GROUP BY 1 ORDER BY MAX(es_burst) DESC, SUM(volumen) DESC LIMIT ?
        """,
        [limit],
    )
    for tema, _ in rows:
        if tema in {"sin_clasificar", "otros"}:
            continue
        suggestions.append(labels.get(tema, tema.replace("_", " ")))

    # 2) Hashtags mas frecuentes en los posts ya recolectados
    try:
        tag_rows = store.query(
            """
            SELECT tag, COUNT(*) AS n
            FROM (SELECT unnest(hashtags) AS tag FROM posts)
            GROUP BY 1 ORDER BY 2 DESC LIMIT ?
            """,
            [limit],
        )
        suggestions += [tag for tag, _ in tag_rows]
    except Exception:  # noqa: BLE001
        pass

    # 3) Lugares del dominio
    suggestions += config.municipios
    suggestions += ["Lago Enriquillo", "Sierra de Neyba", "Las Marías"]

    # 4) Keywords ya detectadas en los posts y que no estan registradas
    try:
        kw_rows = store.query(
            """
            SELECT kw, COUNT(*) AS n
            FROM (SELECT unnest(keywords) AS kw FROM posts)
            GROUP BY 1 ORDER BY 2 DESC LIMIT ?
            """,
            [limit],
        )
        suggestions += [kw for kw, _ in kw_rows]
    except Exception:  # noqa: BLE001
        pass

    seen: set[str] = set()
    out: list[str] = []
    for term in suggestions:
        term = (term or "").strip()
        key = term.lower()
        if term and key not in seen:
            seen.add(key)
            out.append(term)
    return out[:limit]


def add_trend_keywords(config: Config, store: DuckDBStore, limit: int = 20) -> int:
    """Registra como keywords las sugerencias derivadas de tendencias."""
    terms = suggest_from_trends(config, store, limit=limit)
    return store.insert_keywords([{"term": t, "source": "trend"} for t in terms])
