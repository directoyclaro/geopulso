from geopulse.agents.normalizer import NormalizerAgent
from geopulse.config import Config


def test_normalize_spanish_post():
    agent = NormalizerAgent(Config())
    raw = {
        "platform": "instagram",
        "external_id": "abc123",
        "text": "Hoy en Neiba hay feria de la uva en la plaza principal",
        "author": "productor_uva",
    }
    post = agent.normalize(raw)
    assert post is not None
    assert post["platform"] == "instagram"
    assert post["language"] == "es"
    assert post["author_hash"] is not None
    assert "author" not in post


def test_normalize_dedups_near_duplicates():
    agent = NormalizerAgent(Config())
    raw = {"platform": "instagram", "external_id": "1", "text": "Feria de la uva en Neiba este sabado"}
    first = agent.normalize(raw)
    second = agent.normalize({**raw, "external_id": "2"})
    assert first is not None
    assert second is None


def test_normalize_filters_short_text():
    agent = NormalizerAgent(Config())
    assert agent.normalize({"platform": "instagram", "text": "hola"}) is None
