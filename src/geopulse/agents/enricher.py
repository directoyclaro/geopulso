"""Agente enriquecedor: clasifica temas, sentimiento e intencion.

Usa el LLM local (Ollama) cuando esta disponible y, si no, un enriquecedor
heuristico de respaldo basado en lexico, para no romper el pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from ..config import Config
from ..llm.ollama_client import OllamaClient
from ..llm.prompts import build_messages
from ..llm.schemas import SENTIMENTS, flatten_taxonomy
from ..storage.duckdb_store import DuckDBStore
from ..utils.text import normalize_key

logger = logging.getLogger("geopulse.enricher")

# Lexico -> id de taxonomia (fallback sin LLM)
_TOPIC_LEXICON: dict[str, list[str]] = {
    "uva": ["uva", "uvas", "vendimia", "vinedo", "viñedo"],
    "platano": ["platano", "plátano", "platanal"],
    "cafe": ["cafe", "café", "cafetal", "caficultor"],
    "mango": ["mango", "mangos"],
    "pesca": ["pesca", "pescador", "peces"],
    "sal": ["sal", "salinas", "saladero"],
    "ganaderia": ["ganado", "ganaderia", "reses", "cerdos"],
    "comercio": ["mercado", "comercio", "feria", "venta"],
    "empleo": ["empleo", "trabajo", "vacante", "contrato"],
    "lago_enriquillo": ["lago enriquillo", "enriquillo"],
    "sierra_neiba": ["sierra de neyba", "sierra de bahoruco", "montana", "montaña"],
    "las_marias": ["las marias", "las marías"],
    "incendios": ["incendio", "fuego", "forestal"],
    "conservacion": ["reserva", "conservacion", "ambiente", "ecologia"],
    "catolica": ["parroquia", "iglesia catolica", "misa", "sacerdote"],
    "evangelica": ["evangelico", "evangélico", "culto", "templo"],
    "fiestas_patronales": ["patronales", "fiesta patronal"],
    "beisbol": ["beisbol", "béisbol", "pelota invernal", "lidom"],
    "pelota_local": ["pelota", "liga", "torneo"],
    "futbol": ["futbol", "fútbol", "balompie"],
    "bachata": ["bachata", "bachatero"],
    "merengue": ["merengue"],
    "dembow": ["dembow"],
    "tipico": ["tipico", "típico", "acordeon"],
    "carnaval": ["carnaval", "cachua", "diablo cojuelo"],
    "ayuntamiento": ["alcaldia", "alcaldía", "ayuntamiento", "alcalde"],
    "gobernacion": ["gobernacion", "gobernadora", "gobernador"],
    "congreso": ["senador", "diputado", "senadora", "diputada", "congreso"],
    "obras": ["obra", "construccion", "construcción", "inaugura", "asfalto"],
    "elecciones": ["elecciones", "votar", "voto", "campana"],
    "apagones": ["apagon", "apagón", "energia", "energía", "edeesur", "luz"],
    "agua": ["agua", "acueducto", "inapa", "tuberia"],
    "transporte": ["transporte", "motoconcho", "guagua", "pasola"],
    "basura": ["basura", "vertedero", "recogida"],
    "salud": ["hospital", "medico", "médico", "salud", "clinica", "enfermero"],
    "educacion": ["escuela", "liceo", "estudiantes", "docente", "universidad", "adp"],
    "delincuencia": ["robo", "delincuencia", "atracador", "policia", "policía"],
    "accidentes": ["accidente", "choque", "herido", "fallecido"],
    "justicia": ["tribunal", "juez", "fiscalia", "fiscalía", "condena"],
    "migracion": ["migracion", "migración", "haiti", "haití", "frontera", "migrante"],
    "remesas": ["remesa", "remesas", "dinero"],
    "religion_comunidad": ["actividad", "comunidad", "junta de vecinos"],
    "gastronomia": ["comida", "gastronomia", "gastronomía", "plato", "sancocho", "mangú", "mangu"],
    "clima": ["lluvia", "inundacion", "inundación", "sequia", "sequía", "huracan", "huracán", "tormenta"],
}

_POSITIVE = ["exito", "éxito", "logro", "felicidad", "gracias", "bueno", "gran", "mejor", "avance", "apoyo", "bendicion"]
_NEGATIVE = ["apagon", "apagón", "falta", "problema", "denuncia", "malo", "peligro", "robo", "accidente", "crisis", "pobreza"]
_QUEJA = ["no hay", "falta", "denuncia", "reclamo", "abandonado", "mal estado", "exigimos"]
_PROMO = ["vendo", "oferta", "disponible", "promocion", "promoción", "precio", "descuento"]
_CONVOCA = ["invita", "invitamos", "convoca", "este sabado", "este sábado", "actividad", "reunion", "reunión"]


class EnricherAgent:
    def __init__(self, config: Config, store: DuckDBStore, llm: OllamaClient | None = None) -> None:
        self.config = config
        self.store = store
        self.llm = llm or OllamaClient(
            base_url=config.llm.base_url,
            model=config.llm.model,
            timeout=config.llm.timeout_seconds,
            keep_alive=config.llm.keep_alive,
            temperature=config.llm.temperature,
        )
        self.leaves = flatten_taxonomy(config.taxonomy)
        self.valid = set(self.leaves)

    # --- Heuristico ---
    def _heuristic_topics(self, post: dict[str, Any]) -> list[str]:
        haystack = normalize_key(
            " ".join([post.get("text", ""), " ".join(post.get("hashtags") or [])])
        )
        topics: list[str] = []
        for topic, words in _TOPIC_LEXICON.items():
            if topic not in self.valid:
                continue
            if any(normalize_key(w) in haystack for w in words):
                topics.append(topic)
        return topics[:3] or ["sin_clasificar"]

    @staticmethod
    def _heuristic_sentiment(text: str) -> str:
        low = normalize_key(text)
        pos = sum(1 for w in _POSITIVE if normalize_key(w) in low)
        neg = sum(1 for w in _NEGATIVE if normalize_key(w) in low)
        if pos and neg:
            return "mixto"
        if neg:
            return "negativo"
        if pos:
            return "positivo"
        return "neutro"

    @staticmethod
    def _heuristic_intention(text: str) -> str:
        low = normalize_key(text)
        if any(normalize_key(w) in low for w in _QUEJA):
            return "queja"
        if any(normalize_key(w) in low for w in _PROMO):
            return "promocional"
        if any(normalize_key(w) in low for w in _CONVOCA):
            return "convocatoria"
        return "informativo"

    def _heuristic(self, post: dict[str, Any]) -> dict[str, Any]:
        return {
            "raw_id": post["raw_id"],
            "topics": self._heuristic_topics(post),
            "sentiment": self._heuristic_sentiment(post.get("text", "")),
            "intention": self._heuristic_intention(post.get("text", "")),
            "summary": None,
            "method": "heuristic",
            "model": None,
        }

    # --- LLM ---
    def _parse_llm_results(
        self, data: dict[str, Any] | None, batch: list[dict[str, Any]]
    ) -> dict[int, dict[str, Any]]:
        parsed: dict[int, dict[str, Any]] = {}
        if not data:
            return parsed
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return parsed
        for item in results:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(batch):
                continue
            topics = [t for t in (item.get("topics") or []) if t in self.valid] or ["sin_clasificar"]
            sentiment = item.get("sentiment") if item.get("sentiment") in SENTIMENTS else "neutro"
            summary = item.get("summary")
            if isinstance(summary, str):
                summary = summary[:160]
            parsed[idx] = {
                "topics": topics[:3],
                "sentiment": sentiment,
                "intention": item.get("intention") or "otro",
                "summary": summary,
            }
        return parsed

    def _enrich_batch_with_llm(self, batch: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        messages = build_messages(batch, self.leaves)
        data = self.llm.chat_json(messages)
        return self._parse_llm_results(data, batch)

    def enrich(self, limit: int = 500) -> dict[str, int]:
        posts = self.store.get_pending_enrichment(limit)
        if not posts:
            return {"pending": 0, "enriched": 0, "llm": 0, "heuristic": 0}

        use_llm = self.config.llm.enabled and self.llm.is_available()
        if not use_llm:
            logger.info("Ollama no disponible; usando enriquecimiento heuristico.")
            if not self.config.llm.fallback_heuristic:
                return {"pending": len(posts), "enriched": 0, "llm": 0, "heuristic": 0}

        llm_count = 0
        heur_count = 0
        stored_total = 0
        size = max(1, self.config.llm.batch_size)

        for start in range(0, len(posts), size):
            batch = posts[start : start + size]
            parsed = self._enrich_batch_with_llm(batch) if use_llm else {}
            batch_records: list[dict[str, Any]] = []
            for idx, post in enumerate(batch):
                result = parsed.get(idx)
                if result:
                    # Si el LLM no clasifico, intentar con el heurístico antes de rendirse.
                    method = "llm"
                    if result.get("topics") == ["sin_clasificar"]:
                        heur_topics = self._heuristic_topics(post)
                        if heur_topics != ["sin_clasificar"]:
                            result = {**result, "topics": heur_topics}
                            method = "llm+heuristic"
                    batch_records.append(
                        {"raw_id": post["raw_id"], "method": method, "model": self.llm.model, **result}
                    )
                    llm_count += 1
                else:
                    batch_records.append(self._heuristic(post))
                    heur_count += 1
            # Persistir por lote: si el proceso cae, no se pierde el progreso.
            stored_total += self.store.insert_enriched(batch_records)

        logger.info("Enriquecidos %d (llm=%d, heuristic=%d)", stored_total, llm_count, heur_count)
        return {"pending": len(posts), "enriched": stored_total, "llm": llm_count, "heuristic": heur_count}
