"""Colector de Instagram via instagrapi (cuenta quemable).

Recolecta por hashtags, locations y perfiles semilla. Aplica rotacion de cuenta
y pausas configurables para reducir bloqueos.
"""

from __future__ import annotations

import json
from typing import Any

from .base import BaseCollector, logger


def _media_to_record(media: Any, source_id: str | None, platform: str = "instagram") -> dict[str, Any]:
    code = getattr(media, "code", None)
    user = getattr(media, "user", None)
    location = getattr(media, "location", None)
    return {
        "platform": platform,
        "source_id": source_id,
        "external_id": code,
        "url": f"https://www.instagram.com/p/{code}/" if code else None,
        "text": getattr(media, "caption_text", None),
        "author": getattr(user, "username", None),
        "location": getattr(location, "name", None) if location else None,
        "posted_at": getattr(media, "taken_at", None),
        "hashtags": None,
        "collected_at": None,
    }


def _sessionid_cache_path(session_path) -> "Path":
    from pathlib import Path

    return Path(session_path).with_suffix(".sessionid")


def _browser_state_path(session_path) -> "Path":
    """Archivo separado para el storage_state de Playwright (no es formato instagrapi)."""
    from pathlib import Path

    return Path(session_path).with_suffix(".browser.json")


def _is_playwright_state(path) -> bool:
    """Detecta si un archivo es un storage_state de Playwright (no instagrapi)."""
    import json as _json

    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return isinstance(data, dict) and "cookies" in data and "origins" in data


def _warm_up(client) -> None:
    """Acciones de calentamiento para habilitar endpoints (hashtags) tras login."""
    for fn in ("get_timeline_feed", "account_info"):
        try:
            getattr(client, fn)()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Warm-up IG %s fallo: %s", fn, exc)


def _read_cached_sessionid(session_path) -> str | None:
    try:
        path = _sessionid_cache_path(session_path)
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    except Exception:  # noqa: BLE001
        pass
    return None


def _write_cached_sessionid(session_path, sessionid: str) -> None:
    try:
        _sessionid_cache_path(session_path).write_text(sessionid, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo cachear sessionid IG: %s", exc)


def ig_login_via_browser(config, account: dict[str, Any], storage_state_path) -> str | None:
    """Login de Instagram por navegador para obtener la cookie `sessionid`.

    Evita el bloqueo 'version out of date' del endpoint de login de la API.
    Devuelve el sessionid y guarda el storage_state para reutilizarlo.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Playwright no esta instalado.") from exc

    sessionid: str | None = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.collection.headless)
        context = browser.new_context(
            locale="es-DO",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://www.instagram.com/accounts/login/", timeout=config.collection.timeout_seconds * 1000)
        page.wait_for_timeout(3500)

        for sel in [
            'button[data-cookiebanner="accept_button"]',
            'button:has-text("Permitir todas las cookies")',
            'button:has-text("Allow all cookies")',
            'button:has-text("Aceptar")',
        ]:
            try:
                page.click(sel, timeout=2500)
                break
            except Exception:  # noqa: BLE001
                continue

        email_filled = False
        for sel in ['input[name="email"]', 'input[name="username"]']:
            try:
                page.fill(sel, account["username"], timeout=8000)
                email_filled = True
                break
            except Exception:  # noqa: BLE001
                continue
        if not email_filled:
            raise RuntimeError("No se encontro el campo de usuario/correo en el login de Instagram.")

        pass_sel = None
        for sel in ['input[name="pass"]', 'input[name="password"]']:
            try:
                page.fill(sel, account["password"], timeout=8000)
                pass_sel = sel
                break
            except Exception:  # noqa: BLE001
                continue
        if not pass_sel:
            raise RuntimeError("No se encontro el campo de contrasena en el login de Instagram.")

        page.press(pass_sel, "Enter")
        page.wait_for_timeout(9000)

        # Dialogos posteriores al login ("Guardar informacion" / notificaciones)
        for sel in [
            'button:has-text("Not Now")',
            'button:has-text("Ahora no")',
            'button:has-text("Not now")',
        ]:
            try:
                page.click(sel, timeout=3000)
            except Exception:  # noqa: BLE001
                continue

        cookies = context.cookies()
        for c in cookies:
            if c.get("name") == "sessionid":
                sessionid = c.get("value")
                break

        try:
            context.storage_state(path=str(storage_state_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo guardar storage_state de IG: %s", exc)
        browser.close()
    return sessionid


def ig_manual_login(config, account: dict[str, Any], session_path, timeout: int = 300) -> str | None:
    """Abre un navegador visible para que el usuario complete el login (incluido
    el codigo de verificacion por email) y captura la cookie `sessionid`.
    """
    import time

    from playwright.sync_api import sync_playwright

    sessionid: str | None = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(locale="es-DO")
        page = context.new_page()
        page.goto("https://www.instagram.com/accounts/login/", timeout=config.collection.timeout_seconds * 1000)
        page.wait_for_timeout(3000)

        # Prellenar credenciales para acelerar (el usuario solo pone el codigo)
        try:
            for sel in ['input[name="email"]', 'input[name="username"]']:
                if page.query_selector(sel):
                    page.fill(sel, account["username"])
                    break
            for sel in ['input[name="pass"]', 'input[name="password"]']:
                if page.query_selector(sel):
                    page.fill(sel, account["password"])
                    break
        except Exception:  # noqa: BLE001
            pass

        logger.info(
            "Ventana abierta (datos prellenados): pulsa 'Iniciar sesion', completa el codigo del email "
            "y espera a entrar. Escuchando hasta %ds...", timeout
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            for c in context.cookies():
                if c.get("name") == "sessionid":
                    sessionid = c.get("value")
                    break
            if sessionid:
                break
            time.sleep(2)

        if sessionid:
            _write_cached_sessionid(session_path, sessionid)
            try:
                context.storage_state(path=str(_browser_state_path(session_path)))
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudo guardar storage_state: %s", exc)
            logger.info("sessionid de Instagram capturado y guardado.")
        else:
            logger.warning("No se capturo sessionid (login no completado dentro del tiempo).")
        browser.close()
    return sessionid


def build_ig_client(config, account: dict[str, Any], proxy: str | None, session_path):
    """Crea y autentica un cliente de Instagram.

    Orden: `sessionid` de accounts.yml -> sessionid cacheado -> login por
    navegador (Playwright) para obtener el sessionid -> usuario/clave API.
    """
    from instagrapi import Client

    client = Client()
    if proxy:
        client.set_proxy(proxy)
    if session_path.exists() and not _is_playwright_state(session_path):
        try:
            client.load_settings(session_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo cargar sesion IG: %s", exc)

    sessionid = account.get("sessionid") or _read_cached_sessionid(session_path)
    if not sessionid and account.get("password"):
        try:
            sessionid = ig_login_via_browser(config, account, session_path)
            if sessionid:
                _write_cached_sessionid(session_path, sessionid)
                logger.info("IG sessionid obtenido por navegador (%s)", account.get("username"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Login por navegador IG fallo: %s", exc)

    if sessionid:
        try:
            client.login_by_sessionid(sessionid)
            logger.info("IG autenticado por sessionid (%s)", account.get("username"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("login_by_sessionid fallo (%s); intentando usuario/clave", exc)
            client.login(account["username"], account["password"])
    else:
        client.login(account["username"], account["password"])
        logger.info("IG autenticado por usuario/clave (%s)", account.get("username"))

    _warm_up(client)

    try:
        if not _is_playwright_state(session_path):
            client.dump_settings(session_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo guardar sesion IG: %s", exc)
    return client


class InstagramCollector(BaseCollector):
    platform = "instagram"

    def __init__(self, *args: Any, amount: int = 30, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.amount = amount
        self._requests = 0

    def _client(self):
        account = self.accounts.current(self.platform)
        if not account:
            raise RuntimeError(
                "No hay cuentas de Instagram. Crea config/secrets/accounts.yml con al menos una cuenta quemable."
            )
        proxy = self.accounts.proxy_for(account)
        session_path = self.accounts.session_path(self.platform, account)
        try:
            return build_ig_client(self.config, account, proxy, session_path)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "No se pudo autenticar en Instagram. Si el login por clave falla con "
                "'version out of date', agrega 'sessionid' a la cuenta en accounts.yml "
                f"(cookie sessionid del navegador). Detalle: {exc}"
            ) from exc

    def _resolved_locations(self, client) -> list[dict[str, Any]]:
        """Resuelve locations configuradas por nombre o pk."""
        resolved: list[dict[str, Any]] = []
        for entry in self.config.instagram_locations:
            if isinstance(entry, dict) and entry.get("pk"):
                resolved.append(entry)
                continue
            name = entry if isinstance(entry, str) else (entry or {}).get("name")
            if not name:
                continue
            try:
                results = self.retry(lambda: client.fbsearch_places(name), f"location_search:{name}")
                if results:
                    first = results[0]
                    resolved.append({"pk": first.pk, "name": getattr(first, "name", name)})
                    logger.info("Location IG resuelta: %s (%s)", name, first.pk)
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudo resolver location %s: %s", name, exc)
        return resolved

    def _maybe_rotate(self, client):
        self._requests += 1
        if self._requests % self.config.collection.rotate_account_every_requests == 0:
            logger.info("Rotacion programada de cuenta IG")
            return self._client()
        return client

    def collect(self) -> list[dict[str, Any]]:
        client = self._client()
        records: list[dict[str, Any]] = []
        amount = min(self.amount, self.max_posts_per_source)

        # 1) Hashtags
        for tag in self.config.hashtags:
            try:
                medias = self.retry(
                    lambda t=tag: client.hashtag_medias_recent(t, amount=amount),
                    f"hashtag:{tag}",
                )
                records.extend(_media_to_record(m, None) for m in medias)
                logger.info("IG #%s -> %d", tag, len(medias))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error IG hashtag %s: %s", tag, exc)
            self.limiter.wait()
            client = self._maybe_rotate(client)

        # 2) Perfiles semilla de Instagram
        if self.store:
            seeds = [s for s in self.store.get_sources(platform="instagram", status="activa") if s.get("handle")]
        else:
            seeds = [s for s in self.config.sources_seed.get("sources", []) if s.get("platform") == "instagram"]

        for src in seeds:
            handle = src.get("handle")
            if not handle:
                continue
            try:
                user_id = self.retry(lambda h=handle: client.user_id_from_username(h), f"user_id:{handle}")
                medias = self.retry(
                    lambda uid=user_id: client.user_medias(uid, amount=amount),
                    f"user_medias:{handle}",
                )
                records.extend(_media_to_record(m, src.get("source_id")) for m in medias)
                logger.info("IG @%s -> %d", handle, len(medias))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error IG perfil %s: %s", handle, exc)
            self.limiter.wait()
            client = self._maybe_rotate(client)

        # 3) Locations (pk directo o resuelto por nombre)
        for loc in self._resolved_locations(client):
            pk = loc.get("pk")
            if not pk:
                continue
            try:
                medias = self.retry(
                    lambda p=pk: client.location_medias_recent(p, amount=amount),
                    f"location:{pk}",
                )
                records.extend(_media_to_record(m, None) for m in medias)
                logger.info("IG location %s -> %d", pk, len(medias))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error IG location %s: %s", pk, exc)
            self.limiter.wait()
            client = self._maybe_rotate(client)

        return records
