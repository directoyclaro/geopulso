"""Descubrimiento de perfiles/paginas publicas ligados a municipios y distritos.

Busca en Instagram (topsearch + bio) y Facebook (busqueda de paginas) cuentas
que mencionan o se ubican en cualquier municipio/distrito de Bahoruco.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..utils.text import normalize_key
from .base import AccountManager

logger = logging.getLogger("geopulse.profile_discovery")

# Terminos genericos que existen en otros paises: exigir contexto dominicano.
_AMBIGUOUS = {
    "el palmar",
    "el salado",
    "las canitas",
    "santa barbara",
    "mena",
    "santana",
    "los rios",
    "las clavellinas",
    "uvilla",
    "tamayo",
    "galvan",
    "las marias",
    "jaragua",
}

# Senales de que la cuenta es de Republica Dominicana / Bahoruco.
_CONTEXT_SIGNALS = [
    "bahoruco",
    "baoruco",
    "republica dominicana",
    "rep dominicana",
    "dominicana",
    "dominicano",
    "809",
    "829",
    "849",
]


def _mentions(haystack_norm: str, term: str) -> bool:
    key = normalize_key(term)
    if not key:
        return False
    return re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", haystack_norm) is not None


def place_terms(config) -> list[dict[str, Any]]:
    """Lista de lugares a buscar: municipios, alias, distritos y landmarks."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(term: str, municipio: str | None, level: str, distrito: str | None = None) -> None:
        term = (term or "").strip()
        key = term.lower()
        if not term or key in seen:
            return
        seen.add(key)
        out.append({"term": term, "municipio": municipio, "distrito": distrito, "level": level})

    for mun in config.zones.get("municipios", []):
        add(mun["name"], mun["name"], "municipio")
        if mun.get("alt_name"):
            add(mun["alt_name"], mun["name"], "municipio")
        for dist in mun.get("distritos", []):
            add(dist, mun["name"], "distrito", dist)

    for dist in config.zones.get("distritos_extra", []):
        add(dist, None, "distrito", dist)

    for landmark in config.gazetteer.get("landmarks", []):
        add(landmark["name"], None, "landmark")

    return out


def _existing_handles(store, platform: str) -> set[str]:
    try:
        return {(s.get("handle") or "").lower() for s in store.get_sources(platform=platform)}
    except Exception:  # noqa: BLE001
        return set()


def discover_instagram_places(config, store, per_place: int = 8) -> dict[str, int]:
    from playwright.sync_api import sync_playwright

    from .instagram import _read_cached_sessionid
    from .instagram_web import build_context, profile_bio, topsearch

    mgr = AccountManager(config)
    account = mgr.current("instagram")
    if not account:
        return {"terms": 0, "found": 0, "added": 0}
    session_path = mgr.session_path("instagram", account)
    sessionid = account.get("sessionid") or _read_cached_sessionid(session_path)
    existing = _existing_handles(store, "instagram")

    def has_dominican_context(hay: str) -> bool:
        # Solo senales fuertes: los nombres de municipios son homonimos en otros paises.
        return any(_mentions(hay, s) for s in _CONTEXT_SIGNALS)

    terms = place_terms(config)
    found = 0
    added = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.collection.headless)
        context = build_context(browser, config, account, session_path, sessionid)
        page = context.new_page()
        for place in terms:
            users = topsearch(context, place["term"], per_place)
            found += len(users)
            for user in users:
                handle = user["username"]
                if handle.lower() in existing:
                    continue
                info = profile_bio(page, handle)
                bio = info.get("bio") or ""
                name = user.get("full_name") or info.get("full_name") or handle
                haystack = normalize_key(f"{name} {bio}")
                if not _mentions(haystack, place["term"]):
                    continue  # no menciona el lugar: descartar
                ambiguous = normalize_key(place["term"]) in _AMBIGUOUS or place["level"] == "distrito"
                context_ok = has_dominican_context(haystack)
                if ambiguous and not context_ok:
                    continue  # homonimo en otro pais / nombre de persona: descartar
                status = "activa" if context_ok else "dudosa"
                score = 0.8 if context_ok else 0.45
                store.upsert_source(
                    {
                        "name": name,
                        "platform": "instagram",
                        "handle": handle,
                        "url": f"https://www.instagram.com/{handle}/",
                        "type": "descubierta",
                        "municipio": place["municipio"],
                        "verified": bool(user.get("verified")),
                        "status": status,
                        "score": score,
                    }
                )
                existing.add(handle.lower())
                added += 1
            logger.info("IG places '%s' -> %d perfiles", place["term"], len(users))
        context.close()
        browser.close()
    logger.info("Descubrimiento IG por lugares: terms=%d found=%d added=%d", len(terms), found, added)
    return {"terms": len(terms), "found": found, "added": added}


_FB_SEARCH_JS = r"""
() => {
  const out = []; const seen = new Set();
  const skip = new Set(['login','help','policies','privacy','search','groups','watch',
    'marketplace','gaming','reel','videos','pages','about','terms','home.php','settings',
    'ads','business','developers','careers','legal','photo','story.php']);
  document.querySelectorAll('a[href]').forEach(a => {
    const h = a.getAttribute('href') || '';
    const m = h.match(/^https?:\/\/www\.facebook\.com\/([A-Za-z0-9._-]{3,})\/?(?:\?|$)/);
    if (!m) return;
    const slug = m[1];
    if (skip.has(slug) || seen.has(slug)) return;
    const text = (a.innerText || '').trim();
    if (!text || text.length > 70) return;
    seen.add(slug);
    out.push({ name: text, url: 'https://www.facebook.com/' + slug + '/', handle: slug });
  });
  return out;
}
"""


def _read_fb_storage(session_path: Path):
    if not session_path or not session_path.exists():
        return None, None
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None, None
    if isinstance(data, dict) and "cookies" in data:
        return data, None
    if isinstance(data, list):
        return None, data
    return None, None


def discover_facebook_places(config, store, per_place: int = 8) -> dict[str, int]:
    from playwright.sync_api import sync_playwright

    mgr = AccountManager(config)
    account = mgr.current("facebook")
    session_path = mgr.session_path("facebook", account) if account else None
    storage_state, cookies = _read_fb_storage(session_path) if session_path else (None, None)
    if not storage_state and not cookies and account and account.get("password"):
        try:
            from .facebook import login_facebook

            logger.info("Sin sesion de Facebook; iniciando sesion...")
            login_facebook(config, account, session_path)
            storage_state, cookies = _read_fb_storage(session_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Login de Facebook fallo: %s", exc)
    existing = _existing_handles(store, "facebook")
    terms = place_terms(config)
    found = 0
    added = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.collection.headless)
        context = browser.new_context(
            locale="es-DO",
            storage_state=storage_state,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()
        for place in terms:
            url = f"https://www.facebook.com/search/pages/?q={quote(place['term'])}"
            try:
                page.goto(url, timeout=config.collection.timeout_seconds * 1000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                candidates = page.evaluate(_FB_SEARCH_JS)[:per_place]
            except Exception as exc:  # noqa: BLE001
                logger.warning("FB search '%s' fallo: %s", place["term"], exc)
                candidates = []
            found += len(candidates)
            for cand in candidates:
                handle = cand["handle"]
                if handle.lower() in existing:
                    continue
                store.upsert_source(
                    {
                        "name": cand["name"],
                        "platform": "facebook",
                        "handle": handle,
                        "url": cand["url"],
                        "type": "descubierta",
                        "municipio": place["municipio"],
                        "verified": False,
                        "status": "dudosa",
                        "score": 0.45,
                    }
                )
                existing.add(handle.lower())
                added += 1
            logger.info("FB places '%s' -> %d paginas", place["term"], len(candidates))
        context.close()
        browser.close()
    logger.info("Descubrimiento FB por lugares: terms=%d found=%d added=%d", len(terms), found, added)
    return {"terms": len(terms), "found": found, "added": added}


def discover_places(
    config,
    store,
    platforms: tuple[str, ...] = ("instagram", "facebook"),
    per_place: int = 8,
    reset: bool = False,
) -> dict:
    result: dict[str, Any] = {}
    if reset:
        removed = store.delete_discovered_sources(list(platforms))
        result["removed"] = removed
        logger.info("Fuentes descubiertas eliminadas: %d", removed)
    if "instagram" in platforms:
        result["instagram"] = discover_instagram_places(config, store, per_place=per_place)
    if "facebook" in platforms:
        result["facebook"] = discover_facebook_places(config, store, per_place=per_place)
    return result
