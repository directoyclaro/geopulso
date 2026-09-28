"""Detector de tendencias por zona, robusto a baja densidad de datos.

Calcula, para cada (zona, tema), el volumen de la ventana actual frente a una
linea base historica usando z-score (con aproximacion de Poisson cuando no hay
varianza) y ratio de crecimiento. Marca 'burst' cuando hay aceleracion.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date
from statistics import mean, pstdev
from typing import Any

from ..config import Config
from ..storage.duckdb_store import DuckDBStore

logger = logging.getLogger("geopulse.trends")


class TrendDetector:
    def __init__(self, config: Config, store: DuckDBStore) -> None:
        self.config = config
        self.store = store
        self.cfg = config.trends

    @staticmethod
    def _zscore(current: float, baseline: list[float]) -> float:
        if len(baseline) >= 2 and pstdev(baseline) > 0:
            return (current - mean(baseline)) / pstdev(baseline)
        # Sin varianza: aproximacion Poisson sqrt(mu) para no dividir por cero.
        mu = mean(baseline) if baseline else 0.0
        return current / math.sqrt(mu + 1.0)

    def compute(self) -> dict[str, int]:
        counts = self.store.get_daily_topic_counts(lookback_days=self.cfg.lookback_days)
        if not counts:
            logger.info("Sin datos enriquecidos para calcular tendencias.")
            return {"trends": 0, "bursts": 0}

        series: dict[tuple[str, str], dict[date, int]] = defaultdict(dict)
        all_days: set[date] = set()
        for row in counts:
            series[(row["zona"], row["tema"])][row["d"]] = row["c"]
            all_days.add(row["d"])

        as_of = max(all_days)
        records: list[dict[str, Any]] = []
        bursts = 0

        for (zona, tema), by_day in series.items():
            current = by_day.get(as_of, 0)
            baseline = [c for d, c in by_day.items() if d < as_of]
            mu = mean(baseline) if baseline else 0.0
            z = self._zscore(current, baseline)
            ratio = current / max(mu, 0.5)
            # Sin linea base no hay tendencia medible (evita falsos positivos).
            is_burst = (
                bool(baseline)
                and current >= self.cfg.min_volume
                and (z >= self.cfg.z_threshold or ratio >= self.cfg.ratio_threshold)
            )
            if is_burst:
                bursts += 1
            records.append(
                {
                    "zona": zona,
                    "tema": tema,
                    "ventana": as_of,
                    "volumen": current,
                    "esperado": round(mu, 3),
                    "zscore": round(z, 3),
                    "ratio": round(ratio, 3),
                    "es_burst": is_burst,
                }
            )

        stored = self.store.upsert_trends(records)
        logger.info("Tendencias calculadas: %d (bursts=%d, ventana=%s)", stored, bursts, as_of)
        return {"trends": stored, "bursts": bursts}
