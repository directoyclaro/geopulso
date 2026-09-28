from datetime import datetime, timedelta

from geopulse.agents.trends import TrendDetector
from geopulse.config import Config
from geopulse.storage.duckdb_store import DuckDBStore


def _mk(store, raw_id: str, topic: str, when: datetime) -> None:
    store.insert_posts([{"raw_id": raw_id, "source_id": "s", "platform": "facebook",
                         "text": "t", "language": "es", "hashtags": [], "keywords": [], "simhash": 1}])
    store.insert_posts_geo([{"raw_id": raw_id, "municipality": "Neiba", "province": "Bahoruco",
                             "country": "DO", "confidence": 0.9, "method": "location"}])
    store.insert_enriched([{"raw_id": raw_id, "topics": [topic], "sentiment": "neutro",
                            "intention": "informativo", "method": "llm"}])
    store.conn.execute("UPDATE posts_enriched SET enriched_at = ? WHERE raw_id = ?", [when, raw_id])


def test_burst_detection(tmp_path):
    store = DuckDBStore(tmp_path / "t.duckdb")
    store.init_schema()
    base = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

    # tema 'uva': 1 post/dia en baseline (dias -3..-1) y 5 el dia actual -> burst
    for i in range(3):
        _mk(store, f"uva_b{i}", "uva", base - timedelta(days=3 - i))
    for i in range(5):
        _mk(store, f"uva_c{i}", "uva", base)

    # tema 'agua': 1 post/dia incluido el actual -> sin burst (volumen < min_volume)
    for i in range(4):
        _mk(store, f"agua_{i}", "agua", base - timedelta(days=3 - i))

    # tema 'mango': solo aparece el dia actual -> sin linea base, no es burst
    for i in range(4):
        _mk(store, f"mango_{i}", "mango", base)

    result = TrendDetector(Config(), store).compute()
    assert result["trends"] >= 2
    assert result["bursts"] >= 1

    rows = dict(store.query("SELECT tema, es_burst FROM trends WHERE zona='Neiba'"))
    assert rows["uva"] is True
    assert rows["agua"] is False
    assert rows["mango"] is False

    uva = store.query("SELECT volumen, ratio, zscore FROM trends WHERE zona='Neiba' AND tema='uva'")[0]
    assert uva[0] == 5
    assert uva[1] >= 2.0
    store.close()
