"""Agente geo-mapper: infiere municipio/distrito con evidencia multiple.

Fuentes de evidencia: location explicita, hashtags, texto, NER (spaCy),
landmarks del dominio y prior de la fuente. Todo con score y umbral.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

from ..config import Config
from ..utils.text import normalize_key, normalize_text

logger = logging.getLogger("geopulse.geo")

# Nombres ambiguos que solo cuentan en location/hashtag o si van capitalizados.
_WEAK_TEXT_NAMES = {"mena", "santana", "las canitas", "el palmar"}

# Gentilicios y formas coloquiales -> lugar canonico
_EXTRA_ALIASES = {
    "neibero": "Neiba",
    "neybero": "Neiba",
    "neibera": "Neiba",
    "jaraguense": "Villa Jaragua",
    "jaragüense": "Villa Jaragua",
    "galvanense": "Galván",
    "tamayense": "Tamayo",
}

# Landmarks -> municipio asociado (evidencia media)
_LANDMARK_MUNICIPIO = {
    "las marias": "Neiba",
    "las marias oasis": "Neiba",
    "sierra de neyba": "Neiba",
    "sierra de bahoruco": "Neiba",
    "lago enriquillo": None,  # solo provincia
}

# Gentilicios de provincia
_PROVINCE_DEMONYMS = {"baoruquense", "baorucense", "baoruco"}

_WEIGHTS = {
    "location": 0.85,
    "landmark": 0.65,
    "hashtag": 0.60,
    "text": 0.45,
    "ner": 0.35,
    "demonym": 0.60,
    "source": 0.40,
}

_PROVINCE_SCORE = 0.45
_COUNTRY_SCORE = 0.25


@lru_cache(maxsize=1)
def _load_nlp():
    """Carga el modelo espanol de spaCy (None si no esta disponible)."""
    try:
        import spacy

        return spacy.load("es_core_news_md")
    except Exception as exc:  # noqa: BLE001
        logger.warning("spaCy es_core_news_md no disponible (NER desactivado): %s", exc)
        return None


class GeoMapper:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.gcfg = config.geo
        self._index = self._build_index()

    def _build_index(self) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for place in self.config.gazetteer.get("places", []):
            keys = {place["name"], *place.get("aliases", [])}
            for key in keys:
                index[normalize_key(key)] = place
        # Gentilicios -> lugar existente
        for alias, target in _EXTRA_ALIASES.items():
            place = index.get(normalize_key(target))
            if place:
                index[normalize_key(alias)] = place
        return index

    def _iter_alias_matches(self, field: str, text: str) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        if not text:
            return matches
        normalized = normalize_key(text)
        for key, place in self._index.items():
            if not key or len(key) < 4:
                continue
            pattern = r"(?<!\w)" + re.escape(key) + r"(?!\w)"
            if not re.search(pattern, normalized):
                continue
            if field == "text" and key in _WEAK_TEXT_NAMES:
                if not re.search(r"(?<!\w)" + re.escape(place["name"]) + r"(?!\w)", text):
                    continue
            matches.append(place)
        return matches

    @staticmethod
    def _score_candidate(scores: dict[str, float], place: dict[str, Any], weight: float) -> None:
        name = place["name"]
        scores[name] = min(0.98, scores.get(name, 0.0) + weight)

    def _ner_entities(self, text: str) -> list[str]:
        nlp = _load_nlp()
        if nlp is None or not text:
            return []
        try:
            doc = nlp(text[:600])
        except Exception:  # noqa: BLE001
            return []
        return [ent.text for ent in doc.ents if ent.label_ in {"LOC", "GPE"}]

    def map_post(self, post: dict[str, Any], source: dict[str, Any] | None = None) -> dict[str, Any]:
        scores: dict[str, float] = {}
        evidence: dict[str, str] = {}
        text = post.get("text", "") or ""
        location = post.get("location_text", "") or ""
        hashtags = " ".join(post.get("hashtags") or [])

        for place in self._iter_alias_matches("location", location):
            self._score_candidate(scores, place, _WEIGHTS["location"])
            evidence.setdefault(place["name"], "location")

        # Landmarks del dominio (texto o location)
        haystack = normalize_key(f"{text} {location} {hashtags}")
        for phrase, muni in _LANDMARK_MUNICIPIO.items():
            if phrase in haystack and muni:
                place = self._index.get(normalize_key(muni))
                if place:
                    self._score_candidate(scores, place, _WEIGHTS["landmark"])
                    evidence.setdefault(place["name"], "landmark")

        # Gentilicios (neibero, jaraguense, galvanense, ...)
        for alias, target in _EXTRA_ALIASES.items():
            if re.search(r"(?<!\w)" + re.escape(normalize_key(alias)) + r"(?!\w)", haystack):
                place = self._index.get(normalize_key(target))
                if place:
                    self._score_candidate(scores, place, _WEIGHTS["demonym"])
                    evidence.setdefault(place["name"], "demonym")

        for place in self._iter_alias_matches("hashtag", hashtags):
            self._score_candidate(scores, place, _WEIGHTS["hashtag"])
            evidence.setdefault(place["name"], "hashtag")

        for place in self._iter_alias_matches("text", text):
            self._score_candidate(scores, place, _WEIGHTS["text"])
            evidence.setdefault(place["name"], "text")

        # NER sobre el texto
        for entity in self._ner_entities(text):
            for place in self._iter_alias_matches("text", entity):
                self._score_candidate(scores, place, _WEIGHTS["ner"])
                evidence.setdefault(place["name"], "ner")

        if source and source.get("municipio"):
            for place in self._iter_alias_matches("location", source["municipio"]):
                self._score_candidate(scores, place, _WEIGHTS["source"])
                evidence.setdefault(place["name"], "source")

        province_hit = self._province_hit(post)

        best_name, best_score = self._best(scores)
        result = {
            "raw_id": post["raw_id"],
            "municipality": None,
            "distrito": None,
            "province": "Bahoruco",
            "country": "DO",
            "confidence": 0.0,
            "method": None,
        }

        if best_name is not None:
            place = self._index[normalize_key(best_name)]
            if place["level"] == "distrito" and best_score >= self.gcfg.min_confidence_distrito:
                result["distrito"] = place["name"]
                result["municipality"] = place.get("municipio")
                result["confidence"] = round(best_score, 2)
                result["method"] = evidence.get(best_name, "text")
                return result
            if best_score >= self.gcfg.min_confidence_municipio:
                result["municipality"] = place.get("municipio") or place["name"]
                result["confidence"] = round(best_score, 2)
                result["method"] = evidence.get(best_name, "text")
                return result

        if source and source.get("municipio"):
            place = self._index.get(normalize_key(source["municipio"]))
            if place:
                result["municipality"] = place.get("municipio") or place["name"]
                result["confidence"] = 0.55
                result["method"] = "source_prior"
                return result

        if province_hit:
            result["confidence"] = _PROVINCE_SCORE
            result["method"] = "province"
        elif self.gcfg.allow_country_fallback:
            result["confidence"] = _COUNTRY_SCORE
            result["method"] = "country_fallback"
        return result

    @staticmethod
    def _province_hit(post: dict[str, Any]) -> bool:
        haystack = normalize_key(
            " ".join(
                [
                    post.get("text", ""),
                    post.get("location_text", ""),
                    " ".join(post.get("hashtags") or []),
                    " ".join(post.get("keywords") or []),
                ]
            )
        )
        if "bahoruco" in haystack or "baoruco" in haystack:
            return True
        return any(dem in haystack for dem in _PROVINCE_DEMONYMS)

    @staticmethod
    def _best(scores: dict[str, float]) -> tuple[str | None, float]:
        if not scores:
            return None, 0.0
        name = max(scores, key=scores.get)
        return name, scores[name]

    def process(self, posts: list[dict[str, Any]], sources_by_id: dict[str, dict] | None = None) -> list[dict]:
        sources_by_id = sources_by_id or {}
        return [self.map_post(p, sources_by_id.get(p.get("source_id"))) for p in posts]
