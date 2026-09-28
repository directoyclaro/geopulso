"""Capa de acceso a DuckDB: esquema, upserts y consultas."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_source_id(platform: str, handle: str | None, url: str | None, name: str | None = None) -> str:
    key = f"{platform}|{handle or ''}|{url or ''}|{name or ''}".lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


class DuckDBStore:
    def __init__(self, db_path: Path | str, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.read_only = read_only
        if not read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path), read_only=read_only)

    # --- Ciclo de vida ---
    def init_schema(self) -> None:
        if self.read_only:
            return
        self.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DuckDBStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- Sources ---
    def upsert_source(self, source: dict[str, Any]) -> str:
        source_id = source.get("source_id") or make_source_id(
            source.get("platform", ""), source.get("handle"), source.get("url"), source.get("name")
        )
        self.conn.execute(
            """
            INSERT INTO sources (source_id, name, platform, handle, url, type,
                                 municipio, region, verified, status, score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_id) DO UPDATE SET
                name = excluded.name,
                municipio = COALESCE(excluded.municipio, sources.municipio),
                region = COALESCE(excluded.region, sources.region),
                verified = sources.verified OR excluded.verified,
                status = excluded.status
            """,
            [
                source_id,
                source.get("name"),
                source.get("platform"),
                source.get("handle"),
                source.get("url"),
                source.get("type"),
                source.get("municipio"),
                source.get("region"),
                bool(source.get("verified", False)),
                source.get("status", "activa"),
                float(source.get("score", 0.0)),
            ],
        )
        return source_id

    def upsert_sources(self, sources: Iterable[dict[str, Any]]) -> int:
        count = 0
        for src in sources:
            self.upsert_source(src)
            count += 1
        return count

    def get_sources(self, platform: str | None = None, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM sources WHERE 1=1"
        params: list[Any] = []
        if platform:
            query += " AND platform = ?"
            params.append(platform)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY score DESC, name"
        cols = [c[0] for c in self.conn.execute(query, params).description]
        return [dict(zip(cols, row)) for row in self.conn.fetchall()]

    # --- Posts ---
    def insert_posts(self, posts: Iterable[dict[str, Any]]) -> int:
        rows = [self._post_row(p) for p in posts]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO posts (raw_id, source_id, platform, collected_at, posted_at,
                               text, language, author_hash, hashtags, keywords,
                               location_text, url, simhash, raw_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (raw_id) DO NOTHING
            """,
            rows,
        )
        return len(rows)

    @staticmethod
    def _post_row(p: dict[str, Any]) -> list[Any]:
        return [
            p["raw_id"],
            p.get("source_id"),
            p.get("platform"),
            p.get("collected_at", utcnow()),
            p.get("posted_at"),
            p.get("text"),
            p.get("language"),
            p.get("author_hash"),
            p.get("hashtags", []),
            p.get("keywords", []),
            p.get("location_text"),
            p.get("url"),
            p.get("simhash"),
            p.get("raw_path"),
        ]

    def insert_posts_geo(self, records: Iterable[dict[str, Any]]) -> int:
        rows = [
            [
                r["raw_id"],
                r.get("municipality"),
                r.get("distrito"),
                r.get("province"),
                r.get("country", "DO"),
                float(r.get("confidence", 0.0)),
                r.get("method"),
                utcnow(),
            ]
            for r in records
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO posts_geo (raw_id, municipality, distrito, province, country,
                                   confidence, method, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (raw_id) DO UPDATE SET
                municipality = excluded.municipality,
                distrito = excluded.distrito,
                confidence = excluded.confidence,
                method = excluded.method,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        return len(rows)

    # --- Enriquecimiento ---
    def get_pending_enrichment(self, limit: int = 500) -> list[dict]:
        cols = [c[0] for c in self.conn.execute(
            """
            SELECT p.raw_id, p.text, p.hashtags, p.keywords, p.language
            FROM posts p
            LEFT JOIN posts_enriched e ON e.raw_id = p.raw_id
            WHERE e.raw_id IS NULL
            ORDER BY p.collected_at DESC
            LIMIT ?
            """,
            [limit],
        ).description]
        return [dict(zip(cols, row)) for row in self.conn.fetchall()]

    def insert_enriched(self, records: Iterable[dict[str, Any]]) -> int:
        rows = [
            [
                r["raw_id"],
                r.get("topics", []),
                r.get("sentiment"),
                r.get("intention"),
                r.get("summary"),
                r.get("method"),
                r.get("model"),
                utcnow(),
            ]
            for r in records
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO posts_enriched (raw_id, topics, sentiment, intention, summary, method, model, enriched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (raw_id) DO UPDATE SET
                topics = excluded.topics,
                sentiment = excluded.sentiment,
                intention = excluded.intention,
                summary = excluded.summary,
                method = excluded.method,
                model = excluded.model,
                enriched_at = excluded.enriched_at
            """,
            rows,
        )
        return len(rows)

    def get_enriched(self, limit: int = 1000) -> list[dict]:
        cols = [c[0] for c in self.conn.execute(
            "SELECT raw_id, topics, sentiment, intention, summary, method, model FROM posts_enriched LIMIT ?",
            [limit],
        ).description]
        return [dict(zip(cols, row)) for row in self.conn.fetchall()]

    # --- Preferencias (gustos por zona) ---
    def refresh_preferences(self, weeks: int = 8) -> int:
        """Recalcula la tabla de preferencias por zona a partir del enriquecido."""
        self.conn.execute("DELETE FROM preferences")
        self.conn.execute(
            """
            INSERT INTO preferences (zona, categoria, peso, ventana_start, ventana_end, updated_at)
            WITH base AS (
                SELECT
                    COALESCE(g.municipality, 'Desconocido') AS zona,
                    u.topic AS categoria,
                    e.enriched_at AS ts
                FROM posts_enriched e
                LEFT JOIN posts_geo g ON g.raw_id = e.raw_id
                CROSS JOIN UNNEST(e.topics) AS u(topic)
                WHERE e.enriched_at >= (now() - INTERVAL (?) WEEK)
            )
            SELECT zona, categoria, COUNT(*) AS peso,
                   DATE_TRUNC('week', MIN(ts))::DATE AS ventana_start,
                   DATE_TRUNC('week', MAX(ts))::DATE AS ventana_end,
                   now()
            FROM base
            GROUP BY zona, categoria
            HAVING COUNT(*) > 0
            """,
            [weeks],
        )
        return int(self.conn.execute("SELECT COUNT(*) FROM preferences").fetchone()[0])

    def delete_discovered_sources(self, platforms: Iterable[str] | None = None) -> int:
        """Borra fuentes descubiertas (para re-descubrir con reglas mejoradas)."""
        if platforms:
            placeholders = ", ".join("?" for _ in platforms)
            before = self.conn.execute(
                f"SELECT COUNT(*) FROM sources WHERE type='descubierta' AND platform IN ({placeholders})",
                list(platforms),
            ).fetchone()[0]
            self.conn.execute(
                f"DELETE FROM sources WHERE type='descubierta' AND platform IN ({placeholders})",
                list(platforms),
            )
        else:
            before = self.conn.execute("SELECT COUNT(*) FROM sources WHERE type='descubierta'").fetchone()[0]
            self.conn.execute("DELETE FROM sources WHERE type='descubierta'")
        return int(before)

    # --- Keywords ---
    def upsert_keyword(self, term: str, source: str = "manual", weight: float = 1.0) -> str:
        term = (term or "").strip()
        if not term:
            raise ValueError("El termino no puede estar vacio")
        self.conn.execute(
            """
            INSERT INTO keywords (term, source, weight, active)
            VALUES (?, ?, ?, TRUE)
            ON CONFLICT (term) DO UPDATE SET
                source = excluded.source,
                weight = excluded.weight,
                active = TRUE
            """,
            [term, source, float(weight)],
        )
        return term

    def insert_keywords(self, terms: Iterable[dict[str, Any]]) -> int:
        count = 0
        for item in terms:
            self.upsert_keyword(
                item["term"] if isinstance(item, dict) else str(item),
                item.get("source", "manual") if isinstance(item, dict) else "manual",
                item.get("weight", 1.0) if isinstance(item, dict) else 1.0,
            )
            count += 1
        return count

    def get_keywords(self, active_only: bool = True) -> list[dict]:
        query = "SELECT term, source, weight, active FROM keywords"
        if active_only:
            query += " WHERE active = TRUE"
        query += " ORDER BY weight DESC, term"
        cols = [c[0] for c in self.conn.execute(query).description]
        return [dict(zip(cols, row)) for row in self.conn.fetchall()]

    def remove_keyword(self, term: str) -> bool:
        term = term.strip()
        existed = self.conn.execute("SELECT COUNT(*) FROM keywords WHERE term = ?", [term]).fetchone()[0] > 0
        if existed:
            self.conn.execute("DELETE FROM keywords WHERE term = ?", [term])
        return existed

    # --- Tendencias ---
    def get_daily_topic_counts(self, lookback_days: int = 30) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT COALESCE(g.municipality, 'Desconocido') AS zona,
                   u.topic AS tema,
                   DATE_TRUNC('day', e.enriched_at)::DATE AS d,
                   COUNT(*) AS c
            FROM posts_enriched e
            LEFT JOIN posts_geo g ON g.raw_id = e.raw_id
            CROSS JOIN UNNEST(e.topics) AS u(topic)
            WHERE e.enriched_at >= (now() - INTERVAL (?) DAY)
            GROUP BY 1, 2, 3
            """,
            [lookback_days],
        ).fetchall()
        return [{"zona": r[0], "tema": r[1], "d": r[2], "c": r[3]} for r in rows]

    def upsert_trends(self, records: Iterable[dict[str, Any]]) -> int:
        rows = [
            [
                r["zona"],
                r["tema"],
                r["ventana"],
                int(r["volumen"]),
                float(r["esperado"]),
                float(r["zscore"]),
                float(r["ratio"]),
                bool(r["es_burst"]),
                utcnow(),
            ]
            for r in records
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO trends (zona, tema, ventana, volumen, esperado, zscore, ratio, es_burst, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (zona, tema, ventana) DO UPDATE SET
                volumen = excluded.volumen,
                esperado = excluded.esperado,
                zscore = excluded.zscore,
                ratio = excluded.ratio,
                es_burst = excluded.es_burst,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        return len(rows)

    def get_trends(self, limit: int = 50) -> list[dict]:
        cols = [c[0] for c in self.conn.execute(
            """
            SELECT zona, tema, ventana, volumen, esperado, zscore, ratio, es_burst
            FROM trends ORDER BY es_burst DESC, zscore DESC LIMIT ?
            """,
            [limit],
        ).description]
        return [dict(zip(cols, row)) for row in self.conn.fetchall()]

    def get_alerts(self, limit: int = 50) -> list[dict]:
        cols = [c[0] for c in self.conn.execute(
            """
            SELECT zona, tema, ventana, volumen, esperado, zscore, ratio
            FROM trends WHERE es_burst ORDER BY zscore DESC, volumen DESC LIMIT ?
            """,
            [limit],
        ).description]
        return [dict(zip(cols, row)) for row in self.conn.fetchall()]

    # --- Runs ---
    def record_run(
        self,
        run_id: str,
        job_type: str,
        platform: str | None = None,
        status: str = "ok",
        items: int = 0,
        error: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO runs (run_id, job_type, platform, started_at, finished_at, status, items, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id) DO NOTHING
            """,
            [run_id, job_type, platform, started_at or utcnow(), utcnow(), status, items, error],
        )

    # --- Consultas para el dashboard ---
    def query(self, sql: str, params: list[Any] | None = None) -> list[tuple]:
        return self.conn.execute(sql, params or []).fetchall()

    def query_df(self, sql: str, params: list[Any] | None = None):
        return self.conn.execute(sql, params or []).df()
