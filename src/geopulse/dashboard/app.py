"""Dashboard de GeoPulse (Streamlit).

Lectura en read-only con conexiones de vida corta (no bloquea al colector).
La gestion (etiquetas, ubicaciones, temas) y el encolado requieren ejecucion local.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from geopulse.config import get_config  # noqa: E402
from geopulse.orchestrator.queue import JobQueue  # noqa: E402
from geopulse.storage.duckdb_store import DuckDBStore  # noqa: E402
from geopulse.utils.text import slugify  # noqa: E402

st.set_page_config(page_title="GeoPulse - Bahoruco", layout="wide")

_QUERIES = {
    "metrics": {
        "Fuentes": "SELECT COUNT(*) AS v FROM sources",
        "Posts": "SELECT COUNT(*) AS v FROM posts",
        "Comentarios": "SELECT COUNT(*) AS v FROM comments",
        "Con geo": "SELECT COUNT(*) AS v FROM posts_geo WHERE municipality IS NOT NULL",
        "Enriquecidos": "SELECT COUNT(*) AS v FROM posts_enriched",
        "Temas": "SELECT COUNT(*) AS v FROM topics",
        "Ubicaciones": "SELECT COUNT(*) AS v FROM locations",
        "Alertas": "SELECT COUNT(*) AS v FROM trends WHERE es_burst",
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
    "ubicaciones": "SELECT location_id, name, level, municipio, lat, lon, active FROM locations ORDER BY level, name",
    "temas": "SELECT topic_id, label, active, selected FROM topics ORDER BY label",
    "comentarios": "SELECT platform, text, url, collected_at FROM comments ORDER BY collected_at DESC LIMIT 50",
    "heat": """
        SELECT g.municipality AS municipio, u.topic AS tema, COUNT(*) AS menciones
        FROM posts_enriched e
        JOIN posts_geo g ON g.raw_id = e.raw_id
        CROSS JOIN UNNEST(e.topics) AS u(topic)
        WHERE g.municipality IS NOT NULL
        GROUP BY 1, 2
    """,
}


@st.cache_data(ttl=15, show_spinner=False)
def load_data() -> dict:
    config = get_config()
    store = DuckDBStore(config.db_path, read_only=True)
    try:
        data: dict = {
            "metrics": {label: store.query(sql)[0][0] for label, sql in _QUERIES["metrics"].items()},
        }
        for name in (
            "por_municipio", "por_plataforma", "top_fuentes", "fuentes", "runs", "top_temas",
            "sentimiento", "preferencias", "tendencias", "alertas", "etiquetas", "descubiertas",
            "ubicaciones", "temas", "comentarios", "heat",
        ):
            data[name] = store.query_df(_QUERIES[name])
        return data
    finally:
        store.close()


def _write(action) -> tuple[bool, str]:
    """Ejecuta una operacion de escritura corta (solo local)."""
    try:
        config = get_config()
        store = DuckDBStore(config.db_path)
        try:
            store.init_schema()
            action(store)
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


def render_management(data: dict) -> None:
    tab_tags, tab_loc, tab_top = st.tabs(["Etiquetas", "Ubicaciones", "Temas"])

    with tab_tags:
        with st.form("add_tag", clear_on_submit=True):
            col_t, col_b = st.columns([4, 1])
            term = col_t.text_input("Nueva etiqueta / tema a buscar", placeholder="ej. feria de la uva, #bahoruco")
            if col_b.form_submit_button("Agregar") and term.strip():
                ok, err = _write(lambda s: s.upsert_keyword(term.strip(), "manual"))
                st.success(f"Agregado: {term.strip()}") if ok else st.error(err)
                st.rerun()
        st.dataframe(data["etiquetas"], use_container_width=True)
        if not data["etiquetas"].empty:
            sel = st.multiselect("Eliminar etiquetas", data["etiquetas"]["term"].tolist())
            if st.button("Eliminar etiquetas") and sel:
                _write(lambda s: [s.remove_keyword(t) for t in sel])
                st.rerun()
        c1, c2 = st.columns(2)
        if c1.button("Buscar contenido de las etiquetas (encolar)"):
            ok, info = enqueue_job("search_keywords", {"amount": 10, "limit": 25})
            st.success(f"Encolado #{info}") if ok else st.error(info)
        if c2.button("Descubrir perfiles por municipios/distritos (encolar)"):
            ok, info = enqueue_job("discover_places", {"platforms": ["instagram", "facebook"], "per_place": 8, "reset": True})
            st.success(f"Encolado #{info}") if ok else st.error(info)

    with tab_loc:
        st.caption("Agrega ubicaciones manuales (municipios, distritos, landmarks). Se usan para geolocalizar y en el mapa.")
        with st.form("add_loc", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Nombre")
            level = c2.selectbox("Nivel", ["municipio", "distrito", "landmark", "custom"])
            municipio = c3.text_input("Municipio (padre)")
            c4, c5, c6 = st.columns(3)
            lat = c4.number_input("Latitud", value=18.49, format="%.5f")
            lon = c5.number_input("Longitud", value=-71.42, format="%.5f")
            aliases = c6.text_input("Alias (coma)")
            if st.form_submit_button("Agregar ubicacion") and name.strip():
                ok, err = _write(
                    lambda s: s.upsert_location(
                        {
                            "name": name.strip(),
                            "level": level,
                            "municipio": municipio.strip() or None,
                            "lat": lat,
                            "lon": lon,
                            "aliases": [a.strip() for a in aliases.split(",")] if aliases else [],
                        }
                    )
                )
                st.success(f"Agregada: {name}") if ok else st.error(err)
                st.rerun()
        st.dataframe(data["ubicaciones"], use_container_width=True)
        if not data["ubicaciones"].empty:
            ids = data["ubicaciones"]["location_id"].tolist()
            sel_loc = st.multiselect("Activar/desactivar o eliminar (por id)", ids)
            cc1, cc2 = st.columns(2)
            if cc1.button("Eliminar ubicaciones") and sel_loc:
                _write(lambda s: [s.remove_location(i) for i in sel_loc])
                st.rerun()
            if cc2.button("Re-geolocalizar posts (regeo)") :
                ok, info = enqueue_job("regeo")
                st.success(f"Encolado #{info}") if ok else st.error(info)
            st.caption("Sugerencia: tras cambiar ubicaciones ejecuta `regeo` para reaplicar la geolocalizacion.")

    with tab_top:
        st.caption("Temas de conversacion. Los 'seleccionados' se usan en el mapa de calor.")
        with st.form("add_topic", clear_on_submit=True):
            c1, c2 = st.columns(2)
            label = c1.text_input("Nuevo tema")
            keywords = c2.text_input("Palabras clave (coma)")
            if st.form_submit_button("Agregar tema") and label.strip():
                ok, err = _write(
                    lambda s: s.upsert_topic(
                        {
                            "label": label.strip(),
                            "keywords": [k.strip() for k in keywords.split(",")] if keywords else [],
                        }
                    )
                )
                st.success(f"Agregado: {label}") if ok else st.error(err)
                st.rerun()
        st.dataframe(data["temas"], use_container_width=True)
        if not data["temas"].empty:
            sel_top = st.multiselect(
                "Seleccionar/deseleccionar temas", data["temas"]["label"].tolist()
            )
            cc1, cc2 = st.columns(2)
            if cc1.button("Marcar seleccionados") and sel_top:
                ids = data["temas"][data["temas"]["label"].isin(sel_top)]["topic_id"].tolist()
                _write(lambda s: [s.set_topic_selected(i, True) for i in ids])
                st.rerun()
            if cc2.button("Quitar seleccion") and sel_top:
                ids = data["temas"][data["temas"]["label"].isin(sel_top)]["topic_id"].tolist()
                _write(lambda s: [s.set_topic_selected(i, False) for i in ids])
                st.rerun()

    st.divider()
    st.markdown("**Fuentes descubiertas automaticamente**")
    st.dataframe(data["descubiertas"], use_container_width=True)


def render_comments(data: dict) -> None:
    st.subheader("Comentarios")
    col1, col2 = st.columns([3, 1])
    term = col1.text_input("Buscar en comentarios", placeholder="ej. uva, apagon, lago")
    col2.metric("Comentarios almacenados", data["metrics"]["Comentarios"])

    if term.strip():
        with_conn = None
        try:
            config = get_config()
            with_conn = DuckDBStore(config.db_path, read_only=True)
            results = with_conn.query_df(
                "SELECT platform, text, url FROM comments WHERE text ILIKE ? ORDER BY collected_at DESC LIMIT 100",
                [f"%{term}%"],
            )
        finally:
            if with_conn:
                with_conn.close()
        st.markdown(f"Resultados para **{term}**: {len(results)}")
        st.dataframe(results, use_container_width=True)
    else:
        st.dataframe(data["comentarios"], use_container_width=True)

    st.caption("Para recolectar: `geopulse comments <url_instagram_o_facebook>`.")


def render_heatmap(data: dict) -> None:
    st.subheader("Mapa de calor de tendencias/temas")
    heat = data["heat"]
    if heat.empty:
        st.info("Sin datos enriquecidos. Ejecuta `enrich`.")
        return

    coords: dict[str, tuple[float, float]] = {}
    for _, row in data["ubicaciones"].iterrows():
        if row["level"] == "municipio" and pd.notna(row["lat"]) and pd.notna(row["lon"]):
            coords[row["municipio"] or row["name"]] = (float(row["lat"]), float(row["lon"]))

    all_topics = sorted(heat["tema"].unique().tolist())
    selected_default = data["temas"][data["temas"]["selected"] == True]["topic_id"].tolist() if not data["temas"].empty else []
    mode = st.radio("Fuente de temas", ["Seleccionados", "Tendencias (top)", "Manual"], horizontal=True)

    if mode == "Seleccionados":
        topics = [t for t in selected_default if t in all_topics] or all_topics[:8]
    elif mode == "Tendencias (top)":
        topics = heat.groupby("tema")["menciones"].sum().sort_values(ascending=False).head(8).index.tolist()
    else:
        topics = st.multiselect("Temas", all_topics, default=all_topics[:6])

    filtered = heat[heat["tema"].isin(topics)]
    if filtered.empty:
        st.info("Sin datos para los temas elegidos.")
        return

    agg = filtered.groupby("municipio")["menciones"].sum().reset_index()
    agg["lat"] = agg["municipio"].map(lambda m: coords.get(m, (None, None))[0])
    agg["lon"] = agg["municipio"].map(lambda m: coords.get(m, (None, None))[1])
    agg = agg.dropna(subset=["lat", "lon"])

    if agg.empty:
        st.warning("Los municipios con datos no tienen coordenadas. Agrega ubicaciones con lat/lon.")
        st.bar_chart(filtered.groupby("tema")["menciones"].sum())
        return

    fig = px.scatter_mapbox(
        agg,
        lat="lat",
        lon="lon",
        size="menciones",
        color="menciones",
        hover_name="municipio",
        color_continuous_scale="Turbo",
        size_max=45,
        zoom=9,
        height=520,
        mapbox_style="open-street-map",
    )
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Temas: {', '.join(topics)}")
    st.dataframe(agg.sort_values("menciones", ascending=False), use_container_width=True)


def main() -> None:
    st.title("GeoPulse - Bahoruco (DO-03)")
    st.caption("Observatorio de tendencias, comentarios y preferencias por municipio")

    try:
        data = load_data()
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudo abrir la base de datos: {exc}")
        st.info("Ejecuta `python -m geopulse.cli init-db` para crearla.")
        return

    metric_items = list(data["metrics"].items())
    for start in range(0, len(metric_items), 4):
        cols = st.columns(4)
        for col, (label, value) in zip(cols, metric_items[start : start + 4]):
            col.metric(label, value)

    if st.button("Refrescar datos"):
        load_data.clear()
        st.rerun()

    st.divider()
    render_management(data)

    st.divider()
    render_comments(data)

    st.divider()
    render_heatmap(data)

    st.divider()
    st.subheader("Volumen por municipio")
    if data["por_municipio"].empty:
        st.info("Aun no hay datos geolocalizados.")
    else:
        st.bar_chart(data["por_municipio"].set_index("municipio"))

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Posts por plataforma")
        st.dataframe(data["por_plataforma"], use_container_width=True)
    with col_b:
        st.subheader("Top fuentes")
        st.dataframe(data["top_fuentes"], use_container_width=True)

    st.divider()
    st.subheader("Temas y sentimiento")
    col_c, col_d = st.columns(2)
    with col_c:
        if not data["top_temas"].empty:
            st.bar_chart(data["top_temas"].set_index("tema"))
    with col_d:
        st.dataframe(data["sentimiento"], use_container_width=True)

    st.subheader("Tendencias y alertas")
    col_e, col_f = st.columns(2)
    with col_e:
        st.dataframe(data["alertas"], use_container_width=True)
    with col_f:
        st.dataframe(data["tendencias"], use_container_width=True)

    st.divider()
    st.subheader("Fuentes registradas")
    st.dataframe(data["fuentes"], use_container_width=True)
    st.subheader("Ultimas ejecuciones")
    st.dataframe(data["runs"], use_container_width=True)


if __name__ == "__main__":
    main()
