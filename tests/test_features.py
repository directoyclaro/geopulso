from datetime import date

from geopulse.agents.geo import GeoMapper
from geopulse.config import Config
from geopulse.keywords import add_trend_keywords, effective_keywords, suggest_from_trends
from geopulse.locations import municipio_coordinates, seed_locations
from geopulse.storage.duckdb_store import DuckDBStore
from geopulse.topics import seed_topics


def test_effective_keywords_merges_config_and_db(tmp_path):
    store = DuckDBStore(tmp_path / "k.duckdb")
    store.init_schema()
    store.upsert_keyword("term_manual", "manual")
    config = Config()
    terms = effective_keywords(config, store)
    assert "term_manual" in terms
    assert config.keywords[0] in terms
    store.close()


def test_suggest_and_add_trend_keywords(tmp_path):
    store = DuckDBStore(tmp_path / "t.duckdb")
    store.init_schema()
    store.upsert_trends(
        [
            {
                "zona": "Neiba",
                "tema": "lago_enriquillo",
                "ventana": date(2026, 1, 1),
                "volumen": 10,
                "esperado": 1.0,
                "zscore": 5.0,
                "ratio": 5.0,
                "es_burst": True,
            }
        ]
    )
    suggestions = suggest_from_trends(Config(), store, limit=20)
    assert any("Enriquillo" in s for s in suggestions)

    added = add_trend_keywords(Config(), store, limit=20)
    assert added >= 1
    stored = [k["term"] for k in store.get_keywords()]
    assert any("Enriquillo" in t for t in stored)
    store.close()


def test_place_terms_includes_municipios_and_distritos():
    from geopulse.collectors.profile_discovery import place_terms

    terms = place_terms(Config())
    names = {t["term"] for t in terms}
    assert "Neiba" in names
    assert "Neyba" in names
    assert "Uvilla" in names
    assert "Lago Enriquillo" in names

    uvilla = next(t for t in terms if t["term"] == "Uvilla")
    assert uvilla["municipio"] == "Neiba"
    assert uvilla["level"] == "distrito"


def test_locations_seed_and_coordinates(tmp_path):
    store = DuckDBStore(tmp_path / "loc.duckdb")
    store.init_schema()
    seed_locations(Config(), store)
    locs = store.get_locations()
    assert len(locs) > 5
    coords = municipio_coordinates(Config(), store)
    assert "Neiba" in coords

    lid = store.upsert_location({"name": "Barrio Test", "level": "custom", "municipio": "Neiba"})
    assert store.remove_location(lid) is True
    store.close()


def test_topics_seed_and_selection(tmp_path):
    store = DuckDBStore(tmp_path / "top.duckdb")
    store.init_schema()
    seed_topics(Config(), store)
    topics = store.get_topics()
    assert any(t["topic_id"] == "uva" for t in topics)
    store.set_topic_selected("uva", False)
    selected = [t["topic_id"] for t in store.get_topics(selected_only=True)]
    assert "uva" not in selected
    store.close()


def test_comments_insert_and_search(tmp_path):
    store = DuckDBStore(tmp_path / "com.duckdb")
    store.init_schema()
    store.insert_comments(
        [
            {"comment_id": "c1", "platform": "instagram", "text": "Excelente la feria de la uva en Neiba"},
            {"comment_id": "c2", "platform": "facebook", "text": "Otra vez el apagon en Tamayo"},
        ]
    )
    assert len(store.search_comments("uva")) == 1
    assert len(store.search_comments("apagon")) == 1
    assert len(store.get_comments()) == 2
    store.close()


def test_geo_landmark_assigns_municipio():
    geo = GeoMapper(Config())
    post = {"raw_id": "l1", "text": "Visitamos el oasis de Las Marias", "location_text": "", "hashtags": []}
    result = geo.map_post(post)
    assert result["municipality"] == "Neiba"


def test_geo_demonym_assigns_municipio():
    geo = GeoMapper(Config())
    post = {"raw_id": "d1", "text": "Un orgullo neibero para todo el pais", "location_text": "", "hashtags": []}
    result = geo.map_post(post)
    assert result["municipality"] == "Neiba"
