from geopulse.agents.enricher import EnricherAgent
from geopulse.config import Config
from geopulse.llm.schemas import flatten_taxonomy


class FakeLLM:
    def __init__(self, available: bool = False, response: dict | None = None) -> None:
        self._available = available
        self._response = response
        self.model = "fake"

    def is_available(self) -> bool:
        return self._available

    def chat_json(self, messages):
        return self._response


def agent(llm=None) -> EnricherAgent:
    return EnricherAgent(Config(), store=None, llm=llm or FakeLLM())


def test_flatten_taxonomy_has_leaves():
    leaves = flatten_taxonomy(Config().taxonomy)
    assert "uva" in leaves
    assert "beisbol" in leaves
    assert "sin_clasificar" in leaves


def test_heuristic_topics():
    a = agent()
    topics = a._heuristic_topics({"text": "Gran feria de la uva y el vino en Neiba", "hashtags": []})
    assert "uva" in topics


def test_heuristic_sentiment_negative():
    a = agent()
    assert a._heuristic_sentiment("Otra vez el apagon y la falta de agua") == "negativo"


def test_heuristic_intention_queja():
    a = agent()
    assert a._heuristic_intention("No hay agua, denunciamos abandono del acueducto") == "queja"


def test_parse_llm_results_filters_invalid_topics():
    a = agent()
    batch = [{"raw_id": "1"}, {"raw_id": "2"}, {"raw_id": "3"}]
    data = {
        "results": [
            {"id": 0, "topics": ["uva", "inventado"], "sentiment": "positivo", "intention": "informativo"},
            {"id": 1, "topics": ["nope"], "sentiment": "raro", "intention": "otro"},
            {"id": 99, "topics": ["uva"], "sentiment": "neutro", "intention": "otro"},
        ]
    }
    parsed = a._parse_llm_results(data, batch)
    assert parsed[0]["topics"] == ["uva"]
    assert parsed[1]["topics"] == ["sin_clasificar"]
    assert parsed[1]["sentiment"] == "neutro"
    assert 99 not in parsed


def test_enrich_rescues_sin_clasificar_with_heuristic():
    class Store:
        def __init__(self):
            self.records = []

        def get_pending_enrichment(self, limit):
            return [{"raw_id": "r9", "text": "Feria de la uva en Tamayo", "hashtags": [], "keywords": [], "language": "es"}]

        def insert_enriched(self, records):
            self.records = list(records)
            return len(records)

    llm = FakeLLM(
        available=True,
        response={"results": [{"id": 0, "topics": ["sin_clasificar"], "sentiment": "neutro", "intention": "otro"}]},
    )
    store = Store()
    a = EnricherAgent(Config(), store=store, llm=llm)
    result = a.enrich()
    assert result["llm"] == 1
    assert store.records[0]["method"] == "llm+heuristic"
    assert "uva" in store.records[0]["topics"]


def test_enrich_fallback_uses_heuristic():
    class Store:
        def __init__(self):
            self.records = []

        def get_pending_enrichment(self, limit):
            return [{"raw_id": "r1", "text": "Feria de la uva en Tamayo", "hashtags": [], "keywords": [], "language": "es"}]

        def insert_enriched(self, records):
            self.records = list(records)
            return len(records)

    store = Store()
    a = EnricherAgent(Config(), store=store, llm=FakeLLM(available=False))
    result = a.enrich()
    assert result["heuristic"] == 1
    assert store.records[0]["method"] == "heuristic"
    assert "uva" in store.records[0]["topics"]
