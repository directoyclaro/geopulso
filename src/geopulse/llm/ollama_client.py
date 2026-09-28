"""Cliente minimo para Ollama (/api/chat) con salida JSON forzada."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("geopulse.llm")


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:3b-instruct",
        timeout: int = 120,
        keep_alive: str = "5m",
        temperature: float = 0.1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.temperature = temperature

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def list_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                return [m.get("name", "") for m in data.get("models", [])]
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo listar modelos de Ollama: %s", exc)
            return []

    def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any] | None:
        """Envia un chat y parsea la respuesta como JSON. None si falla."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature},
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                content = resp.json().get("message", {}).get("content", "")
                return json.loads(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallo consultando Ollama: %s", exc)
            return None
