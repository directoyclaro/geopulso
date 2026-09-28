"""Generador de informes HTML por zona (sin dependencias de plantillas)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from ..config import Config
from ..storage.duckdb_store import DuckDBStore

logger = logging.getLogger("geopulse.reporter")

_CSS = """
body { font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #1b1b1b; }
h1 { color: #0b3d91; margin-bottom: 0; }
h2 { color: #0b3d91; border-bottom: 2px solid #eee; padding-bottom: 4px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 20px; }
th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; font-size: 13px; }
th { background: #f5f7fa; }
.kpi { display: inline-block; margin-right: 24px; }
.kpi b { font-size: 22px; display: block; }
.burst { background: #fff3cd; }
.small { color: #666; font-size: 12px; }
"""


def _table(rows: list[tuple], headers: list[str], highlight: bool = False) -> str:
    if not rows:
        return "<p class='small'>Sin datos.</p>"
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = ""
    for row in rows:
        cls = " class='burst'" if highlight else ""
        body += f"<tr{cls}>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


class ReporterAgent:
    def __init__(self, config: Config, store: DuckDBStore) -> None:
        self.config = config
        self.store = store

    def generate(self, filename: str | None = None) -> Path:
        posts = self.store.query("SELECT COUNT(*) FROM posts")[0][0]
        enriched = self.store.query("SELECT COUNT(*) FROM posts_enriched")[0][0]
        zonas = self.store.query("SELECT COUNT(DISTINCT zona) FROM preferences")[0][0]
        bursts = self.store.query("SELECT COUNT(*) FROM trends WHERE es_burst")[0][0]

        top_temas = self.store.query(
            """
            SELECT topic, COUNT(*) FROM (SELECT unnest(topics) AS topic FROM posts_enriched)
            GROUP BY 1 ORDER BY 2 DESC LIMIT 15
            """
        )
        sentimiento = self.store.query(
            "SELECT sentiment, COUNT(*) FROM posts_enriched GROUP BY 1 ORDER BY 2 DESC"
        )
        alertas = self.store.query(
            """
            SELECT zona, tema, volumen, esperado, zscore, ratio FROM trends
            WHERE es_burst ORDER BY zscore DESC, volumen DESC LIMIT 25
            """
        )
        preferencias = self.store.query(
            "SELECT zona, categoria, peso FROM preferences ORDER BY zona, peso DESC LIMIT 60"
        )

        now = datetime.now()
        html = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>GeoPulse - Informe Bahoruco {now:%Y-%m-%d}</title>
<style>{_CSS}</style></head><body>
<h1>GeoPulse - Informe de tendencias</h1>
<p class="small">Provincia Bahoruco (DO-03) &middot; generado {now:%Y-%m-%d %H:%M}</p>
<div>
  <span class="kpi"><b>{posts}</b>posts</span>
  <span class="kpi"><b>{enriched}</b>enriquecidos</span>
  <span class="kpi"><b>{zonas}</b>zonas</span>
  <span class="kpi"><b>{bursts}</b>alertas (bursts)</span>
</div>

<h2>Alertas de tendencia (bursts)</h2>
{_table(alertas, ["Zona", "Tema", "Volumen", "Esperado", "z-score", "ratio"], highlight=True)}

<h2>Temas mas mencionados</h2>
{_table(top_temas, ["Tema", "Menciones"])}

<h2>Sentimiento</h2>
{_table(sentimiento, ["Sentimiento", "Posts"])}

<h2>Gustos/preferencias por zona</h2>
{_table(preferencias, ["Zona", "Categoria", "Peso"])}

<p class="small">Generado automaticamente por GeoPulse. Datos publicos agregados; autores anonimizados.</p>
</body></html>"""

        out_dir = self.config.settings.resolve(self.config.settings.paths.reports_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / (filename or f"reporte_bahoruco_{now:%Y%m%d_%H%M}.html")
        out_path.write_text(html, encoding="utf-8")
        logger.info("Informe generado: %s", out_path)
        return out_path
