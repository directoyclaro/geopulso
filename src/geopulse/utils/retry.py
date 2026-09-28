"""Reintentos con backoff exponencial y jitter."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def retry_call(
    fn: Callable[[], T],
    *,
    retries: int = 3,
    backoff_range: tuple[int, int] = (5, 60),
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    logger: logging.Logger | None = None,
    description: str = "",
) -> T:
    """Ejecuta `fn` reintentando ante `exceptions` con backoff exponencial + jitter."""
    attempt = 0
    while True:
        try:
            return fn()
        except exceptions as exc:  # noqa: PERF203
            attempt += 1
            if attempt > retries:
                raise
            base = min(backoff_range[1], backoff_range[0] * (2 ** (attempt - 1)))
            delay = random.uniform(base * 0.5, base)
            if logger:
                logger.warning(
                    "Reintento %d/%d%s tras error: %s (esperando %.1fs)",
                    attempt,
                    retries,
                    f" [{description}]" if description else "",
                    exc,
                    delay,
                )
            time.sleep(delay)


def safe_call(fn: Callable[[], T], default: Any = None, logger: logging.Logger | None = None) -> Any:
    """Ejecuta `fn` y devuelve `default` si falla (para pasos no criticos)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.warning("Llamada fallida (se ignora): %s", exc)
        return default
