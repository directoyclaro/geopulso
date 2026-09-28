"""Esquemas Pydantic y utilidades de taxonomia para el enriquecimiento."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SENTIMENTS = ("positivo", "negativo", "neutro", "mixto")
INTENTIONS = (
    "informativo",
    "promocional",
    "queja",
    "opinion",
    "convocatoria",
    "pregunta",
    "otro",
)


class PostEnrichment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topics: list[str] = Field(default_factory=list)
    sentiment: str = "neutro"
    intention: str = "otro"
    summary: str | None = None
    geo_hints: list[str] = Field(default_factory=list)


class EnrichmentBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[dict[str, Any]] = Field(default_factory=list)


def flatten_taxonomy(taxonomy: dict[str, Any]) -> dict[str, str]:
    """Devuelve {id_hoja: etiqueta}. Incluye categorias como fallback si no hay hijos."""
    leaves: dict[str, str] = {}
    for cat_id, cat in (taxonomy.get("categories") or {}).items():
        children = cat.get("children") or {}
        if not children:
            leaves[cat_id] = cat.get("label", cat_id)
            continue
        for leaf_id, label in children.items():
            leaves[leaf_id] = label if isinstance(label, str) else leaf_id
    return leaves
