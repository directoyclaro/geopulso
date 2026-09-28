"""Agente normalizador: limpia, filtra por idioma, anonimiza y deduplica."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable

from ..config import Config
from ..utils.text import (
    clean_social_text,
    extract_hashtags,
    is_near_duplicate,
    is_spanish,
    keyword_hits,
    normalize_text,
    simhash64,
)

_ANON_SALT = "geopulse"  # solo para anonimizar; no permite re-identificacion


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _raw_id(platform: str, external_id: str | None, url: str | None, text: str) -> str:
    key = f"{platform}|{external_id or ''}|{url or ''}|{text[:160]}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _author_hash(platform: str, author: str | None) -> str | None:
    if not author:
        return None
    return hashlib.sha256(f"{_ANON_SALT}:{platform}:{author}".encode("utf-8")).hexdigest()


class NormalizerAgent:
    def __init__(
        self,
        config: Config,
        existing_simhashes: Iterable[int] | None = None,
        keywords: Iterable[str] | None = None,
    ) -> None:
        self.config = config
        self.ncfg = config.normalizer
        self.keywords = list(keywords) if keywords is not None else config.keywords
        self._seen: list[int] = list(existing_simhashes or [])

    def _is_duplicate(self, fingerprint: int) -> bool:
        for other in self._seen:
            if is_near_duplicate(fingerprint, other, self.ncfg.dedup_simhash_distance):
                return True
        return False

    def normalize(self, raw: dict[str, Any], source: dict[str, Any] | None = None) -> dict[str, Any] | None:
        platform = raw.get("platform") or (source or {}).get("platform") or "unknown"
        text = clean_social_text(raw.get("text") or raw.get("caption") or "")
        if len(text) < self.ncfg.min_text_length:
            return None

        language = raw.get("language") or ("es" if is_spanish(text) else "unknown")
        if self.ncfg.keep_only_spanish and language not in {"es", "unknown"}:
            return None
        if self.ncfg.keep_only_spanish and language == "unknown" and not is_spanish(text):
            return None
        if language == "unknown":
            language = "es"

        fingerprint = simhash64(text)
        if self._is_duplicate(fingerprint):
            return None
        self._seen.append(fingerprint)

        hashtags = raw.get("hashtags") or extract_hashtags(text)
        hashtags = [h.lower().lstrip("#") for h in hashtags]
        hits = keyword_hits(text, self.keywords)

        return {
            "raw_id": raw.get("raw_id") or _raw_id(platform, raw.get("external_id"), raw.get("url"), text),
            "source_id": (source or {}).get("source_id") or raw.get("source_id"),
            "platform": platform,
            "collected_at": raw.get("collected_at") or _utcnow(),
            "posted_at": raw.get("posted_at"),
            "text": text,
            "language": language,
            "author_hash": _author_hash(platform, raw.get("author")),
            "hashtags": hashtags,
            "keywords": hits,
            "location_text": normalize_text(raw.get("location") or ""),
            "url": raw.get("url"),
            "simhash": fingerprint,
            "raw_path": raw.get("raw_path"),
        }

    def process(
        self,
        records: Iterable[dict[str, Any]],
        sources_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        sources_by_id = sources_by_id or {}
        for raw in records:
            source = sources_by_id.get(raw.get("source_id")) if raw.get("source_id") else None
            normalized = self.normalize(raw, source)
            if normalized is not None:
                out.append(normalized)
        return out
