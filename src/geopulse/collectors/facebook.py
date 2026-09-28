"""Colector de Facebook (paginas publicas) via Playwright.

Autenticacion: usa cookies/storage_state de una cuenta quemable; si no existen
y hay usuario/clave, inicia sesion automaticamente y guarda la sesion.
Incluye reintentos, proxy, scroll para paginacion y fallback a mbasic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..utils.text import normalize_text
from .base import BaseCollector, logger

_POSTS_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const selectors = [
    'div[role="article"]',
    'div[data-ad-preview="message"]',
    'div[data-ad-comet-preview="message"]'
  ];
  const push = (node) => {
    let text = (node.innerText || '').trim();
    if (text.length < 20) return;
    const link = node.querySelector('a[href*="/posts/"], a[href*="story_fbid"], a[href*="/videos/"], a[href*="permalink"]');
    const url = link ? link.href : null;
    const key = url || text.slice(0, 80);
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ text, url, external_id: url });
  };
  selectors.forEach((sel) => document.querySelectorAll(sel).forEach(push));
  return out;
}
"""


def _proxy_config(proxy: str | None) -> dict[str, str] | None:
    """Convierte una URL de proxy en el dict que espera Playwright."""
    if not proxy:
        return None
    parsed = urlparse(proxy)
    if not parsed.hostname:
        return {"server": proxy}
    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    config: dict[str, str] = {"server": server}
    if parsed.username:
        config["username"] = parsed.username
    if parsed.password:
        config["password"] = parsed.password
    return config


def _fill_first(page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=8000)
            page.fill(sel, value)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _click_first(page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            page.click(sel, timeout=5000)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def login_facebook(config, account: dict[str, Any], session_path, headless: bool | None = None):
    """Inicia sesion en Facebook con un navegador y guarda el storage_state."""
    from playwright.sync_api import sync_playwright

    headless = config.collection.headless if headless is None else headless
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="es-DO",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://www.facebook.com/login", timeout=config.collection.timeout_seconds * 1000)
        page.wait_for_timeout(3000)
        _click_first(
            page,
            [
                'button[data-cookiebanner="accept_button"]',
                'button[title="Allow all cookies"]',
                'button[title="Permitir todas las cookies"]',
            ],
        )
        if not _fill_first(page, ['input[name="email"]', "input#email"], account["username"]):
            raise RuntimeError("No se encontro el campo de correo en el login de Facebook.")
        if not _fill_first(page, ['input[name="pass"]', "input#pass"], account["password"]):
            raise RuntimeError("No se encontro el campo de contrasena en el login de Facebook.")
        clicked = _click_first(
            page,
            ['button[name="login"]', "#loginbutton", 'button[data-testid="royal_login_button"]', 'button[type="submit"]'],
        )
        if not clicked:
            page.press('input[name="pass"], input#pass', "Enter")
        page.wait_for_timeout(8000)
        if "login" in page.url or "checkpoint" in page.url:
            raise RuntimeError(f"Facebook no completo el login (url={page.url}); posible checkpoint/captcha o 2FA.")
        context.storage_state(path=str(session_path))
        browser.close()
    return session_path


def _dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for rec in records:
        key = rec.get("url") or normalize_text(rec.get("text", ""))[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


class FacebookCollector(BaseCollector):
    platform = "facebook"

    def _sources(self) -> list[dict[str, Any]]:
        if self.store:
            return self.store.get_sources(platform="facebook", status="activa")
        return [
            s
            for s in self.config.sources_seed.get("sources", [])
            if s.get("platform") == "facebook"
        ]

    def _read_session(self, account: dict[str, Any] | None):
        """Devuelve (storage_state | None, cookies | None) desde el archivo de sesion."""
        if not account:
            return None, None
        path: Path = self.accounts.session_path(self.platform, account)
        if not path.exists():
            return None, None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo leer sesion FB %s: %s", path, exc)
            return None, None
        if isinstance(data, dict) and "cookies" in data:
            return data, None
        if isinstance(data, list):
            return None, data
        return None, None

    def _fill_first(self, page, selectors: list[str], value: str) -> bool:
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=8000)
                page.fill(sel, value)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _click_first(self, page, selectors: list[str]) -> bool:
        for sel in selectors:
            try:
                page.click(sel, timeout=5000)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _login(self, page, account: dict[str, Any]) -> None:
        page.goto("https://www.facebook.com/login", timeout=self.config.collection.timeout_seconds * 1000)
        page.wait_for_timeout(3000)

        # Aceptar dialogo de cookies si aparece
        self._click_first(
            page,
            [
                'button[data-cookiebanner="accept_button"]',
                'button[title="Allow all cookies"]',
                'button[title="Permitir todas las cookies"]',
            ],
        )

        if not self._fill_first(page, ['input[name="email"]', "input#email"], account["username"]):
            raise RuntimeError("No se encontro el campo de correo en la pagina de login de Facebook.")
        if not self._fill_first(page, ['input[name="pass"]', "input#pass"], account["password"]):
            raise RuntimeError("No se encontro el campo de contrasena en la pagina de login de Facebook.")

        clicked = self._click_first(
            page,
            [
                'button[name="login"]',
                "#loginbutton",
                'button[data-testid="royal_login_button"]',
                'button[type="submit"]',
            ],
        )
        if not clicked:
            page.press('input[name="pass"], input#pass', "Enter")

        page.wait_for_timeout(8000)
        if "login" in page.url or "checkpoint" in page.url:
            raise RuntimeError(f"Facebook no completo el login (url={page.url}); posible checkpoint/captcha o 2FA.")

    def _scrape_page(self, page, url: str, scrolls: int) -> list[dict[str, Any]]:
        page.goto(url, timeout=self.config.collection.timeout_seconds * 1000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        collected: list[dict[str, Any]] = []
        for _ in range(scrolls):
            collected.extend(page.evaluate(_POSTS_JS))
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)
        return collected

    def collect(self) -> list[dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Playwright no esta instalado. Ejecuta: pip install playwright && playwright install chromium"
            ) from exc

        sources = self._sources()
        if not sources:
            logger.warning("Sin fuentes de Facebook activas.")
            return []

        account = self.accounts.current(self.platform)
        storage_state, cookies = self._read_session(account)
        proxy = self.accounts.proxy_for(account)
        session_path = self.accounts.session_path(self.platform, account) if account else None
        records: list[dict[str, Any]] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.config.collection.headless)
            context = browser.new_context(
                locale="es-DO",
                proxy=_proxy_config(proxy),
                storage_state=storage_state,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            )
            if cookies:
                context.add_cookies(cookies)
            page = context.new_page()

            if not storage_state and not cookies:
                if account and account.get("password"):
                    try:
                        self.retry(lambda: self._login(page, account), "fb-login")
                        if session_path:
                            context.storage_state(path=str(session_path))
                            logger.info("Sesion de Facebook guardada en %s", session_path)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Login de Facebook fallo: %s", exc)
                else:
                    logger.warning("Sin sesion de Facebook: cobertura reducida o bloqueo probable.")

            for src in sources:
                url = src.get("url")
                if not url:
                    continue
                try:
                    posts = self.retry(lambda u=url: self._scrape_page(page, u, scrolls=3), f"fb:{src.get('name')}")
                    if not posts and src.get("handle"):
                        basic = f"https://mbasic.facebook.com/{src['handle']}"
                        posts = self.retry(
                            lambda b=basic: self._scrape_page(page, b, scrolls=2), f"fb-mbasic:{src.get('name')}"
                        )
                    posts = posts[: self.max_posts_per_source]
                    for p in posts:
                        records.append(
                            {
                                "platform": self.platform,
                                "source_id": src.get("source_id"),
                                "external_id": p.get("external_id"),
                                "url": p.get("url"),
                                "text": p.get("text"),
                                "author": src.get("name"),
                                "location": src.get("municipio"),
                                "collected_at": None,
                            }
                        )
                    logger.info("Facebook %s -> %d posts", src.get("name"), len(posts))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error scrapeando %s: %s", url, exc)
                self.limiter.wait()

            context.close()
            browser.close()
        return _dedupe(records)
