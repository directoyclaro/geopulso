"""Utilidades de texto: normalizacion, hashtags, simhash y deteccion de idioma."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

_HASHTAG_RE = re.compile(r"#([\wáéíóúñü]+)", re.IGNORECASE | re.UNICODE)
_MENTION_RE = re.compile(r"@([\w.]+)", re.UNICODE)
_URL_RE = re.compile(r"https?://\S+")
_TOKEN_RE = re.compile(r"[\wáéíóúñü]+", re.IGNORECASE | re.UNICODE)
_WS_RE = re.compile(r"\s+")

_SPANISH_STOPWORDS = {
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
    "un", "para", "con", "no", "una", "su", "al", "lo", "como", "más", "mas",
    "pero", "sus", "le", "ya", "o", "este", "sí", "si", "porque", "esta",
    "entre", "cuando", "muy", "sin", "sobre", "también", "tambien", "me",
    "hasta", "hay", "donde", "quien", "desde", "todo", "nos", "durante",
    "son", "es", "está", "esta", "fue", "ser", "tiene", "hacer",
}

_DOMINICAN_MARKERS = {
    "mano", "tíguere", "tiguere", "klk", "qué lo qué", "que lo que", "pai",
    "pana", "chévere", "chin", "vaina", "tato", "deme", "dimo", "ay",
    "compai", "locombia", "bacano", "teteo", "jevi", "cuero", "enrrollado",
}


def strip_accents(text: str) -> str:
    """Elimina acentos manteniendo las letras base."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    """Limpia espacios y normaliza caracteres invisibles."""
    if not text:
        return ""
    text = text.replace("\u200b", " ").replace("\ufeff", " ")
    return _WS_RE.sub(" ", text).strip()


def normalize_key(text: str) -> str:
    """Forma canonica para comparar toponimos/alias (minusculas sin acentos)."""
    return strip_accents(normalize_text(text)).lower()


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def extract_hashtags(text: str) -> list[str]:
    return [h.lower() for h in _HASHTAG_RE.findall(text or "")]


def extract_mentions(text: str) -> list[str]:
    return [m.lower() for m in _MENTION_RE.findall(text or "")]


def remove_urls(text: str) -> str:
    return _URL_RE.sub(" ", text or "")


_SOCIAL_NOISE = [
    re.compile(r"\bver m[aá]s\b", re.IGNORECASE),
    re.compile(r"\bver menos\b", re.IGNORECASE),
    re.compile(r"\bsee more\b", re.IGNORECASE),
    re.compile(
        r"\bhace (un|una|\d+) (d[ií]a|d[ií]as|hora|horas|minuto|minutos|semana|semanas|mes|meses)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d+\s*(h|min|d)\b", re.IGNORECASE),
]


def clean_social_text(text: str) -> str:
    """Quita ruido de UI de redes sociales (timestamps, 'Ver mas', etc.)."""
    cleaned = text or ""
    for pattern in _SOCIAL_NOISE:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = cleaned.replace("·", " ")
    return normalize_text(cleaned)


def _hash64(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def simhash64(text: str) -> int:
    """SimHash de 64 bits sobre tokens (peso 1)."""
    tokens = tokenize(text)
    if not tokens:
        return 0
    vector = [0] * 64
    for token in tokens:
        h = _hash64(token)
        for i in range(64):
            vector[i] += 1 if (h >> i) & 1 else -1
    fingerprint = 0
    for i in range(64):
        if vector[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def hamming64(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def is_near_duplicate(a: int, b: int, max_distance: int = 3) -> bool:
    return hamming64(a, b) <= max_distance


def detect_language(text: str) -> str | None:
    """Detecta idioma con langdetect; None si no esta disponible o falla."""
    text = normalize_text(text)
    if len(text) < 10:
        return None
    try:
        from langdetect import detect  # type: ignore

        return detect(text)
    except Exception:
        return None


def is_spanish(text: str, min_ratio: float = 0.06) -> bool:
    """Heuristica de espanol: usa langdetect si existe, si no, stopwords."""
    text = normalize_text(text)
    if len(text) < 10:
        return False

    lang = detect_language(text)
    if lang is not None:
        return lang == "es"

    tokens = tokenize(text)
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t in _SPANISH_STOPWORDS)
    if hits / len(tokens) >= min_ratio:
        return True
    joined = strip_accents(text).lower()
    return any(marker in joined for marker in _DOMINICAN_MARKERS)


def sha256_hash(value: str, salt: str = "") -> str:
    """Hash estable para anonimizar autores (nunca se guarda PII en claro)."""
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()


def keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    """Devuelve las keywords presentes (matching por token normalizado)."""
    normalized = strip_accents(normalize_text(text)).lower()
    hits: list[str] = []
    for kw in keywords:
        key = normalize_key(kw)
        if key and key in normalized:
            hits.append(kw)
    return hits
