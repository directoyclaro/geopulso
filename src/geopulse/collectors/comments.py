"""Recoleccion de comentarios en publicaciones publicas (Instagram y Facebook)."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from ..utils.text import clean_social_text

logger = logging.getLogger("geopulse.comments")

_IG_COMMENTS_JS = r"""
() => {
  const out = new Set();
  document.querySelectorAll('ul li span[dir="auto"], ul span[dir="auto"]').forEach(s => {
    const t = (s.innerText || '').trim();
    if (t.length > 1 && t.length < 600) out.add(t);
  });
  return Array.from(out);
}
"""

_FB_COMMENTS_JS = r"""
() => {
  const out = new Set();
  const nodes = document.querySelectorAll('div[role="article"] [dir="auto"], ul[role="list"] [dir="auto"]');
  nodes.forEach(el => {
    const t = (el.innerText || '').trim();
    if (t.length > 1 && t.length < 600) out.add(t);
  });
  return Array.from(out);
}
"""


_UI_STOP = {
    "inicio", "reels", "mensajes", "buscar", "notificaciones", "crear", "perfil", "más", "mas",
    "también de meta", "meta", "información", "blog", "empleo", "ayuda", "api", "privacidad",
    "condiciones", "ubicaciones", "popular", "instagram lite", "threads", "español", "más opciones",
    "inicia la conversación.", "ver más", "ver menos", "seguir", "responder", "me gusta",
}


def _is_ui(text: str) -> bool:
    return clean_social_text(text).lower().strip(" .") in {s.strip(" .") for s in _UI_STOP}


def _comment_id(platform: str, post: str, text: str) -> str:
    return hashlib.sha1(f"{platform}|{post}|{text}".encode("utf-8")).hexdigest()[:20]


def _author_hash(platform: str, author: str | None) -> str | None:
    if not author:
        return None
    return hashlib.sha256(f"geopulse:{platform}:{author}".encode("utf-8")).hexdigest()


def _parse_ig_code(target: str) -> str | None:
    match = re.search(r"instagram\.com/(?:p|reel)/([^/?#]+)", target)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{5,}", target):
        return target
    return None


def collect_instagram_comments(config, account, session_path, target: str, amount: int = 50) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    from .instagram import _read_cached_sessionid
    from .instagram_web import build_context

    code = _parse_ig_code(target)
    if not code:
        raise ValueError(f"No se pudo interpretar el post de Instagram: {target}")
    sessionid = account.get("sessionid") or _read_cached_sessionid(session_path)
    url = f"https://www.instagram.com/p/{code}/"
    texts: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.collection.headless)
        context = build_context(browser, config, account, session_path, sessionid)
        page = context.new_page()
        page.goto(url, timeout=config.collection.timeout_seconds * 1000, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        for _ in range(3):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(1500)
        try:
            texts = page.evaluate(_IG_COMMENTS_JS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudieron extraer comentarios IG: %s", exc)
        context.close()
        browser.close()

    records: list[dict[str, Any]] = []
    for text in texts[:amount]:
        cleaned = clean_social_text(text)
        if len(cleaned) < 3 or _is_ui(cleaned):
            continue
        records.append(
            {
                "comment_id": _comment_id("instagram", code, cleaned),
                "post_raw_id": None,
                "platform": "instagram",
                "text": cleaned,
                "language": "es",
                "author_hash": None,
                "url": url,
                "keywords": [],
            }
        )
    logger.info("Comentarios IG de %s: %d", code, len(records))
    return records


def collect_facebook_comments(config, account, session_path, target: str, amount: int = 50) -> list[dict[str, Any]]:
    import json
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    data = None
    if session_path and Path(session_path).exists():
        data = json.loads(Path(session_path).read_text(encoding="utf-8"))
    storage_state = data if isinstance(data, dict) else None
    cookies = data if isinstance(data, list) else None

    texts: list[str] = []
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
        page.goto(target, timeout=config.collection.timeout_seconds * 1000, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        for _ in range(3):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1500)
        try:
            texts = page.evaluate(_FB_COMMENTS_JS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudieron extraer comentarios FB: %s", exc)
        context.close()
        browser.close()

    records: list[dict[str, Any]] = []
    for text in texts[:amount]:
        cleaned = clean_social_text(text)
        if len(cleaned) < 3 or _is_ui(cleaned):
            continue
        records.append(
            {
                "comment_id": _comment_id("facebook", target, cleaned),
                "post_raw_id": None,
                "platform": "facebook",
                "text": cleaned,
                "language": "es",
                "author_hash": None,
                "url": target,
                "keywords": [],
            }
        )
    logger.info("Comentarios FB de %s: %d", target, len(records))
    return records


def collect_comments(config, store, target: str, amount: int = 50) -> list[dict[str, Any]]:
    """Recolecta comentarios de una publicacion publica segun su URL."""
    from .base import AccountManager

    manager = AccountManager(config)
    target_lower = target.lower()
    if "instagram.com" in target_lower or _parse_ig_code(target):
        account = manager.current("instagram")
        if not account:
            raise RuntimeError("Sin cuenta de Instagram en accounts.yml")
        session_path = manager.session_path("instagram", account)
        return collect_instagram_comments(config, account, session_path, target, amount)
    if "facebook.com" in target_lower:
        account = manager.current("facebook")
        session_path = manager.session_path("facebook", account) if account else None
        return collect_facebook_comments(config, account, session_path, target, amount)
    raise ValueError("URL no soportada (se espera instagram.com o facebook.com)")
