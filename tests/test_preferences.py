from geopulse.agents.preferences import PreferenceProfiler
from geopulse.config import Config
from geopulse.storage.duckdb_store import DuckDBStore


def _post(raw_id: str, text: str) -> dict:
    return {
        "raw_id": raw_id,
        "source_id": "s1",
        "platform": "instagram",
        "text": text,
        "language": "es",
        "hashtags": ["neiba"],
        "keywords": ["Neiba"],
        "simhash": 123,
    }


def test_refresh_preferences_groups_by_zone(tmp_path):
    store = DuckDBStore(tmp_path / "test.duckdb")
    store.init_schema()

    store.insert_posts([_post("p1", "Feria de la uva"), _post("p2", "Feria de la uva")])
    store.insert_posts_geo(
        [
            {"raw_id": "p1", "municipality": "Neiba", "province": "Bahoruco", "country": "DO", "confidence": 0.9, "method": "location"},
            {"raw_id": "p2", "municipality": "Neiba", "province": "Bahoruco", "country": "DO", "confidence": 0.9, "method": "location"},
        ]
    )
    store.insert_enriched(
        [
            {"raw_id": "p1", "topics": ["uva"], "sentiment": "positivo", "intention": "informativo", "method": "heuristic"},
            {"raw_id": "p2", "topics": ["uva", "comercio"], "sentiment": "neutro", "intention": "otro", "method": "heuristic"},
        ]
    )

    result = PreferenceProfiler(Config(), store).run(weeks=52)
    assert result["rows"] >= 2

    rows = store.query("SELECT categoria, peso FROM preferences WHERE zona='Neiba' ORDER BY peso DESC")
    mapping = dict(rows)
    assert mapping["uva"] == 2
    assert mapping["comercio"] == 1
    store.close()
