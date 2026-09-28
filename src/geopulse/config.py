"""Carga centralizada de configuracion (YAML) y rutas del proyecto."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / "config"
GEO_DIR = Path(__file__).resolve().parent / "geo"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


class ProjectConfig(BaseModel):
    name: str = "GeoPulse"
    edition: str = "Bahoruco"
    country: str = "DO"
    province_code: str = "DO-03"
    language: str = "es"


class PathsConfig(BaseModel):
    data_dir: str = "data"
    bronze_dir: str = "data/bronze"
    silver_dir: str = "data/silver"
    gold_dir: str = "data/gold"
    db_path: str = "data/geopulse.duckdb"
    queue_path: str = "data/jobs.sqlite"
    reports_dir: str = "reports/output"
    secrets_dir: str = "config/secrets"
    logs_dir: str = "logs"


class CollectionConfig(BaseModel):
    platforms: list[str] = Field(default_factory=lambda: ["instagram", "facebook"])
    instagram_backend: str = "web"
    max_concurrent_browsers: int = 1
    request_delay_seconds: tuple[int, int] = (30, 90)
    max_requests_per_hour: int = 120
    rotate_account_every_requests: int = 200
    max_posts_per_source: int = 30
    max_retries: int = 3
    backoff_seconds: tuple[int, int] = (5, 60)
    proxies: list[str] = Field(default_factory=list)
    headless: bool = True
    timeout_seconds: int = 45


class NormalizerConfig(BaseModel):
    dedup_simhash_distance: int = 3
    keep_only_spanish: bool = True
    min_text_length: int = 15


class GeoConfig(BaseModel):
    min_confidence_municipio: float = 0.60
    min_confidence_distrito: float = 0.75
    allow_country_fallback: bool = True


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:3b-instruct"
    batch_size: int = 8
    timeout_seconds: int = 120
    keep_alive: str = "5m"
    temperature: float = 0.1
    enabled: bool = True
    fallback_heuristic: bool = True


class TrendConfig(BaseModel):
    lookback_days: int = 30
    min_volume: int = 3
    z_threshold: float = 2.0
    ratio_threshold: float = 2.0


class SchedulerConfig(BaseModel):
    collect_instagram_cron: str | None = "0 */6 * * *"
    collect_facebook_cron: str | None = "15 */6 * * *"
    discover_cron: str | None = "30 3 * * *"
    enrich_cron: str | None = None
    trends_cron: str | None = "30 4 * * *"
    report_cron: str | None = "45 4 * * *"


class Settings(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    normalizer: NormalizerConfig = Field(default_factory=NormalizerConfig)
    geo: GeoConfig = Field(default_factory=GeoConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    trends: TrendConfig = Field(default_factory=TrendConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)

    def resolve(self, relative: str) -> Path:
        """Convierte una ruta relativa del YAML en absoluta desde la raiz."""
        p = Path(relative)
        return p if p.is_absolute() else (ROOT_DIR / p)


class Config:
    """Contenedor unico de toda la configuracion del proyecto."""

    def __init__(self) -> None:
        raw = load_yaml(CONFIG_DIR / "settings.yml")
        self.settings = Settings(**raw)
        self.zones: dict[str, Any] = load_yaml(CONFIG_DIR / "zones.yml")
        self.taxonomy: dict[str, Any] = load_yaml(CONFIG_DIR / "taxonomy.yaml")
        self.sources_seed: dict[str, Any] = load_yaml(CONFIG_DIR / "sources_seed.yml")
        self.aliases: dict[str, Any] = load_yaml(GEO_DIR / "aliases.yml")
        self.gazetteer: dict[str, Any] = load_json(GEO_DIR / "gazetteer_do03.json")

    # --- Accesos directos a subconfiguraciones ---
    @property
    def collection(self) -> CollectionConfig:
        return self.settings.collection

    @property
    def normalizer(self) -> NormalizerConfig:
        return self.settings.normalizer

    @property
    def geo(self) -> GeoConfig:
        return self.settings.geo

    @property
    def llm(self) -> LLMConfig:
        return self.settings.llm

    @property
    def trends(self) -> TrendConfig:
        return self.settings.trends

    @property
    def scheduler(self) -> SchedulerConfig:
        return self.settings.scheduler

    # --- Helpers de rutas ---
    @property
    def db_path(self) -> Path:
        return self.settings.resolve(self.settings.paths.db_path)

    @property
    def queue_path(self) -> Path:
        return self.settings.resolve(self.settings.paths.queue_path)

    def data_path(self, sub: str) -> Path:
        return self.settings.resolve(f"{self.settings.paths.data_dir}/{sub}")

    @property
    def keywords(self) -> list[str]:
        return list(self.zones.get("keywords", []))

    @property
    def hashtags(self) -> list[str]:
        return list(self.zones.get("hashtags", []))

    @property
    def municipios(self) -> list[str]:
        return [m["name"] for m in self.zones.get("municipios", [])]

    @property
    def instagram_locations(self) -> list[dict[str, Any]]:
        """Locations de IG: config de zonas + cache resuelto por descubrimiento."""
        raw = list(self.zones.get("instagram_locations", []))
        cache = self.settings.resolve(f"{self.settings.paths.gold_dir}/instagram_locations.json")
        if cache.exists():
            try:
                extra = json.loads(cache.read_text(encoding="utf-8"))
                if isinstance(extra, list):
                    raw.extend(extra)
            except Exception:  # noqa: BLE001
                pass
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if isinstance(item, dict) and item.get("pk"):
                if str(item["pk"]) in seen:
                    continue
                seen.add(str(item["pk"]))
                out.append({"pk": item["pk"], "name": item.get("name", "")})
            elif isinstance(item, str):
                out.append({"name": item})
        return out

    def ensure_dirs(self) -> None:
        for rel in (
            self.settings.paths.data_dir,
            self.settings.paths.bronze_dir,
            self.settings.paths.silver_dir,
            self.settings.paths.gold_dir,
            self.settings.paths.reports_dir,
            self.settings.paths.secrets_dir,
            self.settings.paths.logs_dir,
        ):
            self.settings.resolve(rel).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
