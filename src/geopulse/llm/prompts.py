"""Construccion de prompts para el agente enriquecedor (espanol dominicano)."""

from __future__ import annotations

import json
from typing import Any

_SYSTEM_TEMPLATE = """Eres un analista de redes sociales especializado en la provincia \
Bahoruco (Republica Dominicana). Clasificas publicaciones en espanol (con jerga dominicana).

Debes devolver EXCLUSIVAMENTE un objeto JSON valido con esta forma:
{{"results": [{{"id": <entero>, "topics": [<ids>], "sentiment": "<s>", "intention": "<i>", "summary": "<texto breve>"}}]}}

Reglas:
- "topics": 1 a 3 ids de la taxonomia permitida. EVITA "sin_clasificar": usalo solo si de \
verdad no hay ninguna pista; casi todo post menciona algo (lugares, gente, servicios, \
agricultura, religion, deporte, politica, clima).
- "sentiment": uno de {sentiments}.
- "intention": uno de {intentions}.
- "summary": una frase corta en espanol (max 120 caracteres).
- No inventes datos. No anadas texto fuera del JSON.

Ejemplos:
- "Gran feria de la uva en Neiba este sabado, los esperamos en la plaza"
  -> topics ["uva","comercio"], sentiment "positivo", intention "convocatoria"
- "Otra vez el apagon en Tamayo, llevamos tres dias sin luz"
  -> topics ["apagones"], sentiment "negativo", intention "queja"
- "El alcalde inauguro el nuevo acueducto del municipio de Galvan"
  -> topics ["obras","ayuntamiento","agua"], sentiment "positivo", intention "informativo"
- "Misa y procesion de la Virgen en Villa Jaragua"
  -> topics ["catolica","fiestas_patronales"], sentiment "positivo", intention "informativo"
- "Torneo de beisbol este domingo en el play de Los Rios"
  -> topics ["beisbol","pelota_local"], sentiment "neutro", intention "convocatoria"

Taxonomia permitida (id = significado):
{taxonomy}
"""


def build_system_prompt(taxonomy_leaves: dict[str, str]) -> str:
    taxonomy_lines = "\n".join(f"- {leaf}: {label}" for leaf, label in taxonomy_leaves.items())
    return _SYSTEM_TEMPLATE.format(
        sentiments=", ".join(("positivo", "negativo", "neutro", "mixto")),
        intentions=", ".join(
            ("informativo", "promocional", "queja", "opinion", "convocatoria", "pregunta", "otro")
        ),
        taxonomy=taxonomy_lines,
    )


def build_user_prompt(posts: list[dict[str, Any]]) -> str:
    items = [
        {
            "id": idx,
            "texto": (p.get("text") or "")[:600],
            "hashtags": p.get("hashtags") or [],
        }
        for idx, p in enumerate(posts)
    ]
    return "Clasifica estas publicaciones:\n" + json.dumps(items, ensure_ascii=False)


def build_messages(posts: list[dict[str, Any]], taxonomy_leaves: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": build_system_prompt(taxonomy_leaves)},
        {"role": "user", "content": build_user_prompt(posts)},
    ]
