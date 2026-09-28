"""Dashboard de GeoPulse (Streamlit).

Lectura en modo read-only con conexiones de vida corta (no bloquea al colector).
La gestion de etiquetas y el encolado de trabajos requieren ejecucion local.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from geopulse.config import get_config  # noqa: E402
from geopulse.orchestrator.queue import JobQueue  # noqa: E402
from geopulse.storage.duckdb_store import DuckDBStore  # noqa: E402

st.set_page_config(page_title="GeoPulse - Bahoruco", layout="wide")

_QUERIES = {
    "metrics": {
        "Fuentes": "SELECT COUNT(*) AS v FROM sources",
        "Posts": "SELECT COUNT(*) AS v FROM posts",
        "Con geo": "SELECT COUNT(*) AS v FROM posts_geo WHERE municipality IS NOT NULL",
        "Enriquecidos": "SELECT COUNT(*) AS v FROM posts_enriched",
    },
    "por_municipio": """
        SELECT g.municipality AS municipio, COUNT(*) AS posts
        FROM posts_geo g WHERE g.municipality IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC
    """,
    "por_plataforma": "SELECT platform, COUNT(*) AS posts FROM posts GROUP BY 1 ORDER BY 2 DESC",
    "top_fuentes": """
        SELECT s.name, s.platform, s.municipio, COUNT(p.raw_id) AS posts
        FROM sources s LEFT JOIN posts p ON p.source_id = s.source_id
        GROUP BY 1,2,3 ORDER BY posts DESC LIMIT 15
    """,
    "fuentes": "SELECT name, platform, municipio, type, verified, status, score FROM sources ORDER BY score DESC",
    "runs": "SELECT job_type, platform, status, items, started_at FROM runs ORDER BY started_at DESC LIMIT 20",
    "top_temas": """
        SELECT topic AS tema, COUNT(*) AS menciones
        FROM (SELECT unnest(topics) AS topic FROM posts_enriched)
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """,
    "sentimiento": "SELECT sentiment, COUNT(*) AS posts FROM posts_enriched GROUP BY 1 ORDER BY 2 DESC",
    "preferencias": "SELECT zona, categoria, peso FROM preferences ORDER BY peso DESC LIMIT 40",
    "tendencias": """
        SELECT zona, tema, volumen, esperado, zscore, ratio, es_burst
        FROM trends ORDER BY es_burst DESC, zscore DESC LIMIT 30
    """,
    "alertas": """
        SELECT zona, tema, volumen, zscore, ratio
        FROM trends WHERE es_burst ORDER BY zscore DESC LIMIT 20
    """,
    "etiquetas": "SELECT term, source, weight, active FROM keywords ORDER BY source, term",
    "descubiertas": """
        SELECT name, platform, handle, municipio, status, score
        FROM sources WHERE type = 'descubierta' ORDER BY score DESC LIMIT 40
    """,
}


@st.cache_data(ttl=15, show_spinner=False)
def load_data() -> dict:
    """Carga todos los datos en una unica conexion read-only de vida corta."""
    config = get_config()
    store = DuckDBStore(config.db_path, read_only=True)
    try:
        data: dict = {
            "metrics": {label: store.query(sql)[0][0] for label, sql in _QUERIES["metrics"].items()},
        }
        for name in (
            "por_municipio",
            "por_plataforma",
            "top_fuentes",
            "fuentes",
            "runs",
            "top_temas",
            "sentimiento",
            "preferencias",
            "tendencias",
            "alertas",
            "etiquetas",
            "descubiertas",
        ):
            data[name] = store.query_df(_QUERIES[name])
        return data
    finally:
        store.close()


def add_keyword(term: str, source: str = "manual") -> tuple[bool, str]:
    try:
        config = get_config()
        store = DuckDBStore(config.db_path)
        try:
            store.init_schema()
            store.upsert_keyword(term, source)
        finally:
            store.close()
        load_data.clear()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def remove_keyword(term: str) -> tuple[bool, str]:
    try:
        config = get_config()
        store = DuckDBStore(config.db_path)
        try:
            store.remove_keyword(term)
        finally:
            store.close()
        load_data.clear()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def enqueue_job(job_type: str, payload: dict | None = None) -> tuple[bool, str]:
    try:
        queue = JobQueue(get_config().queue_path)
        try:
            job_id = queue.enqueue(job_type, payload or {})
        finally:
            queue.close()
        return True, str(job_id)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def render_tag_management(data: dict) -> None:
    st.divider()
    st.subheader("Fuentes y etiquetas a scrapear")
    st.caption(
        "Agrega temas o etiquetas (hashtags) de forma manual. Para descubrir perfiles por "
        "municipio/distrito o buscar por las etiquetas, encola los trabajos (requieren el "
        "worker local: `geopulse worker`)."
    )

    with st.form("add_tag", clear_on_submit=True):
        col_t, col_b = st.columns([4, 1])
        term = col_t.text_input("Nuevo tema o etiqueta", placeholder="ej. feria de la uva, #bahoruco")
        submitted = col_b.form_submit_button("Agregar")
        if submitted and term.strip():
            ok, err = add_keyword(term.strip())
            if ok:
                st.success(f"Agregado: {term.strip()}")
                st.rerun()
            else:
                st.error(f"No se pudo agregar (¿colector en ejecucion?): {err}")

    st.dataframe(data["etiquetas"], use_container_width=True)

    if not data["etiquetas"].empty:
        selected = st.multiselect("Eliminar etiquetas", data["etiquetas"]["term"].tolist())
        if st.button("Eliminar seleccionadas") and selected:
            for term in selected:
                remove_keyword(term)
            st.rerun()

    col1, col2 = st.columns(2)
    if col1.button("Buscar contenido de las etiquetas (encolar)"):
        ok, info = enqueue_job("search_keywords", {"amount": 10, "limit": 25})
        st.success(f"Trabajo encolado #{info}") if ok else st.error(f"Error: {info}")
    if col2.button("Descubrir perfiles por municipios/distritos (encolar)"):
        ok, info = enqueue_job(
            "discover_places",
            {"platforms": ["instagram", "facebook"], "per_place": 8, "reset": True},
        )
        st.success(f"Trabajo encolado #{info}") if ok else st.error(f"Error: {info}")

    st.markdown("**Fuentes descubiertas automaticamente**")
    st.dataframe(data["descubiertas"], use_container_width=True)


def main() -> None:
    st.title("GeoPulse - Bahoruco (DO-03)")
    st.caption("Observatorio de tendencias y preferencias por municipio")

    try:
        data = load_data()
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudo abrir la base de datos: {exc}")
        st.info("Ejecuta `python -m geopulse.cli init-db` para crearla.")
        return

    cols = st.columns(4)
    for col, (label, value) in zip(cols, data["metrics"].items()):
        col.metric(label, value)

    if st.button("Refrescar datos"):
        load_data.clear()
        st.rerun()

    render_tag_management(data)

    st.divider()
    st.subheader("Volumen por municipio")
    if data["por_municipio"].empty:
        st.info("Aun no hay datos geolocalizados. Ejecuta la recoleccion (`collect`).")
    else:
        st.bar_chart(data["por_municipio"].set_index("municipio"))

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Posts por plataforma")
        if data["por_plataforma"].empty:
            st.info("Sin posts.")
        else:
            st.dataframe(data["por_plataforma"], use_container_width=True)
    with col_b:
        st.subheader("Top fuentes")
        st.dataframe(data["top_fuentes"], use_container_width=True)

    st.divider()
    st.subheader("Temas mas mencionados (enriquecido)")
    col_c, col_d = st.columns(2)
    with col_c:
        if data["top_temas"].empty:
            st.info("Sin datos enriquecidos. Ejecuta `enrich`.")
        else:
            st.bar_chart(data["top_temas"].set_index("tema"))
    with col_d:
        st.subheader("Sentimiento")
        if data["sentimiento"].empty:
            st.info("Sin datos.")
        else:
            st.dataframe(data["sentimiento"], use_container_width=True)

    st.subheader("Gustos/preferencias por zona")
    if data["preferencias"].empty:
        st.info("Sin preferencias calculadas.")
    else:
        st.dataframe(data["preferencias"], use_container_width=True)

    st.divider()
    st.subheader("Tendencias y alertas")
    col_e, col_f = st.columns(2)
    with col_e:
        st.markdown("**Alertas (bursts)**")
        if data["alertas"].empty:
            st.info("Sin alertas. Ejecuta `trends`.")
        else:
            st.dataframe(data["alertas"], use_container_width=True)
    with col_f:
        st.markdown("**Tendencia por tema/zona**")
        st.dataframe(data["tendencias"], use_container_width=True)

    st.divider()
    st.subheader("Fuentes registradas")
    st.dataframe(data["fuentes"], use_container_width=True)

    st.subheader("Ultimas ejecuciones")
    st.dataframe(data["runs"], use_container_width=True)


if __name__ == "__main__":
    main()
