"""Colector de Instagram via web (Playwright) usando la sesion de navegador.

Soporta: hashtags, perfiles, busqueda por keyword (explore search) y busqueda
de perfiles publicos (topsearch). Alternativa robusta a la API movil.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote

from ..utils.text import clean_social_text, normalize_key
from .base import BaseCollector, logger
from .instagram import _browser_state_path, _read_cached_sessionid

logger = logging.getLogger("geopulse.instagram_web")

_CODES_JS = r"""
() => {
  const codes = new Set();
  document.querySelectorAll('a[href^="/p/"]').forEach(a => {
    const m = (a.getAttribute('href') || '').match(/^\/p\/([^\/?]+)/);
    if (m) codes.add(m[1]);
  });
  return Array.from(codes);
}
"""

_META_JS = r"""
() => {
  const g = (p) => { const el = document.querySelector(`meta[property="${p}"]`); return el ? el.getAttribute('content') : null; };
  return { desc: g('og:description'), title: g('og:title') };
}
"""

_TOPSEARCH_HEADERS = {
    "x-ig-app-id": "936619743392459",
    "x-requested-with": "XMLHttpRequest",
    "referer": "https://www.instagram.com/",
    "accept": "*/*",
}

_PREFIX_RE = re.compile(
    r"^[\d.,KkMm]+ (?:likes?|Me gusta)(?:, [\d.,KkMm]+ (?:comments?|comentarios?))? - ",
    re.IGNORECASE,
)
_AUTHOR_RE = re.compile(r"^([\w.]+)\s+(?:el|on)\s+")


def _parse_desc(desc: str | None) -> tuple[str | None, str]:
    if not desc:
        return None, ""
    text = _PREFIX_RE.sub("", desc.strip())
    author: str | None = None
    match = _AUTHOR_RE.match(text)
    if match:
        author = match.group(1)
        colon = text.find(": ", match.end())
        if colon != -1:
            text = text[colon + 2 :]
    text = text.strip().strip('"').strip("\u201c").strip("\u201d").strip("'")
    return author, clean_social_text(text)


def _parse_author(title: str | None) -> str | None:
    if not title:
        return None
    m = re.match(r"(.+?) (?:on |en )?Instagram", title)
    return m.group(1).strip() if m else None


def build_context(browser, config, account: dict[str, Any] | None, session_path=None, sessionid: str | None = None):
    """Crea un contexto de Playwright logueado (storage_state o cookie sessionid)."""
    kwargs = dict(
        locale="es-DO",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    )
    if session_path is not None:
        state_path = _browser_state_path(session_path)
        if state_path.exists():
            return browser.new_context(storage_state=str(state_path), **kwargs)

    context = browser.new_context(**kwargs)
    if sessionid:
        context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": sessionid,
                    "domain": ".instagram.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                }
            ]
        )
    return context


class InstagramWebCollector(BaseCollector):
    platform = "instagram"

    def __init__(
        self,
        *args: Any,
        amount: int = 30,
        queries: list[str] | None = None,
        profiles: list[str] | None = None,
        include_config: bool = True,
        keyword_filter: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.amount = min(amount, self.max_posts_per_source)
        self.queries = queries or []
        self.profiles = profiles or []
        self.include_config = include_config
        self.keyword_filter = [k.lower() for k in (keyword_filter or [])]

    def _sessionid(self, account: dict[str, Any] | None) -> str | None:
        if account and account.get("sessionid"):
            return account["sessionid"]
        if account:
            return _read_cached_sessionid(self.accounts.session_path(self.platform, account))
        return None

    def _new_context(self, browser, account, session_path):
        return build_context(browser, self.config, account, session_path, self._sessionid(account))

    def _matches_filter(self, record: dict[str, Any]) -> bool:
        if not self.keyword_filter:
            return True
        haystack = normalize_key(f"{record.get('text','')} {' '.join(record.get('hashtags') or [])}")
        return any(k in haystack for k in self.keyword_filter)

    def _build_targets(self) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        if self.include_config:
            for tag in self.config.hashtags:
                targets.append(
                    {"kind": "hashtag", "value": tag, "url": f"https://www.instagram.com/explore/tags/{tag}/"}
                )
            if self.store:
                for src in self.store.get_sources(platform="instagram", status="activa"):
                    if src.get("handle"):
                        targets.append(
                            {
                                "kind": "profile",
                                "value": src["handle"],
                                "url": f"https://www.instagram.com/{src['handle']}/",
                                "source_id": src.get("source_id"),
                                "municipio": src.get("municipio"),
                            }
                        )
        for query in self.queries:
            targets.append(
                {
                    "kind": "keyword",
                    "value": query,
                    "url": f"https://www.instagram.com/explore/search/keyword/?q={quote(query)}",
                }
            )
        for profile in self.profiles:
            targets.append(
                {"kind": "profile", "value": profile, "url": f"https://www.instagram.com/{profile}/"}
            )
        return targets

    def _collect_codes(self, page, url: str, scrolls: int = 3) -> list[str]:
        page.goto(url, timeout=self.config.collection.timeout_seconds * 1000, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        if page.url.rstrip("/") in {"https://www.instagram.com", "https://instagram.com"} or "/accounts/login" in page.url:
            raise RuntimeError(
                "Sesion de Instagram no valida (redirige a login). Ejecuta: geopulse manual-login instagram"
            )
        codes: list[str] = []
        for _ in range(scrolls):
            codes.extend(page.evaluate(_CODES_JS))
            page.mouse.wheel(0, 3500)
            page.wait_for_timeout(1500)
        # Respaldo: algunos perfiles no renderizan <a href="/p/..."> pero el HTML
        # si contiene los shortcodes (p.ej. en JSON embebido).
        if not codes:
            codes = re.findall(r"/p/([A-Za-z0-9_-]+)/", page.content())
        seen: set[str] = set()
        out: list[str] = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out[: self.amount]

    def _post_details(self, page, code: str) -> dict[str, Any]:
        page.goto(
            f"https://www.instagram.com/p/{code}/",
            timeout=self.config.collection.timeout_seconds * 1000,
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(1500)
        meta = page.evaluate(_META_JS)
        author, caption = _parse_desc(meta.get("desc"))
        return {"text": caption, "author": author or _parse_author(meta.get("title"))}

    def collect(self) -> list[dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Playwright no esta instalado.") from exc

        account = self.accounts.current(self.platform)
        if not account:
            raise RuntimeError("Sin cuenta de Instagram en config/secrets/accounts.yml")
        session_path = self.accounts.session_path(self.platform, account)
        has_state = _browser_state_path(session_path).exists()
        if not has_state and not self._sessionid(account):
            raise RuntimeError("Instagram sin sesion. Ejecuta: geopulse manual-login instagram")

        targets = self._build_targets()
        records: list[dict[str, Any]] = []
        seen_codes: set[str] = set()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.config.collection.headless)
            context = self._new_context(browser, account, session_path)
            page = context.new_page()

            for target in targets:
                try:
                    codes = self.retry(
                        lambda t=target: self._collect_codes(page, t["url"]), f"ig:{target['kind']}:{target['value']}"
                    )
                    logger.info("IG %s %s -> %d posts", target["kind"], target["value"], len(codes))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error IG %s: %s", target["value"], exc)
                    codes = []

                for code in codes:
                    if code in seen_codes:
                        continue
                    seen_codes.add(code)
                    try:
                        details = self.retry(lambda c=code: self._post_details(page, c), f"ig-post:{code}")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Detalle IG %s fallo: %s", code, exc)
                        details = {"text": "", "author": None}
                    record = {
                        "platform": self.platform,
                        "source_id": target.get("source_id"),
                        "external_id": code,
                        "url": f"https://www.instagram.com/p/{code}/",
                        "text": details.get("text"),
                        "author": details.get("author"),
                        "location": target.get("municipio"),
                        "hashtags": [target["value"]] if target["kind"] in {"hashtag", "keyword"} else None,
                        "collected_at": None,
                    }
                    if self._matches_filter(record):
                        records.append(record)
                    self.limiter.wait()

                self.limiter.wait()

            context.close()
            browser.close()
        return records


def topsearch(context, term: str, amount: int = 20) -> list[dict[str, Any]]:
    """Busca perfiles por termino usando un contexto de Playwright ya logueado."""
    url = f"https://www.instagram.com/web/search/topsearch/?context=blended&query={quote(term)}"
    try:
        resp = context.request.get(url, headers=_TOPSEARCH_HEADERS, timeout=20000)
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Busqueda de perfiles '%s' fallo: %s", term, exc)
        return []
    users: list[dict[str, Any]] = []
    for item in data.get("users", [])[:amount]:
        user = item.get("user", {})
        if user.get("username"):
            users.append(
                {
                    "username": user.get("username"),
                    "full_name": user.get("full_name"),
                    "pk": user.get("pk"),
                    "verified": user.get("is_verified"),
                }
            )
    return users


def profile_bio(page, handle: str) -> dict[str, Any]:
    """Extrae la biografia de un perfil publico desde el HTML embebido."""
    try:
        page.goto(f"https://www.instagram.com/{handle}/", timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        html = page.content()
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo leer el perfil %s: %s", handle, exc)
        return {"bio": "", "full_name": None}
    bio = ""
    match = re.search(r'"biography":"(.*?)","', html)
    if match:
        raw = match.group(1)
        try:
            bio = json.loads(f'"{raw}"')
        except Exception:  # noqa: BLE001
            bio = raw
    name_match = re.search(r'"full_name":"(.*?)","', html)
    full_name = None
    if name_match:
        try:
            full_name = json.loads(f'"{name_match.group(1)}"')
        except Exception:  # noqa: BLE001
            full_name = name_match.group(1)
    return {"bio": clean_social_text(bio), "full_name": full_name}


def search_public_profiles(
    config, account: dict[str, Any], session_path, term: str, amount: int = 20
) -> list[dict[str, Any]]:
    """Busca perfiles publicos de Instagram por termino (endpoint topsearch)."""
    from playwright.sync_api import sync_playwright

    sessionid = account.get("sessionid") or _read_cached_sessionid(session_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.collection.headless)
        context = build_context(browser, config, account, session_path, sessionid)
        users = topsearch(context, term, amount)
        context.close()
        browser.close()
    return users
