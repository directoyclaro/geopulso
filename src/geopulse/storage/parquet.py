"""Persistencia en disco: bronze (JSONL crudo) y silver/gold (Parquet)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import polars as pl


def _today(value: str | date | None = None) -> str:
    if value is None:
        return date.today().isoformat()
    return value if isinstance(value, str) else value.isoformat()


def write_bronze(
    base_dir: Path,
    platform: str,
    records: Iterable[dict[str, Any]],
    run_date: str | date | None = None,
) -> Path | None:
    """Guarda el crudo (bronze) como JSON Lines particionado por plataforma/fecha."""
    rows = list(records)
    if not rows:
        return None
    day = _today(run_date)
    out_dir = Path(base_dir) / platform / day
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{platform}_{datetime.now().strftime('%H%M%S')}.jsonl"
    with out_file.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return out_file


def write_parquet(records: Iterable[dict[str, Any]], out_path: Path) -> Path | None:
    """Escribe registros a Parquet (silver/gold)."""
    rows = list(records)
    if not rows:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(out_path)
    return out_path


def read_parquet(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path)
